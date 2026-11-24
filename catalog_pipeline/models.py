from django.db import models
from django.conf import settings

MODEL_REFERENCE_FIELDS = {
    'reference_image': 'front view',
    'face_reference_image': 'face close-up',
    'side_reference_image': 'side profile',
    'three_quarter_reference_image': 'three-quarter view',
    'detail_reference_image': 'additional identity detail',
}


class ModelProfile(models.Model):
    """Reusable model reference images for consistent pose/style"""
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    reference_image = models.ImageField(upload_to='model_profiles/', verbose_name='Front reference photo')
    side_reference_image = models.ImageField(upload_to='model_profiles/', blank=True)
    face_reference_image = models.ImageField(upload_to='model_profiles/', blank=True)
    three_quarter_reference_image = models.ImageField(upload_to='model_profiles/', blank=True)
    detail_reference_image = models.ImageField(upload_to='model_profiles/', blank=True)
    additional_reference_paths = models.JSONField(default=list, blank=True, editable=False)
    garment_masks = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    def generation_profile(self):
        references = [(getattr(self, field).name, role) for field, role in MODEL_REFERENCE_FIELDS.items() if getattr(self, field)]
        references.extend((path, 'additional view of the same person') for path in self.additional_reference_paths)
        instructions = (
            'Use the reference photos of the model to preserve the same face, hairstyle, '
            'skin tone and body proportions. Keep those features consistent between angles.'
        )
        if self.description:
            instructions += f' Model styling notes: {self.description}'
        return {
            'photos': [path for path, _ in references], 'photo_roles': [role for _, role in references],
            'instructions': instructions, 'garment_masks': self.garment_masks,
        }


class SizeChart(models.Model):
    name = models.CharField(max_length=120)
    image = models.ImageField(upload_to='size_charts/')
    sizes = models.JSONField(default=list, blank=True)
    table_rows = models.JSONField(default=list, blank=True, editable=False)
    store_attachment_id = models.PositiveIntegerField(null=True, blank=True, editable=False)
    store_media_url = models.URLField(max_length=1000, blank=True, editable=False)
    store_url = models.URLField(blank=True, editable=False)

    class Meta:
        ordering = ['name', 'id']

    def __str__(self):
        return self.name


class ImageGenerationPreference(models.Model):
    provider = models.CharField(max_length=20, choices=(('openrouter', 'OpenRouter'), ('gemini', 'Gemini')))

    @classmethod
    def selected_provider(cls):
        selected = cls.objects.filter(pk=1).values_list('provider', flat=True).first()
        return selected or getattr(settings, 'IMAGE_GENERATION_PROVIDER', 'openrouter')


class ProductJob(models.Model):
    STATUS_CHOICES = (
        ('INTAKE', 'Intake'),
        ('PROCESSING', 'Processing'),
        ('PENDING_DETAILS', 'Check product details'),
        ('PENDING_REVIEW', 'Pending Review'),
        ('APPROVED', 'Approved'),
        ('PUBLISHING', 'Publishing'),
        ('PUBLISHED', 'Published'),
        ('FAILED', 'Failed'),
    )

    job_id = models.CharField(max_length=50, unique=True, db_index=True)
    thread_id = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='INTAKE', db_index=True)
    
    raw_product_text = models.TextField(help_text="Single text block containing all product details")
    extracted_jean_type = models.CharField(max_length=500, blank=True, help_text="Extracted from raw_product_text")
    
    model_profile = models.ForeignKey(ModelProfile, null=True, blank=True, on_delete=models.SET_NULL)
    garment_images = models.JSONField(default=list, help_text="List of uploaded garment image URLs/paths")
    model_reference = models.JSONField(default=dict, blank=True, editable=False)
    store_media_ids = models.JSONField(default=dict, blank=True, editable=False)
    workflow_progress = models.JSONField(default=dict, blank=True, editable=False)
    generation_notes = models.TextField(blank=True)
    image_provider = models.CharField(max_length=20, blank=True, editable=False)
    tryon_predictions = models.JSONField(default=dict, blank=True, editable=False)
    fabric_reference = models.ImageField(upload_to='fabric_references/', blank=True)
    fabric_image_url = models.TextField(blank=True)
    size_chart = models.ForeignKey(SizeChart, null=True, blank=True, on_delete=models.PROTECT)
    show_size_chart_in_description = models.BooleanField(default=True)
    social_draft = models.JSONField(default=dict, blank=True, editable=False)
    social_logo = models.ImageField(upload_to='social_logos/', blank=True)
    listing_dirty = models.BooleanField(default=False, editable=False)
    gallery_replacements = models.JSONField(default=dict, blank=True, editable=False)
    
    image_1_url = models.TextField(blank=True, help_text="Model in jeans, 3:4, white BG")
    image_2_url = models.TextField(blank=True, help_text="Front & back jeans, 3:4, white BG")
    image_3_url = models.TextField(blank=True, help_text="Side profile model, 3:4, white BG")
    
    image_1_notes = models.TextField(blank=True)
    image_2_notes = models.TextField(blank=True)
    image_3_notes = models.TextField(blank=True)
    
    structured_data = models.JSONField(default=dict, help_text="Generated product JSON (OpenRouter)")
    reviewer_edits = models.JSONField(default=dict, help_text="Edits made in review UI")
    
    woocommerce_product_id = models.IntegerField(null=True, blank=True)
    woocommerce_permalink = models.URLField(blank=True)
    
    error_message = models.TextField(blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', '-created_at']),
            models.Index(fields=['thread_id']),
        ]

    def __str__(self):
        return f"Job {self.job_id}: {self.extracted_jean_type}"

    def get_final_data(self):
        """Return merged structured_data + reviewer_edits"""
        final = self.structured_data.copy()
        final.update(self.reviewer_edits)
        return final

    def pending_gallery_change(self, *fields):
        if not self.woocommerce_product_id:
            return {}
        changes = dict(self.gallery_replacements)
        for field in fields:
            changes.setdefault(field, getattr(self, field))
        return {'gallery_replacements': changes, 'listing_dirty': True}


class SocialPublication(models.Model):
    class State(models.TextChoices):
        SENDING = 'sending', 'Sending'
        PROCESSING = 'processing', 'Processing'
        PUBLISHED = 'published', 'Published'
        FAILED = 'failed', 'Failed'
        BLOCKED = 'blocked', 'Rejected'
        UNKNOWN = 'unknown', 'Check on platform'
        DRAFT = 'draft', 'Draft sent'

    job = models.ForeignKey(ProductJob, on_delete=models.CASCADE, related_name='social_publications')
    platform = models.CharField(max_length=12)
    state = models.CharField(max_length=12, choices=State.choices, default=State.SENDING)
    remote_id = models.CharField(max_length=255, blank=True)
    message = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['job', 'platform'], name='one_campaign_publication_per_platform'),
        ]
