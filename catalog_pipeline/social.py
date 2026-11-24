"""Social captions, account checks and photo publishing for Meta."""

import hmac
import html
import json
import re
from decimal import Decimal, InvalidOperation
from functools import partial
from urllib.parse import urlsplit

import requests
from django.conf import settings
from django.utils.html import strip_tags
from django.utils.text import slugify
from facebook_business.api import FacebookAdsApi
from facebook_business.exceptions import FacebookRequestError
from facebook_business.session import FacebookSession

from .catalog_media import SOCIAL_CHANNELS


GRAPH_API_VERSION = re.compile(r'v\d+\.\d+')


class MetaPublishError(Exception):
    def __init__(self, message, *, ambiguous=False):
        super().__init__(message)
        self.ambiguous = ambiguous


def suggested_captions(job):
    details = job.get_final_data()
    title = details.get('title')
    if not title:
        return {}
    summary = ' '.join(html.unescape(strip_tags(details.get('short_description') or details.get('description') or '')).split())[:340]
    sizes = next((
        attr['options'] for attr in details.get('attributes', [])
        if isinstance(attr, dict) and str(attr.get('name', '')).lower() in ('size', 'sizes', 'waist size')
        and isinstance(attr.get('options'), list) and attr['options']
    ), [])
    if not sizes and job.size_chart_id:
        sizes = job.size_chart.sizes
    size_line = 'Size guide: ' + ', '.join(map(str, sizes[:24])) if sizes else ''
    price_line = ''
    try:
        regular = Decimal(str(details['regular_price']))
        sale = Decimal(str(details['sale_price'])) if details.get('sale_price') else None
        if regular.is_finite() and regular >= 0:
            symbol = settings.SOCIAL_CURRENCY
            price_line = f'Price: {symbol} {regular:,.2f}'
            if sale is not None and sale.is_finite() and 0 <= sale < regular:
                price_line = f'Sale: {symbol} {sale:,.2f} (regular {symbol} {regular:,.2f})'
    except (KeyError, InvalidOperation):
        pass
    sold_out = details.get('manage_stock') and details.get('stock_quantity') == 0
    availability = 'Currently out of stock. Check the product page for updates.' if sold_out else ''
    brand = settings.SOCIAL_BRAND_NAME
    tags = []
    for phrase in (brand, details.get('jean_type', ''), 'DenimStyle', 'Denim', 'Jeans'):
        word = slugify(phrase).replace('-', '')
        if word and word not in tags:
            tags.append(word)
    hashtags = ' '.join('#' + tag for tag in tags[:5])
    result = {}
    for platform in SOCIAL_CHANNELS:
        link = job.woocommerce_permalink
        if link and platform == 'instagram':
            link = 'Shop details: ' + link
        lines = [title, summary, price_line, size_line, availability, link, hashtags]
        result[platform] = '\n\n'.join(line for line in lines if line)[:2200]
    return result


def check_facebook_page():
    page_id = settings.FACEBOOK_PAGE_ID
    token = settings.FACEBOOK_PAGE_ACCESS_TOKEN
    version = settings.META_GRAPH_API_VERSION
    if not all((settings.META_APP_ID, settings.META_APP_SECRET, page_id, token, version)):
        return {'ok': False, 'message': 'Set META_APP_ID, META_APP_SECRET, META_GRAPH_API_VERSION, FACEBOOK_PAGE_ID and FACEBOOK_PAGE_ACCESS_TOKEN in .env.'}, {}
    if not all((settings.META_APP_ID.isdecimal(), page_id.isdecimal(), GRAPH_API_VERSION.fullmatch(version))):
        return {'ok': False, 'message': 'Check the numeric Meta App and Page IDs and the API version (vNN.0).'}, {}
    session = FacebookSession(settings.META_APP_ID, settings.META_APP_SECRET, token, timeout=8)
    # Keep the SDK's App Secret Proof but send the access token in the authorization header.
    session.requests.params.pop('access_token', None)
    session.requests.headers['Authorization'] = f'Bearer {token}'
    try:
        with session.requests:
            api = FacebookAdsApi(session, api_version=version)
            page = api.call('GET', ('me',), params={'fields': 'id,name,instagram_business_account{id,username}'}).json()
        if str(page.get('id')) != page_id or not page.get('name'):
            return {'ok': False, 'message': 'The Page access token does not belong to the configured Facebook Page.'}, {}
        return {'ok': True, 'message': f"Meta Business SDK verified {page['name']}.", 'account': page['name']}, page.get('instagram_business_account') or {}
    except FacebookRequestError as exc:
        return {'ok': False, 'message': f'Meta Business SDK returned HTTP {exc.http_status()}. Check the app secret, Page token and granted permissions.'}, {}
    except (requests.RequestException, ValueError, AttributeError):
        return {'ok': False, 'message': 'Could not verify Facebook through the Meta Business SDK.'}, {}


def check_instagram_account(linked):
    account_id = settings.INSTAGRAM_ACCOUNT_ID
    token = settings.INSTAGRAM_ACCESS_TOKEN
    version = settings.META_GRAPH_API_VERSION
    if not token:
        if not linked.get('id') or not linked.get('username'):
            return {'ok': False, 'message': 'Link an Instagram professional account to the Facebook Page and grant Instagram access, or configure an Instagram Login token and account ID.'}
        if account_id and account_id != str(linked['id']):
            return {'ok': False, 'message': 'The linked Instagram account does not match INSTAGRAM_ACCOUNT_ID.'}
        return {'ok': True, 'message': f"Meta Business SDK verified linked Instagram @{linked['username']}.", 'account': linked['username']}
    if not account_id.isdecimal() or not GRAPH_API_VERSION.fullmatch(version):
        return {'ok': False, 'message': 'Set the numeric Instagram Login account ID and the Meta API version.'}
    try:
        response = requests.get(
            f'https://graph.instagram.com/{version}/me',
            params={'fields': 'user_id,username'}, headers={'Authorization': f'Bearer {token}'},
            timeout=8, allow_redirects=False,
        )
        if response.status_code != 200:
            return {'ok': False, 'message': f'Instagram check returned HTTP {response.status_code}. Check token, permissions and app access.'}
        result = response.json()
        returned_id = result.get('user_id')
        label = result.get('username')
        if str(returned_id) != account_id or not label:
            return {'ok': False, 'message': 'The token does not match the configured Instagram account ID.'}
        return {'ok': True, 'message': f'Account verified as {label}.', 'account': str(label)}
    except (requests.RequestException, ValueError, AttributeError):
        return {'ok': False, 'message': 'Could not verify Instagram. Check the network and token.'}


def _meta_post(path, data, *, host, version, token, proof='', file=None, publishing=False):
    if proof:
        data = {**data, 'appsecret_proof': proof}
    unconfirmed = 'Meta did not confirm the post. Check the account before sending it again.'
    try:
        response = requests.post(
            f'https://{host}/{version}/{path}', data=data,
            files={'source': file} if file else None,
            headers={'Authorization': f'Bearer {token}'}, timeout=(5, 15), allow_redirects=False,
        )
    except requests.RequestException:
        message = unconfirmed if publishing else 'Could not upload media to Meta. Try again.'
        raise MetaPublishError(message, ambiguous=publishing) from None

    try:
        result = response.json()
    except ValueError:
        result = {}
    if not isinstance(result, dict):
        result = {}

    if response.status_code == 429:
        raise MetaPublishError('Meta reached its request limit. Wait, then try again.')
    error = result.get('error')
    if 400 <= response.status_code < 500 or (200 <= response.status_code < 300 and error):
        code = error.get('code') if isinstance(error, dict) else None
        if code in (10, 190, 200):
            raise MetaPublishError('Meta rejected the token or the account’s publishing permissions.')
        if code in (324, 9004, 9007, 36001, 36003):
            raise MetaPublishError('Meta could not prepare the media. Check the photos and their availability.')
        raise MetaPublishError(f'Meta rejected the request (HTTP {response.status_code}). Check account access and media.')

    if not 200 <= response.status_code < 300 or not result.get('id'):
        message = unconfirmed if publishing else 'Meta did not return an uploaded media ID. Try again.'
        raise MetaPublishError(message, ambiguous=publishing)
    return result


def _facebook_post():
    version = settings.META_GRAPH_API_VERSION
    app_secret = settings.META_APP_SECRET
    token = settings.FACEBOOK_PAGE_ACCESS_TOKEN
    account_id = settings.FACEBOOK_PAGE_ID
    if not (account_id.isdecimal() and settings.META_APP_ID.isdecimal() and token and app_secret and GRAPH_API_VERSION.fullmatch(version)):
        raise MetaPublishError('Configure the Meta app, API version, Facebook Page ID and Page access token in .env.')
    proof = hmac.digest(app_secret.encode(), token.encode(), 'sha256').hex()
    return partial(_meta_post, host='graph.facebook.com', version=version, token=token, proof=proof)


def publish_facebook(photos, caption):
    photos = list(photos)
    if not photos or any(not content or len(content) > 10_000_000 for _, content in photos):
        raise MetaPublishError('Facebook requires a photo under 10 MB for each attachment.')

    post = _facebook_post()
    page_id = settings.FACEBOOK_PAGE_ID
    photos_path = f'{page_id}/photos'
    if len(photos) == 1:
        filename, content = photos[0]
        result = post(photos_path, {'published': 'true', 'caption': caption},
                      file=(filename, content, 'image/jpeg'), publishing=True)
        return {'id': str(result.get('post_id') or result['id'])}

    media = {}
    for index, (filename, content) in enumerate(photos):
        photo = post(photos_path, {'published': 'false'}, file=(filename, content, 'image/jpeg'))
        media[f'attached_media[{index}]'] = json.dumps({'media_fbid': str(photo['id'])})
    result = post(f'{page_id}/feed', {'message': caption, **media}, publishing=True)
    return {'id': str(result['id'])}


def _instagram_post():
    token = settings.INSTAGRAM_ACCESS_TOKEN
    version = settings.META_GRAPH_API_VERSION
    if token:
        account_id = settings.INSTAGRAM_ACCOUNT_ID
        if not (account_id.isdecimal() and GRAPH_API_VERSION.fullmatch(version)):
            raise MetaPublishError('Set the numeric Instagram Login account ID and the Meta API version.')
        return partial(_meta_post, host='graph.instagram.com', version=version, token=token), account_id

    post = _facebook_post()
    facebook, linked = check_facebook_page()
    if not facebook['ok']:
        raise MetaPublishError(facebook['message'])
    instagram = check_instagram_account(linked)
    if not instagram['ok']:
        raise MetaPublishError(instagram['message'])
    account_id = str(linked['id'])
    if not account_id.isdecimal():
        raise MetaPublishError('Meta did not return a valid linked Instagram professional account ID.')
    return post, account_id


def publish_instagram(image_urls, caption, *, is_ai_generated=True):
    image_error = 'Instagram requires 1 to 20 public HTTPS JPEG images.'
    try:
        urls = [urlsplit(url) for url in image_urls]
    except ValueError:
        raise MetaPublishError(image_error) from None
    if not 1 <= len(urls) <= 20 or any(url.scheme != 'https' or not url.hostname or url.username for url in urls):
        raise MetaPublishError(image_error)
    if len(caption) > 2200:
        raise MetaPublishError('Instagram captions can be up to 2,200 characters.')

    post, account_id = _instagram_post()
    media_path = f'{account_id}/media'
    details = {'caption': caption}
    if is_ai_generated:
        details['is_ai_generated'] = 'true'
    if len(urls) == 1:
        container = post(media_path, {'image_url': urls[0].geturl(), **details})
    else:
        children = [
            str(post(media_path, {'image_url': url.geturl(), 'is_carousel_item': 'true'})['id'])
            for url in urls
        ]
        container = post(media_path, {'media_type': 'CAROUSEL', 'children': ','.join(children), **details})
    published = post(f'{account_id}/media_publish', {'creation_id': str(container['id'])}, publishing=True)
    return {'id': str(published['id'])}
