import base64
import io
import json
import tempfile
import time
import zipfile
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TransactionTestCase, override_settings
from PIL import Image
from requests import ConnectionError, Response
from rest_framework.test import APIClient

from .models import ProductJob
from .workflow_manager import run_workflow_async


@override_settings(
    OPENROUTER_API_KEY='mock-key', WOOCOMMERCE_URL='https://shop.invalid',
    WOOCOMMERCE_CONSUMER_KEY='mock-consumer', WOOCOMMERCE_CONSUMER_SECRET='mock-secret',
    WORDPRESS_MEDIA_USER='mock-user', WORDPRESS_MEDIA_APP_PASSWORD='mock-password',
    PUBLIC_MEDIA_BASE_URL='', MODEL_TRYON_PROVIDER='openrouter', IMAGE_GENERATION_PROVIDER='openrouter',
)
class CatalogFlowTests(TransactionTestCase):
    def setUp(self):
        # Keep API polling from racing the worker on Django's shared in-memory SQLite database.
        self.enterContext(patch('catalog_pipeline.views.start_workflow', side_effect=lambda job, stage: run_workflow_async(job.pk, stage)))
        media = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(override_settings(MEDIA_ROOT=media))
        user = get_user_model().objects.create_superuser('test-studio', password='test-only-password')
        self.browser = APIClient(enforce_csrf_checks=True)
        self.browser.force_login(user)
        landing = self.browser.get('/pipeline/dashboard/')
        self.assertContains(landing, 'Extract product details')
        self.csrf = landing.cookies['csrftoken'].value
        self.provider_calls = []
        self.tryon_requests = []
        self.fail_tryon_poll_once = False
        self.progress_seen = []
        self.duplicate_side = False
        self.uploads = []
        self.product_posts = []
        self.product_updates = []
        self.remote_lookup_error = ''
        self.shop_lookup = []
        self.fail_image_number = None
        self.send = self.enterContext(patch('requests.sessions.Session.send', side_effect=self.mock_outbound))

    def image_bytes(self, color):
        stream = io.BytesIO()
        Image.new('RGB', (96, 128), color).save(stream, format='JPEG')
        return stream.getvalue()

    def photo(self, filename, color):
        return SimpleUploadedFile(filename, self.image_bytes(color), content_type='image/jpeg')

    def mock_outbound(self, request, **kwargs):
        url = urlsplit(request.url)
        code = 200
        if url.netloc == 'api.fashn.ai':
            self.assertEqual(request.headers['Authorization'], 'Bearer test-fashn-key')
            if url.path == '/v1/run':
                self.assertEqual(request.method, 'POST')
                self.tryon_requests.append(json.loads(request.body))
                data = {'id': f'prediction-{len(self.tryon_requests)}'}
            elif self.fail_tryon_poll_once:
                self.fail_tryon_poll_once = False
                code, data = 503, {'error': 'Temporary test failure'}
            else:
                self.assertEqual(request.method, 'GET')
                color = 'navy' if url.path == '/v1/status/prediction-1' else 'teal'
                data = {'status': 'completed', 'output': [
                    'data:image/jpeg;base64,' + base64.b64encode(self.image_bytes(color)).decode('ascii'),
                ]}
        elif url.netloc == 'openrouter.ai':
            payload = json.loads(request.body)
            if url.path.endswith('/chat/completions') and 'Transcribe the size chart' in payload['messages'][0]['content']:
                self.assertTrue(payload['messages'][1]['content'][0]['image_url']['url'].startswith('data:image/jpeg;base64,'))
                response = Response()
                response.status_code = 200
                response._content = json.dumps({'choices': [{'message': {'content': json.dumps(
                    {'rows': [['Size', 'Waist (in)'], ['28', '?'], ['30', '30.5']]}
                )}}]}).encode()
                return response
            self.provider_calls.append(payload)
            self.progress_seen.append(ProductJob.objects.latest('created_at').workflow_progress)
            if url.path.endswith('/chat/completions'):
                details = {
                    'title': 'Studio Indigo Jeans', 'sku': 'INDIGO-TEST',
                    'jean_type': 'Straight-Leg', 'regular_price': '49.00', 'stock_quantity': 7,
                    'manage_stock': True, 'short_description': 'Dark indigo denim',
                    'description': 'A dark indigo straight-leg cut with a clean silhouette.',
                    'attributes': [
                        {'name': 'Wash', 'options': ['Dark Indigo']},
                        {'name': 'Colours', 'options': ['Dark Indigo']},
                    ],
                }
                data = {'choices': [{'message': {'content': json.dumps(details)}}]}
            else:
                self.assertEqual(url.path, '/api/v1/images')
                if len(self.provider_calls) == self.fail_image_number:
                    code, data = 503, {'error': 'Simulated image failure'}
                else:
                    is_side = 'Strict 90-degree' in payload['prompt'] or 'original side profile photo' in payload['prompt']
                    encoded = base64.b64encode(self.image_bytes('teal' if is_side and not self.duplicate_side else 'navy')).decode('ascii')
                    data = {'data': [{'b64_json': encoded}]}
        elif url.netloc == 'shop.invalid' and url.path.endswith('/wp/v2/users/me'):
            self.assertEqual(request.method, 'GET')
            data = {'capabilities': {'upload_files': True}}
        elif url.netloc == 'shop.invalid' and url.path.endswith('/wp/v2/media'):
            if b'image/png' in request.body:
                self.assertIn(b'filename="straight-leg-', request.body)
            else:
                self.assertIn(b'filename="atelier-indigo-jeans-', request.body)
            self.uploads.append(request.body)
            data = {'id': 100 + len(self.uploads), 'source_url': f'https://shop.invalid/wp-content/uploads/chart-{len(self.uploads)}.png'}
        elif url.netloc == 'shop.invalid' and url.path.endswith('/wc/v3/products/123'):
            if request.method == 'PUT':
                self.product_updates.append(json.loads(request.body))
            else:
                self.assertEqual(request.method, 'GET')
            if self.remote_lookup_error:
                code, data = 404, {'code': self.remote_lookup_error}
            else:
                current = self.product_updates[-1] if self.product_updates else self.product_posts[-1]
                gallery = next((update['images'] for update in reversed(self.product_updates) if 'images' in update),
                               [*self.product_posts[-1]['images'], {'id': 201}])
                meta_data = [{**meta, 'id': index} for index, meta in enumerate(current['meta_data'], 1)]
                data = {'id': 123, 'permalink': 'https://shop.invalid/product/atelier-indigo-jeans/',
                        'meta_data': meta_data, 'images': gallery}
        elif url.netloc in ('graph.facebook.com', 'graph.instagram.com'):
            self.assertEqual(request.method, 'GET')
            self.assertNotIn('access_token', url.query)
            self.assertEqual(request.headers['Authorization'], 'Bearer mock-social-token')
            if url.netloc == 'graph.facebook.com':
                self.assertIn('appsecret_proof=', url.query)
                data = {'id': '100', 'name': 'Studio Page', 'instagram_business_account': {'id': '300', 'username': 'linkedstudio'}}
            else:
                data = {'user_id': '200', 'username': 'studio'}
        elif url.netloc == 'shop.invalid' and url.path.endswith('/wc/v3/products'):
            if request.method == 'POST':
                self.product_posts.append(json.loads(request.body))
                data = {'id': 123, 'permalink': 'https://shop.invalid/product/atelier-indigo-jeans/'}
            else:
                self.shop_lookup.append(url.query)
                data = []
        else:
            self.fail(f'Unexpected outbound request to {request.url}')
        response = Response()
        response.status_code = code
        response.url = request.url
        response._content = json.dumps(data).encode()
        return response

    def post(self, path, data, format='json'):
        return self.browser.post(path, data, format=format, HTTP_X_CSRFTOKEN=self.csrf)

    def wait_for(self, path, target):
        end = time.monotonic() + 8
        while time.monotonic() < end:
            result = self.browser.get(path).json()
            if result['status'] != 'PROCESSING':
                self.assertEqual(result['status'], target, result)
                return result
            time.sleep(.08)
        self.fail(f'Timed out waiting for {target}')

    def intake(self, garment_count=2, *, extra_model_photos=False, bulk_count=0):
        profile = self.post('/pipeline/api/model-profiles/', {
            'name': 'Test catalog model', 'description': 'Keep the short hairstyle.',
            'reference_image': self.photo('front.jpg', 'red'),
            'side_reference_image': self.photo('side.jpg', 'orange'),
        }, 'multipart')
        self.assertEqual(profile.status_code, 201, profile.data)
        profile_id = profile.data['id']
        profile_update = {
            'description': 'Keep the short hairstyle and white sneakers.',
        }
        if extra_model_photos:
            profile_update.update({
                'face_reference_image': self.photo('close-up.jpg', 'green'),
                'three_quarter_reference_image': self.photo('three-quarter.jpg', 'blue'),
                'detail_reference_image': self.photo('detail.jpg', 'purple'),
            })
        if bulk_count:
            profile_update['bulk_reference_images'] = [self.photo(f'angle-{i}.jpg', 'purple') for i in range(bulk_count)]
        patch_result = self.browser.patch(f'/pipeline/api/model-profiles/{profile_id}/', profile_update,
                                          format='multipart', HTTP_X_CSRFTOKEN=self.csrf)
        self.assertEqual(patch_result.status_code, 200, patch_result.data)
        self.assertEqual(self.send.call_count, 0)
        response = self.post('/pipeline/api/jobs/create_job/', {
            'raw_product_text': 'Dark indigo straight leg jeans, INDIGO-TEST, 49.00, 7 in stock',
            'model_profile_id': profile_id,
            'garment_images': [self.photo(f'denim-{index}.jpg', (index * 30, 35, 80)) for index in range(garment_count)],
            'fabric_reference': self.photo('fabric.jpg', 'purple'),
        }, 'multipart')
        self.assertEqual(response.status_code, 201, response.data)
        url = f"/pipeline/api/jobs/{response.data['job_id']}/"
        draft = self.wait_for(url, 'PENDING_DETAILS')
        self.assertEqual(len(self.provider_calls), 1)
        self.assertEqual(self.progress_seen[0]['model'], settings.OPENROUTER_PRODUCT_VISION_MODEL)
        content = self.provider_calls[0]['messages'][1]['content']
        self.assertEqual([part['type'] for part in content], ['text', 'image_url', 'image_url'])
        details = draft['final_data']
        self.assertEqual(details['description'], 'A dark indigo straight-leg cut with a clean silhouette.')
        self.assertEqual(details['meta_title'], 'Studio Indigo Jeans')
        self.assertEqual(details['attributes'], [
            {'name': 'Size', 'options': [], 'visible': True},
            {'name': 'Color', 'options': ['Dark Indigo'], 'visible': True},
        ])
        edit = self.post(url + 'edit_job/', {'reviewer_edits': {'title': 'Atelier Indigo Jeans', 'regular_price': '47.00'}})
        self.assertEqual(edit.status_code, 200, edit.data)
        return url

    def test_product_end_to_end_with_wordpress_media_and_zero_real_requests(self):
        url = self.intake()
        chart = self.post('/pipeline/api/size-charts/', {
            'name': 'Straight leg inches', 'measurements': 'Size,Waist (in),Length (in)\n28,28,30\n30,30,31',
        })
        self.assertEqual(chart.status_code, 201, chart.data)
        with default_storage.open('size_charts/straight-leg-inches.png') as file, Image.open(file) as image:
            self.assertEqual(image.format, 'PNG')
        self.assertEqual(self.post(url + 'edit_job/', {
            'reviewer_edits': {}, 'size_chart_id': chart.data['id'],
        }).status_code, 200)
        premature = self.post(url + 'approve_publish/', {})
        self.assertEqual(premature.status_code, 409)
        self.assertEqual(self.post(url + 'generate/', {}).status_code, 202)
        ready = self.wait_for(url, 'PENDING_REVIEW')
        self.assertEqual(len(ready['reference_photos']['garments']), 2)
        self.assertEqual(len(ready['reference_photos']['model']), 2)
        self.assertTrue(ready['reference_photos']['fabric'])
        self.assertEqual(ready['size_chart'], chart.data['id'])

        front, side, flat_lay = self.provider_calls[1:]
        self.assertEqual([len(call['input_references']) for call in (front, side, flat_lay)], [4, 5, 2])
        self.assertEqual(front['input_references'], side['input_references'][:4])
        self.assertIn('white sneakers', side['prompt'])
        self.assertIn('Strict 90-degree', side['prompt'])
        self.assertIn('only for likeness', side['prompt'])
        self.assertTrue(all('No text' in shot['prompt'] and 'Atelier Indigo Jeans' not in shot['prompt']
                            for shot in (front, side, flat_lay)))
        self.assertEqual([entry['step'] for entry in self.progress_seen], ['extract', 'model-front', 'model-side', 'garment-front-back'])
        self.assertTrue(all(entry['model'] == settings.OPENROUTER_IMAGE_MODEL for entry in self.progress_seen[1:]))
        for suffix, label in enumerate(('model-front', 'garment-front-back', 'model-side'), 1):
            image_url = ready[f'image_{suffix}_url']
            self.assertEqual(Path(image_url).name, f'atelier-indigo-jeans-{label}.jpg')
            path = image_url.removeprefix('/media/')
            self.assertTrue(path.startswith('generated_images/atelier-indigo-jeans-'))
            self.assertEqual(default_storage.url(path), image_url)
            with default_storage.open(path) as file:
                with Image.open(file) as image:
                    self.assertEqual(image.size, (768, 1024))
                    self.assertEqual(image.format, 'JPEG')
        fabric_url = ready['fabric_image_url']
        self.assertTrue(fabric_url.endswith('-fabric-detail.jpg'))
        with default_storage.open(fabric_url.removeprefix('/media/')) as file, Image.open(file) as image:
            self.assertEqual(image.width, image.height)
            red, green, blue = image.getpixel((4, 4))
            self.assertGreater(red, green + 50)
            self.assertGreater(blue, green + 50)
        recrop = self.post(url + 'crop_fabric/', {'center_x': 80, 'center_y': 30})
        self.assertEqual(recrop.status_code, 200)
        self.assertNotEqual(recrop.data['fabric_image_url'], fabric_url)

        approved = self.post(url + 'approve_publish/', {'publish_to_woocommerce': False})
        self.assertEqual(approved.data['status'], 'APPROVED')
        self.assertFalse(self.product_posts)
        published = self.post(url + 'approve_publish/', {'publish_to_woocommerce': True})
        self.assertEqual(published.status_code, 200, published.data)
        self.assertEqual(published.data['status'], 'PUBLISHED')
        self.assertEqual(published.data['woocommerce_product_id'], 123)
        self.assertEqual(self.product_posts[0]['regular_price'], '47.00')
        self.assertEqual(self.product_posts[0]['images'], [{'id': 101}, {'id': 102}, {'id': 103}, {'id': 104}])
        self.assertIn(b'-model-side.jpg"', self.uploads[1])
        self.assertIn(b'-garment-front-back.jpg"', self.uploads[2])
        self.assertIn({'name': 'Size', 'options': ['28', '30'], 'visible': True}, self.product_posts[0]['attributes'])
        self.assertIn({'key': 'denim_size_chart_image_id', 'value': 105}, self.product_posts[0]['meta_data'])
        self.assertIn('<table', self.product_posts[0]['description'])
        self.assertIn({'key': 'denim_size_chart_image_url', 'value': 'https://shop.invalid/wp-content/uploads/chart-5.png'},
                      self.product_posts[0]['meta_data'])
        self.assertEqual(self.product_posts[0]['stock_quantity'], 7)
        self.assertEqual(self.post(url + 'approve_publish/', {}).data['status'], 'PUBLISHED')
        self.assertEqual(len(self.product_posts), 1)
        self.assertEqual(len(self.uploads), 5)
        self.assertEqual(len(self.shop_lookup), 1)

        logo = io.BytesIO()
        Image.new('RGBA', (120, 50), '#ffffff').save(logo, format='PNG')
        captions = {'facebook': 'Facebook caption', 'instagram': 'Instagram caption', 'tiktok': 'TikTok caption'}
        remote_requests = self.send.call_count
        export = self.post(url + 'export_social/', {
            **captions, 'logo': SimpleUploadedFile('logo.png', logo.getvalue(), content_type='image/png'),
        }, 'multipart')
        self.assertEqual(export.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(b''.join(export.streaming_content))) as archive:
            self.assertEqual(len(archive.namelist()), 15)
            for channel, caption in captions.items():
                self.assertEqual(archive.read(f'{channel}/caption.txt').decode(), caption)
            image = archive.read('instagram/atelier-indigo-jeans-model-front.jpg')
            with Image.open(io.BytesIO(image)) as watermarked:
                self.assertTrue(all(channel > 235 for channel in watermarked.getpixel((700, 975))))
        source = ready['image_1_url'].removeprefix('/media/')
        with default_storage.open(source) as file, Image.open(file) as original:
            self.assertLess(original.getpixel((700, 975))[0], 25)
        self.assertEqual(self.browser.get(url).data['social_draft'], captions)
        replay = self.post(url + 'export_social/', captions, 'multipart')
        self.assertEqual(replay.status_code, 200)
        replay.close()
        self.assertEqual(self.send.call_count, remote_requests)

        # Change the selection and product price without creating a new WooCommerce product.
        replacement = self.post('/pipeline/api/size-charts/', {
            'name': 'Straight leg centimeters', 'measurements': 'Size,Waist (cm)\n32,81\n34,86',
        })
        self.assertEqual(replacement.status_code, 201, replacement.data)
        edited = self.post(url + 'edit_job/', {
            'reviewer_edits': {'regular_price': '52.00', 'sale_price': '48.00', 'stock_quantity': 0},
            'size_chart_id': replacement.data['id'],
        })
        self.assertTrue(edited.data['listing_dirty'])
        caption = edited.data['suggested_captions']['instagram']
        self.assertIn('Sale: LKR 48.00 (regular LKR 52.00)', caption)
        self.assertIn('Size guide: 32, 34', caption)
        self.assertIn('out of stock', caption)
        self.assertIn('#straightleg', caption)
        updated = self.post(url + 'update_listing/', {})
        self.assertEqual(updated.status_code, 200, updated.data)
        self.assertEqual(updated.data['woocommerce_product_id'], 123)
        self.assertFalse(updated.data['listing_dirty'])
        self.assertEqual(len(self.product_posts), 1)
        self.assertEqual(len(self.uploads), 6)
        self.assertNotIn('images', self.product_updates[0])
        self.assertNotIn('status', self.product_updates[0])
        self.assertEqual(self.product_updates[0]['description'].count('<h2>Size chart</h2>'), 1)
        self.assertIn('Waist (cm)', self.product_updates[0]['description'])

        self.post(url + 'edit_job/', {'reviewer_edits': {}, 'show_size_chart_in_description': False})
        self.assertEqual(self.post(url + 'update_listing/', {}).status_code, 200)
        self.assertNotIn('<h2>Size chart</h2>', self.product_updates[1]['description'])
        self.assertTrue(any(item['key'] == 'denim_size_chart_image_id' and item['value'] == 106
                            for item in self.product_updates[1]['meta_data']))

        self.post(url + 'edit_job/', {'reviewer_edits': {}, 'size_chart_id': None})
        self.assertEqual(self.post(url + 'update_listing/', {}).status_code, 200)
        self.assertEqual(len(self.uploads), 6)
        self.assertNotIn('<h2>Size chart</h2>', self.product_updates[2]['description'])
        metadata = {item['key']: item['value'] for item in self.product_updates[2]['meta_data']}
        self.assertEqual(metadata['denim_size_chart_image_id'], '')
        self.assertEqual(metadata['denim_size_chart_image_url'], '')

    def test_generation_uses_all_seven_garment_photos_and_keeps_a_step_log(self):
        url = self.intake(garment_count=7, extra_model_photos=True)
        notes = 'Keep the cuffs unrolled.'
        response = self.post(url + 'generate/', {'generation_notes': notes})
        self.assertEqual(response.status_code, 202, response.data)
        ready = self.wait_for(url, 'PENDING_REVIEW')
        self.assertEqual(ready['generation_notes'], notes)
        front, side, cut = self.provider_calls[1:]
        self.assertEqual([len(shot['input_references']) for shot in (front, side, cut)], [9, 10, 7])
        self.assertEqual(front['input_references'][:7], cut['input_references'])
        self.assertEqual(side['input_references'][:9], front['input_references'])
        self.assertEqual(len({ref['image_url']['url'] for ref in cut['input_references']}), 7)
        self.assertEqual(len(ready['reference_photos']['model']), 5)
        self.assertIn('identity contact sheet', front['prompt'])
        encoded_sheet = front['input_references'][-1]['image_url']['url'].split(',', 1)[1]
        with Image.open(io.BytesIO(base64.b64decode(encoded_sheet))) as sheet:
            self.assertEqual(sheet.size, (1280, 1280))
            self.assertEqual(len({sheet.getpixel((x, y)) for x in (320, 960) for y in (320, 960)}), 4)
        self.assertTrue(all(notes in shot['prompt'] for shot in (front, cut, side)))
        self.assertTrue(all(ready[field] for field in ('image_1_url', 'image_2_url', 'image_3_url', 'fabric_image_url')))
        history = ready['workflow_progress']['history']
        self.assertEqual(list(dict.fromkeys(entry['step'] for entry in history)), [
            'extract', 'details', 'references', 'model-front', 'model-side', 'garment-front-back', 'fabric', 'review',
        ])
        self.assertTrue(all(entry['at'] for entry in history))

    def test_published_photo_updates_keep_remote_gallery_and_remove_only_confirmed_deleted_jobs(self):
        url = self.intake()
        self.assertEqual(self.post(url + 'generate/', {}).status_code, 202)
        self.wait_for(url, 'PENDING_REVIEW')
        published = self.post(url + 'approve_publish/', {})
        self.assertEqual(published.status_code, 200, published.data)
        self.assertEqual(published.data['status'], 'PUBLISHED')
        original = published.data['image_1_url']
        for color in ('red', 'pink'):
            edited = self.post(url + 'replace_photo/', {
                'shot': 'image_1_url', 'image': self.photo('replacement.jpg', color),
            }, 'multipart')
            self.assertEqual(edited.status_code, 200, edited.data)
            self.assertEqual(edited.data['gallery_replacements']['image_1_url'], original)
        self.assertEqual(len(self.uploads), 4)
        self.assertTrue(edited.data['listing_dirty'])
        self.assertEqual(self.post(url + 'regenerate_shot/', {'shot': 'image_2_url'}).status_code, 202)
        redo = self.wait_for(url, 'PUBLISHED')
        self.assertIn('image_2_url', redo['gallery_replacements'])
        updated = self.post(url + 'update_listing/', {})
        self.assertEqual(updated.status_code, 200, updated.data)
        self.assertEqual(updated.data['woocommerce_product_id'], 123)
        self.assertEqual(updated.data['gallery_replacements'], {})
        self.assertEqual(len(self.product_posts), 1)
        self.assertEqual(len(self.uploads), 6)
        self.assertEqual(self.product_updates[0]['images'], [
            {'id': 105}, {'id': 102}, {'id': 106}, {'id': 104}, {'id': 201},
        ])
        self.assertEqual(self.post(url + 'remove_deleted_listing/', {}).status_code, 409)
        self.remote_lookup_error = 'rest_no_route'
        self.assertEqual(self.post(url + 'remove_deleted_listing/', {}).status_code, 409)
        self.assertEqual(self.browser.get(url).status_code, 200)
        self.remote_lookup_error = 'woocommerce_rest_product_invalid_id'
        self.assertEqual(self.post(url + 'remove_deleted_listing/', {}).status_code, 204)
        self.assertEqual(self.browser.get(url).status_code, 404)
        self.assertTrue(default_storage.exists(updated.data['image_1_url'].removeprefix('/media/')))

    def test_five_model_photos_are_separate_when_there_is_room(self):
        url = self.intake(extra_model_photos=True)
        self.assertEqual(self.post(url + 'generate/', {}).status_code, 202)
        self.wait_for(url, 'PENDING_REVIEW')
        front, side, flat_lay = self.provider_calls[1:]
        self.assertEqual([len(shot['input_references']) for shot in (front, side, flat_lay)], [7, 8, 2])
        self.assertIn('face close-up', front['prompt'])
        self.assertIn('Ignore faces and bodies in the garment photos', front['prompt'])
        self.assertIn('match the real references', side['prompt'])

    @override_settings(MODEL_TRYON_PROVIDER='fashn', FASHN_API_KEY='test-fashn-key')
    def test_tryon_uses_exact_pose_photos_and_resumes_the_saved_prediction(self):
        from .langgraph_workflow import reference_image

        url = self.intake(extra_model_photos=True, bulk_count=3)
        self.fail_tryon_poll_once = True
        self.assertEqual(self.post(url + 'generate/', {'generation_notes': 'Keep the original hem.'}).status_code, 202)
        self.wait_for(url, 'FAILED')
        self.assertEqual(len(self.tryon_requests), 1)
        self.assertEqual(self.post(url + 'generate/', {}).status_code, 202)
        result = self.wait_for(url, 'PENDING_REVIEW')
        self.assertEqual(len(result['reference_photos']['model']), 8)
        self.assertEqual(len(self.tryon_requests), 2)
        job = ProductJob.objects.get(job_id=result['job_id'])
        role_paths = dict(zip(job.model_reference['photo_roles'], job.model_reference['photos']))
        product = reference_image(job.garment_images[0], max_dimension=1600)['image_url']['url']
        for payload, role in zip(self.tryon_requests, ('front view', 'side profile')):
            self.assertEqual(payload['model_name'], 'tryon-max')
            inputs = payload['inputs']
            self.assertEqual(inputs['model_image'], reference_image(role_paths[role], max_dimension=1600)['image_url']['url'])
            self.assertEqual(inputs['product_image'], product)
            self.assertIn('Replace only the trousers', inputs['prompt'])
            self.assertIn('Keep the original hem', inputs['prompt'])
        self.assertEqual(job.tryon_predictions, {})
        self.assertEqual(len(self.provider_calls), 2)  # Copy and flat lay.
        self.assertTrue(result['image_1_url'] and result['image_3_url'] and result['fabric_image_url'])

    @override_settings(
        MODEL_TRYON_PROVIDER='openrouter_masked', FASHN_API_KEY='',
        OPENROUTER_IMAGE_MODEL='bytedance-seed/seedream-5-0-pro',
    )
    def test_openrouter_pose_edit_saves_a_whole_photo_without_a_split(self):
        url = self.intake()
        self.assertEqual(self.post(url + 'generate/', {}).status_code, 202)
        ready = self.wait_for(url, 'PENDING_REVIEW')
        job = ProductJob.objects.get(job_id=ready['job_id'])
        self.assertFalse(job.model_reference.get('garment_masks'))
        self.assertEqual(len(self.provider_calls), 4)
        self.assertEqual(self.tryon_requests, [])
        for call in self.provider_calls[1:]:
            self.assertEqual(call['model'], 'bytedance-seed/seedream-5-0-pro')
            self.assertEqual(call['resolution'], '1K')
            self.assertEqual(call['aspect_ratio'], '3:4')
        self.assertEqual([call['output_format'] for call in self.provider_calls[1:]], ['png', 'png', 'jpeg'])
        for call, field in zip(self.provider_calls[1:3], ('image_1_url', 'image_3_url')):
            self.assertIn('one continuous photograph', call['prompt'])
            self.assertIn('Remove every visible trace', call['prompt'])
            self.assertEqual(len(call['input_references']), 3)
            with default_storage.open(ready[field].removeprefix('/media/')) as file, Image.open(file) as output:
                self.assertEqual(output.format, 'PNG')
                self.assertEqual(output.getpixel((380, 250)), output.getpixel((380, 700)))

    @override_settings(GEMINI_API_KEY='', GEMINI_IMAGE_MODEL='gemini-nano-banana-2.1')
    def test_provider_toggle_routes_only_photo_requests_to_gemini(self):
        path = '/pipeline/api/image-provider/'
        self.assertEqual(self.browser.get(path).data['provider'], 'openrouter')
        self.assertEqual(self.post(path, {'provider': 'gemini'}).status_code, 400)
        self.assertEqual(APIClient().get(path).status_code, 403)
        url = self.intake()

        def fake_gemini(prompt, references, *, aspect_ratio):
            self.assertEqual(aspect_ratio, '3:4')
            color = 'teal' if 'side profile' in prompt else 'navy'
            return base64.b64encode(self.image_bytes(color)).decode('ascii')

        with override_settings(GEMINI_API_KEY='test-gemini-key'):
            enabled = self.post(path, {'provider': 'gemini'})
            self.assertEqual(enabled.status_code, 200, enabled.data)
            self.assertTrue(enabled.data['gemini_configured'])
            self.assertNotIn('test-gemini-key', enabled.content.decode())
            with patch('catalog_pipeline.langgraph_workflow.generate_gemini_image', side_effect=fake_gemini) as gemini:
                self.assertEqual(self.post(url + 'generate/', {}).status_code, 202)
                ready = self.wait_for(url, 'PENDING_REVIEW')
                self.assertEqual(gemini.call_count, 3)
                self.assertEqual(len(self.provider_calls), 1)  # Only the catalog copy request.
                self.assertTrue(ready['fabric_image_url'])
                self.assertIn('gemini-nano-banana-2.1', [entry['model'] for entry in ready['workflow_progress']['history']])
                self.assertEqual(self.post(path, {'provider': 'openrouter'}).data['provider'], 'openrouter')
                self.assertEqual(self.post(url + 'regenerate_shot/', {'shot': 'image_2_url'}).status_code, 202)
                self.wait_for(url, 'PENDING_REVIEW')
                self.assertEqual(gemini.call_count, 3)
                self.assertEqual(len(self.provider_calls), 2)

    def test_front_regeneration_keeps_the_original_pair_if_the_second_edit_fails(self):
        url = self.intake()
        self.assertEqual(self.post(url + 'generate/', {}).status_code, 202)
        self.wait_for(url, 'PENDING_REVIEW')
        original = self.post(url + 'approve_publish/', {}).data
        self.assertEqual(original['status'], 'PUBLISHED')
        self.fail_image_number = len(self.provider_calls) + 2
        self.assertEqual(self.post(url + 'regenerate_shot/', {'shot': 'image_1_url'}).status_code, 202)
        failed = self.wait_for(url, 'PUBLISHED')
        self.assertIn('HTTP 503', failed['error_message'])
        self.assertEqual(failed['image_1_url'], original['image_1_url'])
        self.assertEqual(failed['image_3_url'], original['image_3_url'])
        self.assertEqual(failed['gallery_replacements'], {})
        self.assertEqual(self.post(url + 'regenerate_shot/', {'shot': 'image_1_url'}).status_code, 202)
        regenerated = self.wait_for(url, 'PUBLISHED')
        self.assertEqual(regenerated['gallery_replacements'], {
            'image_1_url': original['image_1_url'], 'image_3_url': original['image_3_url'],
        })
        self.assertNotEqual(regenerated['image_1_url'], original['image_1_url'])
        self.assertNotEqual(regenerated['image_3_url'], original['image_3_url'])
        self.assertEqual(regenerated['image_2_url'], original['image_2_url'])
        self.assertFalse(self.product_updates)

    def test_redo_side_uses_one_call_and_keeps_original_if_duplicate(self):
        url = self.intake()
        self.assertEqual(self.post(url + 'generate/', {}).status_code, 202)
        ready = self.wait_for(url, 'PENDING_REVIEW')
        self.duplicate_side = True
        self.assertEqual(self.post(url + 'regenerate_shot/', {'shot': 'image_3_url'}).status_code, 202)
        failed = self.wait_for(url, 'PENDING_REVIEW')
        self.assertIn('near-duplicate', failed['error_message'])
        self.assertEqual(failed['image_3_url'], ready['image_3_url'])
        self.duplicate_side = False
        self.assertEqual(self.post(url + 'regenerate_shot/', {'shot': 'image_3_url'}).status_code, 202)
        replaced = self.wait_for(url, 'PENDING_REVIEW')
        self.assertNotEqual(replaced['image_3_url'], ready['image_3_url'])
        for field in ('image_1_url', 'image_2_url', 'fabric_image_url'):
            self.assertEqual(replaced[field], ready[field])
        self.assertEqual(len(self.provider_calls), 6)

    def test_chart_photo_returns_editable_measurements_without_saving(self):
        result = self.post('/pipeline/api/size-charts/read_photo/', {'image': self.photo('notes.jpg', 'white')}, 'multipart')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['measurements'], 'Size,Waist (in)\n28,?\n30,30.5\n')
        self.assertEqual(self.browser.get('/pipeline/api/size-charts/').data['count'], 0)
        invalid = self.post('/pipeline/api/size-charts/', {'name': 'Handwritten', **result.data})
        self.assertEqual(invalid.status_code, 400)
        saved = self.post('/pipeline/api/size-charts/', {'name': 'Handwritten', 'measurements': result.data['measurements'].replace('?', '28')})
        self.assertEqual(saved.status_code, 201, saved.data)
        self.assertEqual(saved.data['table_rows'][1], ['28', '28'])
        self.assertEqual(self.send.call_count, 1)

    @override_settings(
        META_GRAPH_API_VERSION='v27.0', META_APP_ID='1234', META_APP_SECRET='mock-app-secret',
        FACEBOOK_PAGE_ID='100', FACEBOOK_PAGE_ACCESS_TOKEN='mock-social-token',
        INSTAGRAM_ACCOUNT_ID='200', INSTAGRAM_ACCESS_TOKEN='mock-social-token',
    )
    def test_social_connections_verify_identity_without_posting(self):
        result = self.post('/pipeline/api/social/connections/', {})
        self.assertEqual(result.data['facebook']['account'], 'Studio Page')
        self.assertEqual(result.data['instagram']['account'], 'studio')
        self.assertFalse(result.data['tiktok']['ok'])
        self.assertNotIn('mock-social-token', result.content.decode())
        with override_settings(INSTAGRAM_ACCOUNT_ID='999'):
            self.assertFalse(self.post('/pipeline/api/social/connections/', {}).data['instagram']['ok'])
        with override_settings(INSTAGRAM_ACCESS_TOKEN='', INSTAGRAM_ACCOUNT_ID=''):
            count = self.send.call_count
            linked = self.post('/pipeline/api/social/connections/', {}).data
            self.assertEqual(linked['instagram']['account'], 'linkedstudio')
            self.assertEqual(self.send.call_count - count, 1)

    @override_settings(TIKTOK_ACCESS_TOKEN='mock-tiktok-token', TIKTOK_MEDIA_BASE_URL='https://media.example.test/photos')
    def test_social_campaign_confirmation_and_platform_status_without_real_posts(self):
        url = self.intake()
        self.assertEqual(self.post(url + 'generate/', {}).status_code, 202)
        self.wait_for(url, 'PENDING_REVIEW')
        publish = url + 'publish_social/'
        facebook = {'platform': 'facebook', 'caption': 'Campaign photos', 'consent': True}
        self.assertEqual(self.post(publish, {**facebook, 'consent': False}).status_code, 400)
        with patch('catalog_pipeline.campaign_publishing.publish_facebook', return_value={'id': 'facebook-post'}) as send:
            result = self.post(publish, facebook)
            self.assertEqual(result.data['state'], 'published')
            self.assertEqual(self.post(publish, facebook).data['state'], 'published')
            send.assert_called_once()
            self.assertIn('model-front', send.call_args.args[0][0][0])

        tiktok = {**facebook, 'platform': 'tiktok', 'title': 'Indigo jeans'}
        with patch('catalog_pipeline.campaign_publishing.upload_tiktok_photos', return_value='tiktok-draft') as send:
            self.assertEqual(self.post(publish, tiktok).data['state'], 'processing')
            self.assertTrue(all(image.startswith('https://media.example.test/photos/social_posts/')
                                for image in send.call_args.kwargs['photo_urls']))
        with patch('catalog_pipeline.views.tiktok_post_status', return_value={'status': 'SEND_TO_USER_INBOX'}) as check:
            self.assertEqual(self.post(url + 'refresh_social/', {}).data['state'], 'draft')
            check.assert_called_once_with('tiktok-draft')
        self.assertEqual(self.browser.get(url).data['social_publications']['tiktok']['state'], 'draft')

    def test_retry_generates_only_missing_images(self):
        url = self.intake()
        self.fail_image_number = 3
        self.assertEqual(self.post(url + 'generate/', {}).status_code, 202)
        failed = self.wait_for(url, 'FAILED')
        self.assertTrue(failed['image_1_url'])
        self.assertFalse(failed['image_2_url'])
        self.assertEqual(len(self.provider_calls), 3)
        self.assertEqual(self.post(url + 'generate/', {}).status_code, 202)
        ready = self.wait_for(url, 'PENDING_REVIEW')
        self.assertEqual(ready['image_1_url'], failed['image_1_url'])
        self.assertEqual(len(self.provider_calls), 5)

    def test_admin_dashboard_and_profile_form(self):
        self.assertContains(self.browser.get('/admin/'), 'Catalog workspace')
        url = '/admin/catalog_pipeline/modelprofile/add/'
        self.assertContains(self.browser.get(url), 'Generation instructions')
        result = self.post(url, {
            'name': 'Admin model', 'description': 'Keep neutral styling.',
            'reference_image': self.photo('front.jpg', 'black'),
            'side_reference_image': self.photo('side.jpg', 'navy'),
            'bulk_reference_images': [self.photo('angle.jpg', 'purple')],
            '_save': 'Save',
        }, 'multipart')
        self.assertEqual(result.status_code, 302)
        self.assertContains(self.browser.get(result.url), 'Admin model')
        self.assertEqual(len(self.browser.get('/pipeline/api/model-profiles/').data['results'][0]['bulk_references']), 1)
        self.assertContains(self.browser.get('/admin/catalog_pipeline/productjob/'), 'Product jobs')
        self.assertEqual(self.send.call_count, 0)

    def test_dashboard_and_workflow_require_staff(self):
        anonymous = APIClient()
        self.assertEqual(anonymous.get('/pipeline/dashboard/').status_code, 302)
        self.assertEqual(anonymous.get('/pipeline/api/model-profiles/').status_code, 403)
        self.assertEqual(anonymous.get('/pipeline/api/jobs/').status_code, 403)
        self.assertFalse(ProductJob.objects.exists())

    def test_store_connection_check_is_read_only(self):
        path = '/pipeline/api/store/connection/'
        result = self.post(path, {})
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.data['woocommerce']['ok'])
        self.assertTrue(result.data['media']['ok'])
        self.assertEqual([call.args[0].method for call in self.send.call_args_list], ['GET', 'GET'])
        self.assertFalse(self.uploads)
        self.assertFalse(self.product_posts)
        with override_settings(WORDPRESS_MEDIA_APP_PASSWORD=''):
            result = self.post(path, {})
        self.assertFalse(result.data['media']['ok'])
        self.assertIn('WORDPRESS_MEDIA_APP_PASSWORD', result.data['media']['message'])
        self.assertEqual(self.send.call_count, 3)
        self.assertEqual(APIClient().post(path, {}, format='json').status_code, 403)

    def test_storefront_paste_survives_openrouter_connection_failure(self):
        pasted = """[**Wide Leg**](https://denimyard.com/product-category/wide-leg/)

# Imported Cinch Baggy Flattering High Rise Wide Straight Leg Women’S Jean

SKU: **DY-263** • **Currently Out Of Stock**

**රු7,196.00**

**Select Waist Size**

**Out of stock**

**Size Chart**

**Islandwide Delivery**Colombo 1–2 days • Outstation 2–4 days

**Cash On Delivery**Pay via Card, or COD

**Product Details−**

## About this Style

Love the classic baggy denim look but dread the waist gap? Look no further than our Cinch Baggy jeans. We cut them with a flattering mid rise, a straight leg and an easy, loose fit. Even better? This style features adjusters on the back for a tailored-to-you fit.

- Baggy jeans, without the waist gap
- With a flattering high rise
- Cut with a wide, straight leg
- Features adjusters on the back for a tailored-to-you fit
- Take your true size for the perfect fit
- Made with soft, 100% cotton that gives it an easy, drapey fit

Color: Never Going To Change – Light Wash
"""
        self.send.side_effect = ConnectionError('Simulated blocked outbound socket')
        job = ProductJob.objects.create(job_id='copied-product', raw_product_text=pasted, status='FAILED')
        url = f'/pipeline/api/jobs/{job.job_id}/'
        self.assertEqual(self.post(url + 'retry_extraction/', {}).status_code, 202)
        details = self.wait_for(url, 'PENDING_DETAILS')['final_data']
        self.assertTrue(details['title'].startswith('Imported Cinch Baggy'))
        self.assertEqual(details['jean_type'], 'Wide Straight Leg')
        self.assertEqual(details['sku'], 'DY-263')
        self.assertEqual(details['regular_price'], '7196.00')
        self.assertEqual(details['stock_quantity'], 0)
        self.assertTrue(details['manage_stock'])
        self.assertIn('100% cotton', details['description'])
        self.assertNotIn('Size Chart', details['description'])
        self.assertNotIn('Cash On Delivery', details['description'])
        self.assertIn('conflicting rise', details['extraction_notice'])
        self.assertIn('unreachable', details['extraction_notice'])
        self.assertEqual(self.send.call_count, 1)
