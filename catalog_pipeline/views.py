"""Dashboard and catalog workflow API."""

import logging
import uuid
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote, urlsplit

import requests
from django.conf import settings
from django.http import FileResponse
from django.contrib.admin.views.decorators import staff_member_required
from django.core.files.storage import default_storage
from django.db import transaction
from django.shortcuts import render
from django.utils import timezone
from django.utils.html import format_html
from django.utils.text import slugify
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from .campaign_publishing import job_campaign_images, publication_data, submit_campaign
from .catalog_media import SOCIAL_CHANNELS, fabric_crop, mark_progress, size_chart_html, social_archive
from .langgraph_workflow import SHOT_NAMES, generated_file_path, image_filename, transcribe_chart
from .models import ImageGenerationPreference, ModelProfile, ProductJob, SizeChart, SocialPublication
from .serializers import (
    ChartPhotoSerializer, FabricCropSerializer, GalleryPhotoSerializer, GenerationOptionsSerializer, ModelProfileSerializer, ProductDetailsSerializer, ProductJobApprovalSerializer,
    ProductJobIntakeSerializer, ProductJobSerializer, ProductJobUpdateSerializer, SizeChartSerializer,
    SocialCampaignSerializer, SocialPublicationSerializer,
)
from .woocommerce_client import WooCommerceClient
from .tryon import TRYON_ROLES, model_pose_path
from .social import check_facebook_page, check_instagram_account
from .tiktok import TikTokError, tiktok_account, tiktok_configured, tiktok_post_status
from .workflow_manager import start_workflow

logger = logging.getLogger(__name__)
GALLERY_SHOTS = dict(SHOT_NAMES, fabric_image_url='fabric-detail')
IMAGE_EDIT_STATES = ('PENDING_REVIEW', 'APPROVED', 'PUBLISHED')


@api_view(['GET', 'POST'])
@permission_classes([IsAdminUser])
def image_provider(request):
    from .gemini_images import gemini_image_config_error

    gemini_error = gemini_image_config_error()
    if request.method == 'POST':
        provider = request.data.get('provider')
        if provider not in ('openrouter', 'gemini'):
            raise ValidationError('Choose OpenRouter or Gemini.')
        if provider == 'gemini' and gemini_error:
            raise ValidationError(gemini_error)
        ImageGenerationPreference.objects.update_or_create(pk=1, defaults={'provider': provider})
    return Response({
        'provider': ImageGenerationPreference.selected_provider(),
        'gemini_configured': not gemini_error,
        'gemini_model': settings.GEMINI_IMAGE_MODEL,
        'openrouter_model': settings.OPENROUTER_IMAGE_MODEL,
    })


def selected_image_provider():
    provider = ImageGenerationPreference.selected_provider()
    if provider == 'gemini':
        from .gemini_images import gemini_image_config_error

        error = gemini_image_config_error()
        if error:
            raise ValidationError(error)
    elif not settings.OPENROUTER_API_KEY:
        raise ValidationError('Set OPENROUTER_API_KEY before generating images with OpenRouter.')
    return provider


def store_image_matches(existing, url, media_ids):
    if not url:
        return False
    path = generated_file_path(url)
    media_id = media_ids.get(path)
    if media_id is not None:
        return existing.get('id') == media_id
    return Path(unquote(urlsplit(existing.get('src') or '').path)).name == Path(path).name


def merge_gallery(remote_images, replacements, original_urls, media_ids):
    images = []
    pending = dict(replacements)
    for image in remote_images:
        field = next((field for field in pending if store_image_matches(image, original_urls[field], media_ids)), None)
        if field is None:
            # A prior PUT might have applied even though the response to our request was lost.
            field = next((field for field, target in pending.items()
                          if (target.get('id') and image.get('id') == target['id'])
                          or (target.get('src') and image.get('src') == target['src'])), None)
        if field is not None:
            images.append(pending.pop(field))
        elif image.get('id'):
            images.append({'id': image['id']})
        elif image.get('src'):
            images.append({'src': image['src']})
    for field, target in pending.items():
        index = list(GALLERY_SHOTS).index(field)
        images.insert(min(index, len(images)), target)
    return images


def store_configuration_error():
    if not all((settings.WOOCOMMERCE_URL, settings.WOOCOMMERCE_CONSUMER_KEY, settings.WOOCOMMERCE_CONSUMER_SECRET)):
        return 'Set WOOCOMMERCE_URL, WOOCOMMERCE_CONSUMER_KEY and WOOCOMMERCE_CONSUMER_SECRET in .env.'
    if not _is_https_url(settings.WOOCOMMERCE_URL):
        return 'Set WOOCOMMERCE_URL to the HTTPS store address.'
    return ''


def _is_https_url(value):
    try:
        url = urlsplit(value)
        return url.scheme == 'https' and bool(url.hostname) and not (url.username or url.password or url.query or url.fragment)
    except ValueError:
        return False


def media_configuration_error():
    if settings.PUBLIC_MEDIA_BASE_URL:
        if not _is_https_url(settings.PUBLIC_MEDIA_BASE_URL) or urlsplit(settings.PUBLIC_MEDIA_BASE_URL).hostname in ('localhost', '127.0.0.1', '::1'):
            return 'Set PUBLIC_MEDIA_BASE_URL to a public HTTPS media directory.'
    elif not (settings.WORDPRESS_MEDIA_USER and settings.WORDPRESS_MEDIA_APP_PASSWORD):
        return 'Set WORDPRESS_MEDIA_USER and WORDPRESS_MEDIA_APP_PASSWORD in .env, then restart Django.'
    return ''


def check_connection(check, service):
    try:
        return check()
    except requests.HTTPError as exc:
        code = exc.response.status_code
        if code in (401, 403):
            return False, f'{service} rejected the credentials or permissions (HTTP {code}).'
        if code == 404:
            return False, f'{service} REST route was not found. Check the store URL, plugin and permalink settings.'
        return False, f'{service} returned HTTP {code}.'
    except requests.RequestException:
        return False, f'Could not connect to {service}. Check the HTTPS store URL and network.'
    except (AttributeError, ValueError):
        return False, f'{service} did not return the expected REST API response. Check for a redirect or firewall login page.'


@api_view(['POST'])
@permission_classes([IsAdminUser])
def store_connection(request):
    catalog_error = store_configuration_error()
    media_error = media_configuration_error()
    client = WooCommerceClient(
        settings.WOOCOMMERCE_URL, settings.WOOCOMMERCE_CONSUMER_KEY, settings.WOOCOMMERCE_CONSUMER_SECRET,
    )

    def check_catalog():
        client.check_product_access()
        return True, 'WooCommerce product read access verified. Use a Read/Write key when publishing.'

    def check_media():
        if not client.check_media_access(settings.WORDPRESS_MEDIA_USER, settings.WORDPRESS_MEDIA_APP_PASSWORD):
            return False, 'WordPress did not report media upload permission for this user.'
        return True, 'WordPress credentials verified. This user has media upload permission.'

    catalog_ok, catalog_message = (False, catalog_error) if catalog_error else check_connection(check_catalog, 'WooCommerce')
    if media_error or not _is_https_url(settings.WOOCOMMERCE_URL):
        media_ok, media_message = False, media_error or 'Set WOOCOMMERCE_URL to the HTTPS store address first.'
    elif settings.PUBLIC_MEDIA_BASE_URL:
        media_ok, media_message = None, 'Public media URL configured. WooCommerce checks image access during publish.'
    else:
        media_ok, media_message = check_connection(check_media, 'WordPress')
    return Response({
        'woocommerce': {'ok': catalog_ok, 'message': catalog_message},
        'media': {'ok': media_ok, 'message': media_message},
    })


@api_view(['POST'])
@permission_classes([IsAdminUser])
def social_connections(request):
    facebook, linked = check_facebook_page()
    return Response({
        'facebook': facebook,
        'instagram': check_instagram_account(linked),
        'tiktok': tiktok_connection(),
    })


def tiktok_connection():
    if not tiktok_configured():
        return {'ok': False, 'message': 'Set a TikTok upload access token and verified HTTPS media prefix in .env.'}
    try:
        account = tiktok_account()
    except TikTokError as exc:
        return {'ok': False, 'message': str(exc)}
    nickname = account['nickname']
    return {'ok': True, 'message': f'TikTok account verified as {nickname}.', 'account': nickname}


@api_view(['GET'])
@permission_classes([IsAdminUser])
def social_tiktok_account(request):
    return Response(tiktok_connection())


class ModelProfileViewSet(viewsets.ModelViewSet):
    queryset = ModelProfile.objects.all()
    serializer_class = ModelProfileSerializer
    permission_classes = (IsAdminUser,)
    parser_classes = (MultiPartParser, FormParser)


class SizeChartViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    queryset = SizeChart.objects.all()
    serializer_class = SizeChartSerializer
    permission_classes = (IsAdminUser,)
    parser_classes = (JSONParser, MultiPartParser, FormParser)
    search_fields = ('name',)

    @action(detail=False, methods=['POST'], parser_classes=(MultiPartParser,))
    def read_photo(self, request):
        serializer = ChartPhotoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not settings.OPENROUTER_API_KEY:
            raise ValidationError('Set OPENROUTER_API_KEY to read a chart photo.')
        try:
            table = transcribe_chart(serializer.validated_data['image'])
        except requests.RequestException:
            return Response({'detail': 'The chart model could not be reached or rejected the request.'}, status=502)
        except (OSError, ValueError, IndexError, KeyError, TypeError):
            raise ValidationError('The photo did not produce a valid table. Use a clearer photo or enter the measurements manually.')
        return Response({'measurements': table})


class ProductJobViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    queryset = ProductJob.objects.select_related('size_chart').all()
    serializer_class = ProductJobSerializer
    lookup_field = 'job_id'
    permission_classes = (IsAdminUser,)
    parser_classes = (JSONParser, MultiPartParser, FormParser)
    search_fields = ('structured_data__title', 'structured_data__sku', 'reviewer_edits__title', 'reviewer_edits__sku')

    @action(detail=False, methods=['POST'], parser_classes=(MultiPartParser, FormParser))
    def create_job(self, request):
        serializer = ProductJobIntakeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        profile = data.get('model_profile_id')
        job_id = str(uuid.uuid4())
        saved = []
        try:
            for photo in data['garment_images']:
                suffix = {'JPEG': '.jpg', 'PNG': '.png', 'WEBP': '.webp'}[photo.image.format]
                saved.append(default_storage.save(f'garments/{job_id}/{len(saved) + 1}{suffix}', photo))
            fabric = data.get('fabric_reference')
            fabric_path = ''
            if fabric:
                fabric_path = default_storage.save(f'fabric_references/{job_id}/{fabric.name}', fabric)
            job = ProductJob.objects.create(
                job_id=job_id, raw_product_text=data['raw_product_text'],
                model_profile=profile, model_reference=profile.generation_profile() if profile else {},
                garment_images=saved, fabric_reference=fabric_path, status='PROCESSING',
                workflow_progress={'step': 'extract', 'label': 'Waiting to extract product details', 'model': ''},
            )
        except Exception:
            for path in saved:
                default_storage.delete(path)
            if 'fabric_path' in locals() and fabric_path:
                default_storage.delete(fabric_path)
            raise

        transaction.on_commit(lambda: start_workflow(job, 'extract'))
        return Response(self.get_serializer(job).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['POST'])
    def retry_extraction(self, request, job_id=None):
        job = self.get_object()
        if job.structured_data or not ProductJob.objects.filter(pk=job.pk, status='FAILED', updated_at=job.updated_at).update(
            status='PROCESSING', error_message='', updated_at=timezone.now(),
        ):
            return Response({'detail': 'Only failed extraction jobs can be retried here.'}, status=409)
        job.refresh_from_db()
        transaction.on_commit(lambda: start_workflow(job, 'extract'))
        return Response(self.get_serializer(job).data, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=['POST'])
    def edit_job(self, request, job_id=None):
        job = self.get_object()
        serializer = ProductJobUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if job.status not in ('PENDING_DETAILS', 'PENDING_REVIEW', 'APPROVED', 'PUBLISHED'):
            return Response({'detail': 'This job cannot be edited in its current stage.'}, status=409)
        edits = job.reviewer_edits | serializer.validated_data['reviewer_edits']
        changes = {'reviewer_edits': edits, 'updated_at': timezone.now()}
        if 'size_chart_id' in serializer.validated_data:
            changes['size_chart'] = serializer.validated_data['size_chart_id']
        if 'show_size_chart_in_description' in serializer.validated_data:
            changes['show_size_chart_in_description'] = serializer.validated_data['show_size_chart_in_description']
        if job.status == 'PENDING_DETAILS' and 'generation_notes' in serializer.validated_data:
            changes['generation_notes'] = serializer.validated_data['generation_notes']
        if job.status == 'PUBLISHED' and (
            edits != job.reviewer_edits or changes.get('size_chart', job.size_chart) != job.size_chart
            or changes.get('show_size_chart_in_description', job.show_size_chart_in_description) != job.show_size_chart_in_description
        ):
            changes['listing_dirty'] = True
        if not ProductJob.objects.filter(pk=job.pk, status=job.status, updated_at=job.updated_at).update(
            **changes,
        ):
            return Response({'detail': 'The job changed. Reload and try again.'}, status=409)
        job.refresh_from_db()
        return Response(self.get_serializer(job).data)

    @action(detail=True, methods=['POST'])
    def generate(self, request, job_id=None):
        job = self.get_object()
        options = GenerationOptionsSerializer(data=request.data)
        options.is_valid(raise_exception=True)
        if job.status not in ('PENDING_DETAILS', 'FAILED') or not job.structured_data:
            return Response({'detail': 'Review the extracted product details before generating.'}, status=409)
        image_provider = selected_image_provider()
        if image_provider == 'openrouter' and settings.MODEL_TRYON_PROVIDER == 'fashn':
            if not settings.FASHN_API_KEY:
                raise ValidationError('Set FASHN_API_KEY before starting virtual try-on.')
            if any(not model_pose_path(job.model_reference, shot) for shot in TRYON_ROLES):
                raise ValidationError('Choose a model profile with both front and side pose photos for virtual try-on.')
        model_reference = job.model_reference
        if (image_provider == 'gemini' or settings.MODEL_TRYON_PROVIDER == 'openrouter_masked') and job.model_profile_id:
            if job.status == 'PENDING_DETAILS' and any(not model_pose_path(model_reference, shot) for shot in TRYON_ROLES):
                model_reference = job.model_profile.generation_profile()
            if any(not model_pose_path(model_reference, shot) for shot in TRYON_ROLES):
                raise ValidationError('In Model profiles, add a front and side photo of the same model.')
        details = job.get_final_data()
        if not all(isinstance(details.get(key), str) and details[key].strip() for key in ('title', 'jean_type')):
            raise ValidationError('Add a product name and jean type first.')
        if not job.garment_images or any(not default_storage.exists(path) for path in job.garment_images):
            raise ValidationError('The garment photos are missing. Create a job with valid photos.')
        notes = options.validated_data.get('generation_notes', job.generation_notes)
        if job.status == 'FAILED' and notes != job.generation_notes:
            raise ValidationError('Retry uses the original generation notes to keep all shots consistent.')
        if not ProductJob.objects.filter(pk=job.pk, status=job.status, updated_at=job.updated_at).update(
            status='PROCESSING', error_message='', updated_at=timezone.now(),
            generation_notes=notes,
            image_provider=image_provider,
            model_reference=model_reference,
            workflow_progress={**job.workflow_progress, 'step': 'references', 'label': 'Preparing the garment and model references', 'model': ''},
        ):
            return Response({'detail': 'The job changed. Reload and try again.'}, status=409)
        job.refresh_from_db()
        transaction.on_commit(lambda: start_workflow(job, 'generate'))
        return Response(self.get_serializer(job).data, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=['POST'])
    def regenerate_shot(self, request, job_id=None):
        job = self.get_object()
        field = request.data.get('shot')
        if not isinstance(field, str) or field not in SHOT_NAMES:
            raise ValidationError('Select the model front, model side or garment flat lay shot.')
        if job.status not in IMAGE_EDIT_STATES:
            return Response({'detail': 'Open a reviewed or published product to redo a shot.'}, status=409)
        image_provider = selected_image_provider()
        if not job.garment_images or any(not default_storage.exists(path) for path in job.garment_images):
            raise ValidationError('The original garment photos are missing.')
        poses = ('image_1_url', 'image_3_url') if field == 'image_1_url' else (field,) if field == 'image_3_url' else ()
        if poses and image_provider == 'openrouter' and settings.MODEL_TRYON_PROVIDER == 'fashn':
            if not settings.FASHN_API_KEY or any(not model_pose_path(job.model_reference, pose) for pose in poses):
                raise ValidationError('FASHN requires its API key and the original model photos for the requested views.')
        if poses and (image_provider == 'gemini' or settings.MODEL_TRYON_PROVIDER == 'openrouter_masked') and job.model_profile_id:
            if any(not model_pose_path(job.model_reference, pose) for pose in poses):
                raise ValidationError('This job is missing an original pose photo. Use a job created with both model photos.')
        if field == 'image_3_url' and not job.image_1_url:
            raise ValidationError('Generate the model front image first.')
        if not ProductJob.objects.filter(pk=job.pk, status=job.status, updated_at=job.updated_at).update(
            status='PROCESSING', updated_at=timezone.now(), error_message='',
            image_provider=image_provider,
            workflow_progress={**job.workflow_progress, 'step': 'references', 'label': 'Preparing to redo the selected shot', 'model': ''},
        ):
            return Response({'detail': 'The job changed. Reload and try again.'}, status=409)
        job.refresh_from_db()
        transaction.on_commit(lambda: start_workflow(job, f'regenerate:{field}'))
        return Response(self.get_serializer(job).data, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=['POST'])
    def replace_photo(self, request, job_id=None):
        job = self.get_object()
        if job.status not in IMAGE_EDIT_STATES:
            return Response({'detail': 'Open a reviewed or published product to replace a photo.'}, status=409)
        serializer = GalleryPhotoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        field = serializer.validated_data['shot']
        image = serializer.validated_data['image']
        title = job.get_final_data().get('title', '')
        path = default_storage.save(
            image_filename(job.job_id, title, GALLERY_SHOTS[field], Path(image.name).suffix.lstrip('.')), image,
        )
        return self._save_gallery_photo(job, field, default_storage.url(path))

    def _save_gallery_photo(self, job, field, url):
        changes = {field: url, **job.pending_gallery_change(field)}
        if not ProductJob.objects.filter(pk=job.pk, status=job.status, updated_at=job.updated_at).update(
            **changes, updated_at=timezone.now(),
        ):
            default_storage.delete(generated_file_path(url))
            return Response({'detail': 'The job changed. Reload and try again.'}, status=409)
        job.refresh_from_db()
        return Response(self.get_serializer(job).data)

    @action(detail=True, methods=['POST'])
    def crop_fabric(self, request, job_id=None):
        job = self.get_object()
        if job.status not in IMAGE_EDIT_STATES:
            return Response({'detail': 'Open a reviewed or published product to crop its fabric.'}, status=409)
        serializer = FabricCropSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        source = job.fabric_reference.name or next(iter(job.garment_images), '')
        destination = image_filename(job.job_id, job.get_final_data().get('title', ''), 'fabric-detail')
        try:
            url = fabric_crop(source, destination, **serializer.validated_data)
        except (ValueError, OSError) as exc:
            raise ValidationError('The original fabric photo could not be read.') from exc
        return self._save_gallery_photo(job, 'fabric_image_url', url)

    @action(detail=True, methods=['POST'])
    def remove_deleted_listing(self, request, job_id=None):
        job = self.get_object()
        if job.status != 'PUBLISHED' or not job.woocommerce_product_id:
            return Response({'detail': 'Only a product previously published from this dashboard can be removed here.'}, status=409)
        configuration_error = store_configuration_error()
        if configuration_error:
            raise ValidationError(configuration_error)
        if not job.woocommerce_permalink or urlsplit(job.woocommerce_permalink).hostname != urlsplit(settings.WOOCOMMERCE_URL).hostname:
            raise ValidationError('The listing does not match the configured store.')

        client = WooCommerceClient(
            settings.WOOCOMMERCE_URL, settings.WOOCOMMERCE_CONSUMER_KEY, settings.WOOCOMMERCE_CONSUMER_SECRET,
        )
        try:
            result = client.get_product(job.woocommerce_product_id)
            deleted = result.get('status') == 'trash' and any(
                meta.get('key') == '_denim_job_id' and meta.get('value') == job.job_id for meta in result.get('meta_data', [])
            )
        except requests.HTTPError as exc:
            deleted = False
            if exc.response.status_code == 404:
                try:
                    deleted = exc.response.json().get('code') == 'woocommerce_rest_product_invalid_id'
                except (ValueError, AttributeError):
                    pass
        except (requests.RequestException, ValueError, AttributeError):
            return Response({'detail': 'Could not verify deletion in WooCommerce. The dashboard entry was kept.'}, status=502)
        if not deleted:
            return Response({'detail': 'WooCommerce has not confirmed this listing was deleted. The dashboard entry was kept.'}, status=409)
        count, _ = ProductJob.objects.filter(pk=job.pk, status='PUBLISHED', updated_at=job.updated_at).delete()
        if not count:
            return Response({'detail': 'The job changed. Reload and try again.'}, status=409)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['POST'], parser_classes=(MultiPartParser, FormParser))
    def export_social(self, request, job_id=None):
        job = self.get_object()
        if job.status not in ('PENDING_REVIEW', 'APPROVED', 'PUBLISHED'):
            return Response({'detail': 'Finish generation before preparing a campaign.'}, status=409)
        serializer = SocialCampaignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        captions = {platform: serializer.validated_data[platform] for platform in SOCIAL_CHANNELS}
        uploaded_logo = serializer.validated_data.get('logo')
        logo = uploaded_logo or job.social_logo
        if not logo:
            raise ValidationError('Upload a PNG watermark logo.')
        title = slugify(job.get_final_data().get('title', ''))[:100] or 'denim-product'
        images = job_campaign_images(job)
        if not images:
            raise ValidationError('No product images are available to export.')
        try:
            output = social_archive(images, logo, captions)
        except (OSError, ValueError) as exc:
            raise ValidationError(str(exc) if isinstance(exc, ValueError) else 'A product image or logo could not be read.') from exc
        finally:
            if not uploaded_logo:
                job.social_logo.close()
        if uploaded_logo:
            uploaded_logo.seek(0)
            job.social_logo = uploaded_logo
        job.social_draft = captions
        job.save(update_fields=['social_draft', 'social_logo'])
        return FileResponse(output, as_attachment=True, filename=f'{title}-social-campaign.zip')

    @action(detail=True, methods=['POST'], parser_classes=(JSONParser, MultiPartParser, FormParser))
    def publish_social(self, request, job_id=None):
        job = self.get_object()
        if job.status not in IMAGE_EDIT_STATES:
            return Response({'detail': 'Finish generation before publishing a campaign.'}, status=409)
        serializer = SocialPublicationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        options = serializer.validated_data
        platform = options['platform']
        publication, created = SocialPublication.objects.get_or_create(job=job, platform=platform)
        states = SocialPublication.State
        if not created:
            if not SocialPublication.objects.filter(pk=publication.pk, state=states.FAILED).update(
                state=states.SENDING, remote_id='', message='', updated_at=timezone.now(),
            ):
                publication.refresh_from_db()
                return Response(publication_data(publication))
        publication.state, publication.remote_id, publication.message = submit_campaign(job, publication, options)
        publication.save(update_fields=['state', 'remote_id', 'message', 'updated_at'])
        if publication.state in (states.PROCESSING, states.PUBLISHED):
            with transaction.atomic():
                saved_job = ProductJob.objects.select_for_update().get(pk=job.pk)
                saved_job.social_draft = {**saved_job.social_draft, platform: options['caption']}
                if platform != 'tiktok' and options.get('logo'):
                    options['logo'].seek(0)
                    saved_job.social_logo = options['logo']
                saved_job.save(update_fields=['social_draft', 'social_logo'])
        return Response(publication_data(publication))

    @action(detail=True, methods=['POST'])
    def refresh_social(self, request, job_id=None):
        job = self.get_object()
        publication = job.social_publications.filter(platform='tiktok').first()
        if not publication or not publication.remote_id:
            return Response({'detail': 'Send a campaign to TikTok before checking its status.'}, status=409)
        states = SocialPublication.State
        if publication.state not in (states.PROCESSING, states.DRAFT):
            return Response(publication_data(publication))
        try:
            result = tiktok_post_status(publication.remote_id)
        except TikTokError as exc:
            return Response({'detail': str(exc)}, status=502)
        transitions = {
            'SEND_TO_USER_INBOX': (states.DRAFT, 'Open the TikTok inbox to review and publish the draft.'),
            'PUBLISH_COMPLETE': (states.PUBLISHED, 'TikTok confirmed the post was published.'),
            'FAILED': (
                states.FAILED if result.get('retry_allowed') else states.BLOCKED,
                result.get('fail_reason') or 'TikTok could not prepare the draft.',
            ),
        }
        if result['status'] in transitions:
            state, message = transitions[result['status']]
            SocialPublication.objects.filter(pk=publication.pk, state=publication.state, remote_id=publication.remote_id).update(
                state=state, message=message, updated_at=timezone.now(),
            )
            publication.refresh_from_db()
        return Response(publication_data(publication))

    @action(detail=True, methods=['POST'])
    def approve_publish(self, request, job_id=None):
        job = self.get_object()
        serializer = ProductJobApprovalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        if job.status == 'PUBLISHED':
            return Response(self.get_serializer(job).data)
        if job.status not in ('PENDING_REVIEW', 'APPROVED'):
            return Response({'detail': 'The job must finish image generation before approval.'}, status=409)
        if not serializer.validated_data['publish_to_woocommerce']:
            if not ProductJob.objects.filter(pk=job.pk, status=job.status, updated_at=job.updated_at).update(
                status='APPROVED', updated_at=timezone.now(),
            ):
                return Response({'detail': 'The job changed. Reload and try again.'}, status=409)
            job.refresh_from_db()
            return Response(self.get_serializer(job).data)

        return self._sync_listing(job)

    @action(detail=True, methods=['POST'])
    def update_listing(self, request, job_id=None):
        job = self.get_object()
        if job.status != 'PUBLISHED' or not job.woocommerce_product_id:
            return Response({'detail': 'Publish this product before updating its WooCommerce listing.'}, status=409)
        if not job.listing_dirty:
            return Response(self.get_serializer(job).data)
        return self._sync_listing(job, updating=True)

    def _sync_listing(self, job, *, updating=False):
        details = ProductDetailsSerializer(data=job.get_final_data())
        details.is_valid(raise_exception=True)
        product = details.validated_data
        if not product.get('title') or not product.get('regular_price'):
            raise ValidationError('A product name and regular price are required to publish.')
        if product.get('sale_price') and Decimal(product['sale_price']) >= Decimal(product['regular_price']):
            raise ValidationError('The sale price must be below the regular price.')
        configuration_error = store_configuration_error()
        if not updating or job.size_chart or job.gallery_replacements:
            configuration_error = configuration_error or media_configuration_error()
        if configuration_error:
            raise ValidationError(configuration_error)

        if not updating and not all(getattr(job, field) for field in SHOT_NAMES):
            raise ValidationError('Three product images are required before publishing.')
        image_fields = job.gallery_replacements if updating else GALLERY_SHOTS
        image_urls = {field: getattr(job, field) for field in image_fields if getattr(job, field)}
        if not ProductJob.objects.filter(pk=job.pk, status=job.status, updated_at=job.updated_at).update(
            status='PUBLISHING', error_message='', updated_at=timezone.now(),
            workflow_progress={**job.workflow_progress, 'step': 'publish', 'label': 'Preparing WooCommerce media', 'model': ''},
        ):
            return Response({'detail': 'The job changed. Reload and try again.'}, status=409)

        previous_status = job.status
        try:
            client = WooCommerceClient(
                settings.WOOCOMMERCE_URL, settings.WOOCOMMERCE_CONSUMER_KEY,
                settings.WOOCOMMERCE_CONSUMER_SECRET,
            )
            if updating:
                remote = client.get_product(job.woocommerce_product_id)
                if not any(meta.get('key') == '_denim_job_id' and meta.get('value') == job.job_id for meta in remote.get('meta_data', [])):
                    raise ValueError('The saved WooCommerce product does not belong to this job on the configured store.')
                if job.gallery_replacements and not isinstance(remote.get('images'), list):
                    raise ValueError('Could not read the current WooCommerce gallery. The gallery was kept.')
            images = {}
            for index, (field, url) in enumerate(image_urls.items(), 1):
                mark_progress(job.job_id, 'publish', f'Preparing gallery image {index} of {len(image_urls)}')
                path = generated_file_path(url)
                if settings.PUBLIC_MEDIA_BASE_URL:
                    images[field] = {'src': f"{settings.PUBLIC_MEDIA_BASE_URL.rstrip('/')}/{quote(path)}"}
                    continue
                media_id = job.store_media_ids.get(path)
                if not media_id:
                    with default_storage.open(path, 'rb') as photo:
                        media_id = client.upload_media(
                            Path(path).name, photo, settings.WORDPRESS_MEDIA_USER,
                            settings.WORDPRESS_MEDIA_APP_PASSWORD,
                        )
                    job.store_media_ids[path] = media_id
                    job.save(update_fields=['store_media_ids'])
                images[field] = {'id': media_id}

            chart_fields = {'denim_size_chart_image_id': '', 'denim_size_chart_image_url': ''}
            description = product.get('description', '')
            if job.size_chart:
                mark_progress(job.job_id, 'publish', 'Preparing the assigned size chart')
                chart = job.size_chart
                if settings.PUBLIC_MEDIA_BASE_URL:
                    chart_url = f"{settings.PUBLIC_MEDIA_BASE_URL.rstrip('/')}/{quote(chart.image.name)}"
                else:
                    store_url = settings.WOOCOMMERCE_URL.rstrip('/')
                    if chart.store_url != store_url or not chart.store_attachment_id or not chart.store_media_url:
                        with chart.image.open('rb') as photo:
                            chart.store_attachment_id, chart.store_media_url = client.upload_media(
                                Path(chart.image.name).name, photo, settings.WORDPRESS_MEDIA_USER,
                                settings.WORDPRESS_MEDIA_APP_PASSWORD, include_url=True,
                            )
                        chart.store_url = store_url
                        chart.save(update_fields=['store_attachment_id', 'store_media_url', 'store_url'])
                    chart_url = chart.store_media_url
                    chart_fields['denim_size_chart_image_id'] = chart.store_attachment_id
                if not _is_https_url(chart_url):
                    raise ValueError('The size chart image must have an HTTPS WordPress media URL.')
                chart_fields['denim_size_chart_image_url'] = chart_url
                if job.show_size_chart_in_description:
                    if chart.table_rows:
                        description += str(size_chart_html(chart.name, chart.table_rows))
                    else:
                        description += str(format_html(
                            '<h2>Size chart</h2><p><img src="{}" alt="{}" loading="lazy"></p>', chart_url, chart.name,
                        ))

            stock = product.get('stock_quantity')
            attributes = [attribute for attribute in product.get('attributes', []) if attribute.get('options')]
            if job.size_chart and job.size_chart.sizes and not any(
                str(attribute.get('name', '')).lower() in ('size', 'sizes', 'waist size') for attribute in attributes
            ):
                attributes.insert(0, {'name': 'Size', 'options': job.size_chart.sizes, 'visible': True})
            payload = {
                'name': product['title'], 'type': 'simple', 'status': 'publish',
                'sku': product.get('sku') or f"DENIM-{job.job_id[:8].upper()}",
                'regular_price': product['regular_price'], 'sale_price': product.get('sale_price') or '',
                'description': description,
                'short_description': product.get('short_description', ''),
                'manage_stock': product.get('manage_stock', False) and stock is not None,
                'attributes': attributes,
                'meta_data': [
                    {'key': '_denim_job_id', 'value': job.job_id},
                    {'key': '_seo_title', 'value': product.get('meta_title', '')},
                    {'key': '_seo_description', 'value': product.get('meta_description', '')},
                    *[{'key': key, 'value': value} for key, value in chart_fields.items() if value or updating],
                ],
            }
            if payload['manage_stock']:
                payload['stock_quantity'] = stock
            if updating:
                mark_progress(job.job_id, 'publish', 'Updating the existing WooCommerce listing')
                for field in ('type', 'status'):
                    payload.pop(field)
                meta_ids = {meta['key']: meta['id'] for meta in remote.get('meta_data', []) if 'key' in meta and 'id' in meta}
                for meta in payload['meta_data']:
                    if meta['key'] in meta_ids:
                        meta['id'] = meta_ids[meta['key']]
                if images:
                    payload['images'] = merge_gallery(remote.get('images', []), images, job.gallery_replacements, job.store_media_ids)
                published = client.update_product(job.woocommerce_product_id, payload)
            else:
                mark_progress(job.job_id, 'publish', 'Checking the SKU and sending the WooCommerce listing')
                payload['images'] = list(images.values())
                existing = client.get_product_by_sku(payload['sku'])
                if existing and not any(meta['key'] == '_denim_job_id' and meta['value'] == job.job_id for meta in existing.get('meta_data', [])):
                    raise ValueError('This SKU is already assigned to another WooCommerce product.')
                published = existing or client.create_product(payload)
            job.woocommerce_product_id = published['id']
            job.woocommerce_permalink = published.get('permalink', '')
            job.status = 'PUBLISHED'
            job.error_message = ''
            job.listing_dirty = False
            job.gallery_replacements = {}
            job.save(update_fields=[
                'woocommerce_product_id', 'woocommerce_permalink', 'status', 'error_message', 'listing_dirty', 'gallery_replacements', 'updated_at',
            ])
        except (requests.RequestException, OSError, ValueError, KeyError) as exc:
            logger.warning('WooCommerce publish failed for %s (%s)', job.job_id, type(exc).__name__)
            job.status = previous_status
            job.error_message = str(exc) if isinstance(exc, ValueError) else 'Publish failed. Check the store connection, credentials and media configuration.'
            job.save(update_fields=['status', 'error_message', 'updated_at'])
            return Response({'detail': job.error_message}, status=502)
        return Response(self.get_serializer(job).data)


@staff_member_required
def dashboard(request):
    image_model = settings.OPENROUTER_IMAGE_MODEL
    tryon_model = {
        'fashn': 'FASHN Try-On Max',
        'openrouter_masked': 'OpenRouter · Full-frame photo edit',
    }.get(settings.MODEL_TRYON_PROVIDER, image_model)
    if ImageGenerationPreference.selected_provider() == 'gemini':
        image_model = settings.GEMINI_IMAGE_MODEL
        tryon_model = f'Gemini · {image_model}'
    return render(request, 'pipeline_dashboard.html', {
        'text_model': settings.OPENROUTER_PRODUCT_VISION_MODEL, 'image_model': image_model, 'tryon_model': tryon_model,
    })
