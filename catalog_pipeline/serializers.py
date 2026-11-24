from decimal import Decimal, InvalidOperation

from uuid import uuid4
from math import isfinite

from rest_framework import serializers
from django.core.files.storage import default_storage

from .models import MODEL_REFERENCE_FIELDS, ModelProfile, ProductJob, SizeChart


class PhotoField(serializers.ImageField):
    def to_internal_value(self, data):
        if getattr(data, 'size', 0) > 10 * 1024 * 1024:
            raise serializers.ValidationError('Keep each photo under 10 MB.')
        photo = super().to_internal_value(data)
        if photo.image.format not in ('JPEG', 'PNG', 'WEBP'):
            raise serializers.ValidationError('Upload a JPEG, PNG, or WebP photo.')
        suffix = {'JPEG': '.jpg', 'PNG': '.png', 'WEBP': '.webp'}[photo.image.format]
        photo.name = f'{photo.name.rsplit(".", 1)[0][:80]}{suffix}'
        return photo


class ModelProfileSerializer(serializers.ModelSerializer):
    reference_image = PhotoField()
    side_reference_image = PhotoField(required=False, allow_null=True)
    face_reference_image = PhotoField(required=False, allow_null=True)
    three_quarter_reference_image = PhotoField(required=False, allow_null=True)
    detail_reference_image = PhotoField(required=False, allow_null=True)
    bulk_reference_images = serializers.ListField(child=PhotoField(), max_length=3, min_length=1, required=False, write_only=True)
    bulk_references = serializers.SerializerMethodField()
    garment_masks = serializers.JSONField(required=False)

    class Meta:
        model = ModelProfile
        fields = ['id', 'name', 'description', *MODEL_REFERENCE_FIELDS, 'bulk_reference_images', 'bulk_references', 'garment_masks', 'created_at']
        read_only_fields = ['created_at']

    def get_bulk_references(self, obj):
        return [default_storage.url(path) for path in obj.additional_reference_paths]

    def validate_garment_masks(self, value):
        if not isinstance(value, dict) or set(value) - {'front', 'side'}:
            raise serializers.ValidationError('Choose a front or side outline.')
        for points in value.values():
            if not isinstance(points, list) or not 3 <= len(points) <= 40 or any(
                not isinstance(point, list) or len(point) != 2
                or any(type(v) not in (int, float) or not 0 <= v <= 1 or not isfinite(v) for v in point)
                for point in points
            ):
                raise serializers.ValidationError('Draw between 3 and 40 points around the trousers.')
            area = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]))
            if abs(area) < .02:
                raise serializers.ValidationError('Trace a larger area around the trousers without crossing the outline.')
        return value

    def validate(self, attrs):
        masks = attrs.get('garment_masks', dict(self.instance.garment_masks) if self.instance else {})
        if 'garment_masks' not in attrs:
            # An outline is only valid for the exact image on which it was made.
            for field, view in (('reference_image', 'front'), ('side_reference_image', 'side')):
                if field in attrs:
                    masks.pop(view, None)
        for field, view in (('reference_image', 'front'), ('side_reference_image', 'side')):
            if view in masks and not attrs.get(field, getattr(self.instance, field, None)):
                raise serializers.ValidationError(f'Upload the {view} pose photo before outlining it.')
        attrs['garment_masks'] = masks
        return attrs

    def save_bulk_references(self, validated_data, save_profile):
        uploads = validated_data.pop('bulk_reference_images', None)
        if not uploads:
            return save_profile(validated_data)
        paths = []
        folder = f'model_profiles/bulk/{uuid4().hex}'
        try:
            for image in uploads:
                paths.append(default_storage.save(f'{folder}/{image.name}', image))
            return save_profile({**validated_data, 'additional_reference_paths': paths})
        except Exception:
            for path in paths:
                default_storage.delete(path)
            raise

    def create(self, validated_data):
        return self.save_bulk_references(validated_data, super().create)

    def update(self, instance, validated_data):
        update_profile = super().update
        return self.save_bulk_references(validated_data, lambda data: update_profile(instance, data))


class SizeChartSerializer(serializers.ModelSerializer):
    image = PhotoField(required=False)
    measurements = serializers.CharField(write_only=True, required=False, max_length=5000)
    listed_sizes = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=300)
    sizes = serializers.JSONField(read_only=True)

    class Meta:
        model = SizeChart
        fields = ['id', 'name', 'image', 'measurements', 'listed_sizes', 'sizes', 'table_rows']
        read_only_fields = ['table_rows']

    def validate(self, attrs):
        from .catalog_media import read_chart_table, size_chart_png

        image = attrs.get('image')
        table = attrs.pop('measurements', None)
        labels = attrs.pop('listed_sizes', '')
        if bool(image) == bool(table):
            raise serializers.ValidationError('Upload one chart image or enter a measurement table.')
        if table:
            try:
                chart_rows = read_chart_table(table)
                if any(cell.strip() == '?' for row in chart_rows for cell in row):
                    raise ValueError('Replace each ? with the verified measurement or a dash if it does not apply.')
                attrs['table_rows'] = chart_rows
                attrs['image'] = size_chart_png(attrs['name'], chart_rows)
                header, *rows = chart_rows
                size_column = next((index for index, cell in enumerate(header) if cell.strip().lower() in ('size', 'label', 'waist size')), None)
                if not labels and size_column is not None:
                    attrs['sizes'] = list(dict.fromkeys(row[size_column].strip() for row in rows))
            except ValueError as exc:
                raise serializers.ValidationError({'measurements': str(exc)}) from exc
        if labels:
            sizes = list(dict.fromkeys(size.strip() for size in labels.split(',') if size.strip()))
            if len(sizes) > 24 or any(len(size) > 24 for size in sizes):
                raise serializers.ValidationError({'listed_sizes': 'Use up to 24 short size labels.'})
            attrs['sizes'] = sizes
        return attrs


class ProductJobSerializer(serializers.ModelSerializer):
    final_data = serializers.JSONField(source='get_final_data', read_only=True)
    reference_photos = serializers.SerializerMethodField()
    suggested_captions = serializers.SerializerMethodField()
    social_publications = serializers.SerializerMethodField()
    social_logo_configured = serializers.SerializerMethodField()

    class Meta:
        model = ProductJob
        fields = [
            'id', 'job_id', 'status', 'raw_product_text', 'extracted_jean_type',
            'model_profile', 'garment_images', 'reference_photos', 'image_1_url', 'image_2_url', 'image_3_url',
            'image_1_notes', 'image_2_notes', 'image_3_notes', 'structured_data',
            'workflow_progress', 'generation_notes', 'fabric_image_url', 'size_chart', 'show_size_chart_in_description',
            'listing_dirty', 'gallery_replacements', 'social_draft', 'suggested_captions',
            'social_publications', 'social_logo_configured',
            'reviewer_edits', 'final_data', 'woocommerce_product_id',
            'woocommerce_permalink', 'error_message', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_suggested_captions(self, obj):
        view = self.context.get('view')
        if view is not None and view.action == 'list':
            return {}
        from .social import suggested_captions
        return suggested_captions(obj)

    def get_social_publications(self, obj):
        view = self.context.get('view')
        if view is not None and view.action == 'list':
            return {}
        from .campaign_publishing import publication_data
        return {item.platform: publication_data(item) for item in obj.social_publications.all()}

    def get_social_logo_configured(self, obj):
        return bool(obj.social_logo)

    def get_reference_photos(self, obj):
        if self.context.get('view') and self.context['view'].action == 'list':
            return {}
        return {
            'garments': [default_storage.url(path) for path in obj.garment_images if path.startswith('garments/')],
            'model': [default_storage.url(path) for path in obj.model_reference.get('photos', []) if path],
            'fabric': obj.fabric_reference.url if obj.fabric_reference else '',
        }


class ProductJobIntakeSerializer(serializers.Serializer):
    raw_product_text = serializers.CharField(max_length=20000)
    model_profile_id = serializers.PrimaryKeyRelatedField(
        queryset=ModelProfile.objects.all(), required=False, allow_null=True,
    )
    garment_images = serializers.ListField(child=PhotoField(), min_length=1, max_length=7)
    fabric_reference = PhotoField(required=False)


class ProductDetailsSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=200, required=False)
    jean_type = serializers.CharField(max_length=120, required=False)
    sku = serializers.CharField(max_length=100, required=False, allow_blank=True)
    regular_price = serializers.CharField(required=False, allow_null=True, allow_blank=True, max_length=32)
    sale_price = serializers.CharField(required=False, allow_null=True, allow_blank=True, max_length=32)
    stock_quantity = serializers.IntegerField(required=False, min_value=0, allow_null=True)
    manage_stock = serializers.BooleanField(required=False)
    description = serializers.CharField(required=False, allow_blank=True, max_length=6000)
    short_description = serializers.CharField(required=False, allow_blank=True, max_length=1200)
    meta_title = serializers.CharField(required=False, allow_blank=True, max_length=200)
    meta_description = serializers.CharField(required=False, allow_blank=True, max_length=500)
    attributes = serializers.ListField(child=serializers.DictField(), required=False)

    def validate_regular_price(self, value):
        return self.normalize_price(value)

    def validate_sale_price(self, value):
        return self.normalize_price(value)

    @staticmethod
    def normalize_price(value):
        if not value:
            return None
        try:
            amount = Decimal(value)
            if amount.is_finite() and 0 <= amount <= 999999999 and amount.as_tuple().exponent >= -2:
                return format(amount, '.2f')
        except InvalidOperation:
            pass
        raise serializers.ValidationError('Enter a decimal price with up to two decimal places.')


class ProductJobUpdateSerializer(serializers.Serializer):
    reviewer_edits = ProductDetailsSerializer()
    size_chart_id = serializers.PrimaryKeyRelatedField(queryset=SizeChart.objects.all(), required=False, allow_null=True)
    show_size_chart_in_description = serializers.BooleanField(required=False)
    generation_notes = serializers.CharField(max_length=2000, required=False, allow_blank=True)


class ChartPhotoSerializer(serializers.Serializer):
    image = PhotoField()


class GalleryPhotoSerializer(serializers.Serializer):
    shot = serializers.ChoiceField(choices=('image_1_url', 'image_2_url', 'image_3_url', 'fabric_image_url'))
    image = PhotoField()


class GenerationOptionsSerializer(serializers.Serializer):
    generation_notes = serializers.CharField(max_length=2000, required=False, allow_blank=True)


class ProductJobApprovalSerializer(serializers.Serializer):
    publish_to_woocommerce = serializers.BooleanField(default=True)


class FabricCropSerializer(serializers.Serializer):
    center_x = serializers.IntegerField(min_value=0, max_value=100, default=50)
    center_y = serializers.IntegerField(min_value=0, max_value=100, default=50)


class SocialCampaignSerializer(serializers.Serializer):
    logo = PhotoField(required=False)
    facebook = serializers.CharField(max_length=2200)
    instagram = serializers.CharField(max_length=2200)
    tiktok = serializers.CharField(max_length=4000)

    def validate_logo(self, photo):
        if photo.image.format != 'PNG':
            raise serializers.ValidationError('Upload the brand logo as a PNG.')
        return photo


class SocialPublicationSerializer(serializers.Serializer):
    platform = serializers.ChoiceField(choices=('facebook', 'instagram', 'tiktok'))
    caption = serializers.CharField(max_length=4000)
    title = serializers.CharField(max_length=90, required=False)
    logo = PhotoField(required=False)
    consent = serializers.BooleanField()

    def validate_logo(self, photo):
        if photo.image.format != 'PNG':
            raise serializers.ValidationError('Upload the brand logo as a PNG.')
        return photo

    def validate(self, attrs):
        if not attrs['consent']:
            raise serializers.ValidationError({'consent': 'Confirm this send before continuing.'})
        if attrs['platform'] == 'tiktok':
            if not attrs.get('title') or len(attrs['title'].encode('utf-16-le')) // 2 > 90:
                raise serializers.ValidationError({'title': 'Enter a TikTok title of up to 90 UTF-16 units.'})
            if len(attrs['caption'].encode('utf-16-le')) // 2 > 4000:
                raise serializers.ValidationError({'caption': 'Keep the TikTok caption within 4,000 UTF-16 units.'})
        elif len(attrs['caption']) > 2200:
            raise serializers.ValidationError({'caption': 'Keep the caption within 2,200 characters.'})
        return attrs
