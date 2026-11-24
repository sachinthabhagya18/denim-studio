"""Send photo drafts through TikTok's Content Posting API."""

from urllib.parse import unquote, urlsplit

import requests
from django.conf import settings


API_BASE = 'https://open.tiktokapis.com/v2'
AUTH_ERRORS = {
    'access_token_invalid': 'The TikTok access token is invalid or expired. Refresh the token and update TIKTOK_ACCESS_TOKEN.',
    'scope_not_authorized': 'Authorize this TikTok account with video.upload and user.info.basic.',
    'token_not_authorized_for_specified_publish_id': 'This TikTok account cannot read the status of that upload.',
}
UPLOAD_ERRORS = {
    'app_version_check_failed': 'Update the TikTok app on the creator’s device to accept photo drafts.',
    'invalid_param': 'TikTok rejected the draft details. Review the photos, title and caption.',
    'invalid_publish_id': 'TikTok could not find this upload.',
    'rate_limit_exceeded': 'TikTok is rate-limiting requests. Try again shortly.',
    'reached_active_user_cap': 'The TikTok app has reached its daily creator limit.',
    'spam_risk_too_many_pending_share': 'This TikTok account has too many pending drafts. Review its inbox first.',
    'spam_risk_too_many_posts': 'This TikTok account has reached its daily posting limit.',
    'spam_risk_user_banned_from_posting': 'TikTok has temporarily restricted posting for this account.',
    'url_ownership_unverified': 'Verify the media domain or URL prefix in your TikTok developer app.',
}
FAIL_REASONS = {
    'auth_removed': 'The creator removed this TikTok app’s access while the upload was processing.',
    'file_format_check_failed': 'TikTok rejected the image format.',
    'picture_size_check_failed': 'TikTok rejected the image dimensions.',
    'photo_pull_failed': 'TikTok could not download the photos. Check that each HTTPS URL is publicly accessible.',
    'pull_from_url_failed': 'TikTok could not download the photos. Check that each HTTPS URL is publicly accessible.',
    'publish_cancelled': 'The TikTok upload was cancelled.',
    'spam_risk': 'TikTok rejected the draft after its content checks.',
    'spam_risk_text': 'TikTok rejected the caption after its content checks.',
    'spam_risk_user_banned_from_posting': 'TikTok has restricted posting for this creator.',
}
TERMINAL_FAILURES = {'auth_removed', 'spam_risk', 'spam_risk_text', 'spam_risk_user_banned_from_posting'}


class TikTokError(Exception):
    def __init__(self, message, *, ambiguous=False):
        super().__init__(message)
        self.ambiguous = ambiguous


def _access_token():
    return getattr(settings, 'TIKTOK_ACCESS_TOKEN', '').strip()


def _https_url(value):
    if not isinstance(value, str) or any(char.isspace() or char == '\\' for char in value):
        raise ValueError('Invalid URL')
    url = urlsplit(value)
    if (url.scheme != 'https' or not url.hostname or url.username is not None or url.password is not None
            or url.port not in (None, 443) or url.fragment):
        raise ValueError('Invalid HTTPS URL')
    return url


def _media_base():
    value = getattr(settings, 'TIKTOK_MEDIA_BASE_URL', '').strip()
    try:
        base = _https_url(value)
        if base.query or '.' not in base.hostname or base.hostname.replace('.', '').isdigit():
            raise ValueError('Invalid media prefix')
    except ValueError:
        raise TikTokError('Set TIKTOK_MEDIA_BASE_URL to a verified public HTTPS media URL prefix.') from None
    return base


def tiktok_configured():
    if not _access_token():
        return False
    try:
        _media_base()
    except TikTokError:
        return False
    return True


def _request(method, path, *, data=None, params=None, final_upload=False):
    token = _access_token()
    if not token:
        raise TikTokError('Configure TIKTOK_ACCESS_TOKEN with an authorized TikTok user access token.')
    try:
        response = requests.request(
            method, f'{API_BASE}/{path}/', json=data, params=params,
            headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json'},
            timeout=12, allow_redirects=False,
        )
    except requests.RequestException:
        raise TikTokError('TikTok did not return a response.', ambiguous=final_upload) from None
    try:
        payload = response.json()
    except ValueError:
        ambiguous = final_upload and (200 <= response.status_code < 300 or response.status_code >= 500)
        raise TikTokError('TikTok returned an unreadable response.', ambiguous=ambiguous) from None

    error = payload.get('error') if isinstance(payload, dict) else None
    code = error.get('code') if isinstance(error, dict) else None
    if response.status_code != 200 or code != 'ok':
        if not isinstance(code, str):
            code = ''
        message = AUTH_ERRORS.get(code) or UPLOAD_ERRORS.get(code)
        if message is None:
            if response.status_code == 401:
                message = AUTH_ERRORS['access_token_invalid']
            elif response.status_code == 429:
                message = UPLOAD_ERRORS['rate_limit_exceeded']
        ambiguous = final_upload and (
            response.status_code >= 500
            or (200 <= response.status_code < 300 and code in ('', 'ok', 'internal_error'))
        )
        raise TikTokError(message or 'TikTok could not complete the request.', ambiguous=ambiguous)
    result = payload.get('data')
    if not isinstance(result, dict):
        raise TikTokError('TikTok did not return usable response data.', ambiguous=final_upload)
    return result


def tiktok_account():
    result = _request('GET', 'user/info', params={'fields': 'open_id,display_name'})
    user = result.get('user')
    if not isinstance(user, dict) or not all(
        isinstance(user.get(key), str) and user[key] for key in ('display_name', 'open_id')
    ):
        raise TikTokError('TikTok did not return the connected account’s name.')
    return {'nickname': user['display_name'], 'account_id': user['open_id']}


def upload_tiktok_photos(*, photo_urls, title, description, is_aigc=True):
    base = _media_base()
    if not isinstance(photo_urls, (list, tuple)) or not 1 <= len(photo_urls) <= 35:
        raise TikTokError('Choose between 1 and 35 photos for TikTok.')
    for value in photo_urls:
        try:
            image_url = _https_url(value)
            decoded_path = unquote(image_url.path)
            if (image_url.hostname != base.hostname or not image_url.path.startswith(base.path.rstrip('/') + '/')
                    or any(part in ('.', '..') for part in decoded_path.split('/')) or '\\' in decoded_path):
                raise ValueError('URL outside media prefix')
        except ValueError:
            raise TikTokError('All TikTok photos need HTTPS URLs under the verified media prefix.') from None
    for label, value, limit in (('title', title, 90), ('caption', description, 4000)):
        try:
            valid = isinstance(value, str) and len(value.encode('utf-16-le')) // 2 <= limit
        except UnicodeEncodeError:
            valid = False
        if not valid:
            raise TikTokError(f'The TikTok {label} must fit within {limit} UTF-16 characters.')
    result = _request('POST', 'post/publish/content/init', final_upload=True, data={
        'post_info': {'title': title, 'description': description},
        'source_info': {'source': 'PULL_FROM_URL', 'photo_images': photo_urls, 'photo_cover_index': 0},
        'post_mode': 'MEDIA_UPLOAD',
        'media_type': 'PHOTO',
        'is_aigc': is_aigc,
    })
    publish_id = result.get('publish_id')
    if not isinstance(publish_id, str) or not 1 <= len(publish_id) <= 64:
        raise TikTokError('TikTok did not return an upload reference.', ambiguous=True)
    return publish_id


def tiktok_post_status(publish_id):
    if not isinstance(publish_id, str) or not 1 <= len(publish_id) <= 64:
        raise TikTokError('A valid TikTok upload reference is needed to check its status.')
    result = _request('POST', 'post/publish/status/fetch', data={'publish_id': publish_id})
    status = result.get('status')
    if status not in ('PROCESSING_UPLOAD', 'PROCESSING_DOWNLOAD', 'SEND_TO_USER_INBOX', 'PUBLISH_COMPLETE', 'FAILED'):
        raise TikTokError('TikTok did not return a recognized upload status.')
    post_ids = result.get('publicaly_available_post_id') or result.get('publicly_available_post_id')
    if not isinstance(post_ids, list):
        post_ids = []
    reason = result.get('fail_reason')
    if not isinstance(reason, str):
        reason = ''
    failure = ''
    retry_allowed = False
    if status == 'FAILED':
        failure = FAIL_REASONS.get(reason, 'TikTok reported that the draft could not be completed.')
        retry_allowed = reason not in TERMINAL_FAILURES
    return {
        'status': status,
        'fail_reason': failure,
        'post_ids': [str(post_id) for post_id in post_ids],
        'retry_allowed': retry_allowed,
    }
