"""Prepare campaign photos and submit them to the selected social platform."""

from urllib.parse import quote, urlsplit
from uuid import uuid4
from datetime import timedelta

import requests
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils.text import slugify
from django.utils import timezone

from .catalog_media import campaign_photos
from .langgraph_workflow import SHOT_NAMES, generated_file_path
from .models import SocialPublication
from .social import MetaPublishError, publish_facebook, publish_instagram
from .tiktok import TikTokError, tiktok_configured, upload_tiktok_photos
from .woocommerce_client import WooCommerceClient


def job_campaign_images(job):
    title = slugify(job.get_final_data().get('title', ''))[:100] or 'denim-product'
    gallery = dict(SHOT_NAMES, fabric_image_url='fabric-detail')
    return [
        (f'{title}-{shot}', generated_file_path(getattr(job, field)))
        for field, shot in gallery.items() if getattr(job, field)
    ]


def publication_data(publication):
    state, message = publication.state, publication.message
    if state == SocialPublication.State.SENDING and timezone.now() - publication.updated_at > timedelta(minutes=10):
        state = SocialPublication.State.UNKNOWN
        message = 'The response was interrupted. Check the platform for this campaign.'
    return {
        'platform': publication.platform,
        'state': state,
        'message': message,
        'updated_at': publication.updated_at.isoformat(),
    }


def public_media_base(value):
    try:
        url = urlsplit(value)
        if (url.scheme == 'https' and url.hostname and url.hostname not in ('localhost', '127.0.0.1', '::1')
                and not (url.username or url.password or url.query or url.fragment)):
            return value.rstrip('/')
    except ValueError:
        pass
    raise ValueError('Set a public HTTPS media directory for this platform.')


def campaign_urls(photos, base):
    folder = f'social_posts/{uuid4().hex}'
    urls = []
    for filename, data in photos:
        path = default_storage.save(f'{folder}/{filename}', ContentFile(data))
        urls.append(f'{base}/{quote(path)}')
    return urls


def wordpress_urls(photos):
    if not (settings.WOOCOMMERCE_URL and settings.WORDPRESS_MEDIA_USER and settings.WORDPRESS_MEDIA_APP_PASSWORD):
        raise ValueError('Configure the WordPress media upload credentials in .env.')
    client = WooCommerceClient(
        settings.WOOCOMMERCE_URL, settings.WOOCOMMERCE_CONSUMER_KEY, settings.WOOCOMMERCE_CONSUMER_SECRET,
    )
    return [
        client.upload_media(
            filename, data, settings.WORDPRESS_MEDIA_USER, settings.WORDPRESS_MEDIA_APP_PASSWORD, include_url=True,
        )[1]
        for filename, data in photos
    ]


def instagram_urls(photos):
    if settings.PUBLIC_MEDIA_BASE_URL:
        return campaign_urls(photos, public_media_base(settings.PUBLIC_MEDIA_BASE_URL))
    return wordpress_urls(photos)


def submit_campaign(job, publication, options):
    platform = publication.platform
    logo = None if platform == 'tiktok' else (options.get('logo') or job.social_logo)
    try:
        if platform == 'tiktok' and not tiktok_configured():
            raise ValueError('Set the TikTok upload access token and verified media prefix before sending.')
        frame_size = (1080, 1350) if platform == 'instagram' else None
        max_dimension = 1080 if platform == 'tiktok' else None
        photos = list(campaign_photos(job_campaign_images(job), logo, frame_size=frame_size, max_dimension=max_dimension))
        if not photos:
            raise ValueError('Generate product photos before sending a campaign.')
        if platform == 'facebook':
            result = publish_facebook(photos, options['caption'])
        elif platform == 'instagram':
            result = publish_instagram(instagram_urls(photos), options['caption'])
        else:
            base = public_media_base(settings.TIKTOK_MEDIA_BASE_URL)
            urls = wordpress_urls(photos) if settings.TIKTOK_MEDIA_SOURCE == 'wordpress' else campaign_urls(photos, base)
            publish_id = upload_tiktok_photos(
                photo_urls=urls, title=options['title'], description=options['caption'],
            )
            return SocialPublication.State.PROCESSING, publish_id, 'TikTok is preparing the photos. Check status for inbox delivery.'
    except (MetaPublishError, TikTokError) as exc:
        state = SocialPublication.State.UNKNOWN if exc.ambiguous else SocialPublication.State.FAILED
        message = f'{exc} Check the platform before sending another campaign.' if exc.ambiguous else str(exc)
        return state, '', message
    except requests.RequestException:
        return SocialPublication.State.FAILED, '', 'The media upload failed. Check your WordPress media settings.'
    except (OSError, KeyError, ValueError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else 'A campaign photo or media response could not be read.'
        return SocialPublication.State.FAILED, '', message
    finally:
        if logo and logo is job.social_logo:
            logo.close()
    return SocialPublication.State.PUBLISHED, result['id'], 'Published on the selected account.'
