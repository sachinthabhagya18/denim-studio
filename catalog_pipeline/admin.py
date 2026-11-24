from django.contrib import admin
from django.utils.html import format_html

from .models import ModelProfile, ProductJob
from .serializers import ModelProfileSerializer, PhotoField
from rest_framework.exceptions import ValidationError as APIValidationError
from django import forms


admin.site.site_header = 'Denim Studio'
admin.site.site_title = 'Denim Studio Administration'
admin.site.index_title = 'Administration'
admin.site.site_url = '/pipeline/dashboard/'


class BulkPhotoInput(forms.FileInput):
    allow_multiple_selected = True


class BulkPhotoField(forms.Field):
    widget = BulkPhotoInput(attrs={'accept': 'image/jpeg,image/png,image/webp'})

    def clean(self, data):
        if not data:
            return []
        if len(data) > 3:
            raise forms.ValidationError('Choose up to three additional model photos.')
        try:
            return [PhotoField().run_validation(photo) for photo in data]
        except APIValidationError as exc:
            raise forms.ValidationError(str(exc.detail[0])) from exc


class ModelProfileForm(forms.ModelForm):
    bulk_reference_images = BulkPhotoField(required=False, label='Additional photos',
                                           help_text='Optional. Up to three photos. A new batch replaces the saved batch.')

    class Meta:
        model = ModelProfile
        fields = ('name', 'reference_image', 'side_reference_image', 'bulk_reference_images', 'description')
        help_texts = {
            'description': 'Optional styling notes included in future generation prompts. Saving or updating this profile uses no credits.',
            'reference_image': 'Upload a clear, front-facing photo of the model.',
            'side_reference_image': 'Side view of the same person. Use JPEG, PNG or WebP up to 10 MB.',
        }

    def clean(self):
        cleaned = super().clean()
        for name in ('reference_image', 'side_reference_image'):
            photo = self.files.get(name)
            if photo is None or name in self.errors:
                continue
            try:
                cleaned[name] = PhotoField().run_validation(photo)
            except APIValidationError as exc:
                self.add_error(name, str(exc.detail[0]))
        return cleaned


@admin.register(ModelProfile)
class ModelProfileAdmin(admin.ModelAdmin):
    form = ModelProfileForm
    list_display = ['preview', 'name', 'has_side_photo', 'created_at']
    list_display_links = ['name']
    search_fields = ['name', 'description']
    list_filter = ['created_at']
    readonly_fields = ('preview', 'reference_instructions', 'created_at', 'updated_at')
    fieldsets = (
        ('Reference profile', {'fields': ('name', 'reference_image', 'side_reference_image', 'bulk_reference_images', 'description', 'preview')}),
        ('Generation instructions', {'fields': ('reference_instructions', 'created_at', 'updated_at')}),
    )

    def save_model(self, request, obj, form, change):
        # Legacy outlines belong only to the original file they were created for.
        for image_field, role in (('reference_image', 'front'), ('side_reference_image', 'side')):
            if image_field in form.changed_data:
                obj.garment_masks.pop(role, None)
        save = super().save_model

        def save_profile(values):
            if 'additional_reference_paths' in values:
                obj.additional_reference_paths = values['additional_reference_paths']
            save(request, obj, form, change)

        ModelProfileSerializer().save_bulk_references({'bulk_reference_images': form.cleaned_data['bulk_reference_images']}, save_profile)

    @admin.display(description='Front photo')
    def preview(self, obj):
        if obj and obj.reference_image:
            return format_html('<img src="{}" alt="{}" class="studio-admin-thumbnail">', obj.reference_image.url, obj.name)
        return '—'

    @admin.display(description='Side photo', boolean=True)
    def has_side_photo(self, obj):
        return bool(obj.side_reference_image)

    @admin.display(description='Reusable prompt')
    def reference_instructions(self, obj):
        return obj.generation_profile()['instructions'] if obj and obj.pk else 'Available after the profile is saved.'


@admin.register(ProductJob)
class ProductJobAdmin(admin.ModelAdmin):
    list_display = ['product_title', 'extracted_jean_type', 'status', 'woocommerce_product_id', 'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['job_id', 'extracted_jean_type', 'raw_product_text', 'structured_data__title']
    readonly_fields = (
        'job_id', 'status', 'created_at', 'updated_at', 'raw_product_text', 'model_profile',
        'garment_images', 'extracted_jean_type', 'structured_data', 'image_1_url',
        'image_2_url', 'image_3_url', 'reviewer_edits', 'woocommerce_product_id',
        'woocommerce_permalink', 'error_message',
    )
    fieldsets = (
        ('Job', {'fields': ('job_id', 'status', 'created_at', 'updated_at', 'error_message')}),
        ('Intake', {'fields': ('raw_product_text', 'model_profile', 'garment_images')}),
        ('Product details', {'fields': ('extracted_jean_type', 'structured_data', 'reviewer_edits')}),
        ('Generated photos', {'fields': ('image_1_url', 'image_2_url', 'image_3_url')}),
        ('WooCommerce', {'fields': ('woocommerce_product_id', 'woocommerce_permalink')}),
    )

    def has_add_permission(self, request):
        return False

    @admin.display(description='Product')
    def product_title(self, obj):
        return obj.get_final_data().get('title') or obj.job_id
