import hmac
import json
from unittest.mock import patch

import requests
from django.test import SimpleTestCase, override_settings

from .social import MetaPublishError, publish_facebook, publish_instagram


def graph_response(status, body):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(body).encode()
    return response


@override_settings(
    META_GRAPH_API_VERSION='v27.0', META_APP_ID='1234', META_APP_SECRET='test-app-secret',
    FACEBOOK_PAGE_ID='100', FACEBOOK_PAGE_ACCESS_TOKEN='test-page-token',
    INSTAGRAM_ACCOUNT_ID='200', INSTAGRAM_ACCESS_TOKEN='test-instagram-token',
)
class MetaPublishingTests(SimpleTestCase):
    @patch('catalog_pipeline.social.requests.post')
    def test_facebook_stages_each_photo_then_publishes_the_ordered_post(self, post):
        post.side_effect = [
            graph_response(200, {'id': '21'}), graph_response(200, {'id': '22'}),
            graph_response(200, {'id': '100_54'}),
        ]
        photos = [('front.jpg', b'front'), ('back.jpg', b'back')]

        self.assertEqual(publish_facebook(photos, 'Test caption'), {'id': '100_54'})
        first, second, published = post.call_args_list
        self.assertEqual(first.args[0], 'https://graph.facebook.com/v27.0/100/photos')
        self.assertEqual(first.kwargs['files']['source'], ('front.jpg', b'front', 'image/jpeg'))
        self.assertEqual(second.kwargs['files']['source'], ('back.jpg', b'back', 'image/jpeg'))
        self.assertEqual(first.kwargs['data']['published'], 'false')
        self.assertEqual(second.kwargs['data']['published'], 'false')
        self.assertEqual(published.args[0], 'https://graph.facebook.com/v27.0/100/feed')
        self.assertEqual(published.kwargs['headers']['Authorization'], 'Bearer test-page-token')
        self.assertEqual(published.kwargs['data']['message'], 'Test caption')
        self.assertEqual(json.loads(published.kwargs['data']['attached_media[0]']), {'media_fbid': '21'})
        self.assertEqual(json.loads(published.kwargs['data']['attached_media[1]']), {'media_fbid': '22'})
        proof = hmac.digest(b'test-app-secret', b'test-page-token', 'sha256').hex()
        self.assertEqual(published.kwargs['data']['appsecret_proof'], proof)
        self.assertFalse(published.kwargs['allow_redirects'])

    @patch('catalog_pipeline.social.requests.post')
    def test_instagram_creates_a_carousel_before_publishing(self, post):
        post.side_effect = [graph_response(200, {'id': str(i)}) for i in range(1, 5)]
        images = ['https://catalog.example/front.jpg', 'https://catalog.example/back.jpg']

        self.assertEqual(publish_instagram(images, 'Test caption'), {'id': '4'})
        first, second, carousel, published = post.call_args_list
        self.assertEqual(first.args[0], 'https://graph.instagram.com/v27.0/200/media')
        self.assertEqual(first.kwargs['data'], {'image_url': images[0], 'is_carousel_item': 'true'})
        self.assertEqual(second.kwargs['data'], {'image_url': images[1], 'is_carousel_item': 'true'})
        self.assertEqual(carousel.kwargs['data'], {
            'caption': 'Test caption', 'children': '1,2', 'media_type': 'CAROUSEL', 'is_ai_generated': 'true',
        })
        self.assertEqual(published.args[0], 'https://graph.instagram.com/v27.0/200/media_publish')
        self.assertEqual(published.kwargs['data'], {'creation_id': '3'})
        self.assertEqual(published.kwargs['headers']['Authorization'], 'Bearer test-instagram-token')

    @override_settings(INSTAGRAM_ACCESS_TOKEN='', INSTAGRAM_ACCOUNT_ID='')
    @patch('catalog_pipeline.social.check_facebook_page')
    @patch('catalog_pipeline.social.requests.post')
    def test_linked_instagram_uses_the_page_token_and_linked_account_id(self, post, check):
        check.return_value = ({'ok': True}, {'id': '300', 'username': 'studio'})
        post.side_effect = [graph_response(200, {'id': str(i)}) for i in range(1, 5)]

        self.assertEqual(publish_instagram(['https://catalog.example/front.jpg'], 'Test caption'), {'id': '2'})
        published = post.call_args
        self.assertEqual(published.args[0], 'https://graph.facebook.com/v27.0/300/media_publish')
        self.assertEqual(published.kwargs['headers']['Authorization'], 'Bearer test-page-token')
        self.assertIn('appsecret_proof', published.kwargs['data'])
        self.assertEqual(post.call_args_list[0].kwargs['data']['is_ai_generated'], 'true')

        publish_instagram(['https://catalog.example/front.jpg'], 'Test caption', is_ai_generated=False)
        self.assertNotIn('is_ai_generated', post.call_args_list[2].kwargs['data'])

    @patch('catalog_pipeline.social.requests.post')
    def test_final_publish_timeout_is_ambiguous_but_staging_timeout_is_retryable(self, post):
        post.side_effect = requests.Timeout()
        with self.assertRaises(MetaPublishError) as staged:
            publish_instagram(['https://catalog.example/front.jpg'], 'Test caption')
        self.assertFalse(staged.exception.ambiguous)

        with self.assertRaises(MetaPublishError) as published:
            publish_facebook([('front.jpg', b'front')], 'Test caption')
        self.assertTrue(published.exception.ambiguous)

        post.side_effect = None
        post.return_value = graph_response(400, {'error': {'code': 200, 'message': 'test-page-token'}})
        with self.assertRaises(MetaPublishError) as rejected:
            publish_facebook([('front.jpg', b'front')], 'Test caption')
        self.assertFalse(rejected.exception.ambiguous)
        self.assertNotIn('test-page-token', str(rejected.exception))
