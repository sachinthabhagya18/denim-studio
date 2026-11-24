from unittest.mock import Mock, patch

import requests
from django.test import SimpleTestCase, override_settings

from .tiktok import TikTokError, tiktok_account, tiktok_post_status, upload_tiktok_photos


@override_settings(TIKTOK_ACCESS_TOKEN='test-token', TIKTOK_MEDIA_BASE_URL='https://media.example.com/catalog/')
class TikTokPhotoTests(SimpleTestCase):
    def upload(self, **overrides):
        return upload_tiktok_photos(**{
            'photo_urls': ['https://media.example.com/catalog/first.jpg', 'https://media.example.com/catalog/second.jpg'],
            'title': 'Relaxed denim',
            'description': 'See the new fit.',
            **overrides,
        })

    @patch('catalog_pipeline.tiktok.requests.request')
    def test_upload_sends_photo_draft_with_editable_caption(self, request):
        request.return_value = Mock(status_code=200)
        request.return_value.json.return_value = {'error': {'code': 'ok'}, 'data': {'publish_id': 'draft-9'}}
        self.assertEqual(self.upload(), 'draft-9')
        self.assertEqual(request.call_args.args, ('POST', 'https://open.tiktokapis.com/v2/post/publish/content/init/'))
        self.assertEqual(request.call_args.kwargs['json'], {
            'post_info': {'title': 'Relaxed denim', 'description': 'See the new fit.'},
            'source_info': {
                'source': 'PULL_FROM_URL',
                'photo_images': ['https://media.example.com/catalog/first.jpg', 'https://media.example.com/catalog/second.jpg'],
                'photo_cover_index': 0,
            },
            'post_mode': 'MEDIA_UPLOAD',
            'media_type': 'PHOTO',
            'is_aigc': True,
        })
        self.assertFalse(request.call_args.kwargs['allow_redirects'])

    @patch('catalog_pipeline.tiktok.requests.request')
    def test_invalid_photo_urls_and_utf16_title_are_rejected_locally(self, request):
        for url in (
            'https://uploads.example.com/catalog/p.jpg',
            'https://media.example.com/catalogue/p.jpg',
            'https://media.example.com/catalog/%2e%2e/p.jpg',
            'http://media.example.com/catalog/p.jpg',
        ):
            with self.subTest(url=url), self.assertRaises(TikTokError):
                self.upload(photo_urls=[url])
        with self.assertRaisesMessage(TikTokError, '90 UTF-16 characters'):
            self.upload(title='👖' * 46)
        request.assert_not_called()

    @patch('catalog_pipeline.tiktok.requests.request')
    def test_final_upload_ambiguity_distinguishes_timeout_and_rejection(self, request):
        request.side_effect = requests.Timeout()
        with self.assertRaises(TikTokError) as timeout:
            self.upload()
        self.assertTrue(timeout.exception.ambiguous)
        request.side_effect = None
        request.return_value = Mock(status_code=500)
        request.return_value.json.return_value = {'error': {'code': 'internal_error'}}
        with self.assertRaises(TikTokError) as server_error:
            self.upload()
        self.assertTrue(server_error.exception.ambiguous)
        request.return_value = Mock(status_code=403)
        request.return_value.json.return_value = {'error': {'code': 'url_ownership_unverified'}}
        with self.assertRaisesMessage(TikTokError, 'Verify the media domain') as rejected:
            self.upload()
        self.assertFalse(rejected.exception.ambiguous)

    @patch('catalog_pipeline.tiktok.requests.request')
    def test_account_identity_and_inbox_state_remain_distinct_from_a_public_post(self, request):
        request.return_value = Mock(status_code=200)
        request.return_value.json.side_effect = [
            {'error': {'code': 'ok'}, 'data': {'user': {'display_name': 'Denim Store', 'open_id': 'creator-7'}}},
            {'error': {'code': 'ok'}, 'data': {'status': 'SEND_TO_USER_INBOX'}},
            {'error': {'code': 'ok'}, 'data': {'status': 'PUBLISH_COMPLETE', 'publicaly_available_post_id': [123456789012345678]}},
            {'error': {'code': 'ok'}, 'data': {'status': 'FAILED', 'fail_reason': 'auth_removed'}},
            {'error': {'code': 'ok'}, 'data': {'status': 'FAILED', 'fail_reason': 'photo_pull_failed'}},
        ]
        self.assertEqual(tiktok_account(), {'nickname': 'Denim Store', 'account_id': 'creator-7'})
        self.assertEqual(tiktok_post_status('draft-9'), {
            'status': 'SEND_TO_USER_INBOX', 'fail_reason': '', 'post_ids': [], 'retry_allowed': False,
        })
        self.assertEqual(tiktok_post_status('draft-9')['post_ids'], ['123456789012345678'])
        self.assertFalse(tiktok_post_status('draft-9')['retry_allowed'])
        self.assertTrue(tiktok_post_status('draft-9')['retry_allowed'])
