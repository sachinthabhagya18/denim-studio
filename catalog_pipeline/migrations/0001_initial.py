# Generated migration for catalog_pipeline app

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='ModelProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                ('description', models.TextField(blank=True)),
                ('reference_image', models.ImageField(upload_to='model_profiles/')),
                ('metadata', models.JSONField(default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name='ProductJob',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('job_id', models.CharField(max_length=255, unique=True)),
                ('thread_id', models.CharField(blank=True, max_length=255)),
                ('status', models.CharField(
                    choices=[
                        ('INTAKE', 'Intake'),
                        ('PROCESSING', 'Processing'),
                        ('PENDING_REVIEW', 'Pending Review'),
                        ('APPROVED', 'Approved'),
                        ('PUBLISHED', 'Published'),
                        ('FAILED', 'Failed'),
                    ],
                    default='INTAKE',
                    max_length=20,
                )),
                ('raw_product_text', models.TextField()),
                ('extracted_jean_type', models.CharField(blank=True, max_length=255)),
                ('garment_images', models.JSONField(default=list)),
                ('image_1_url', models.URLField(blank=True, null=True)),
                ('image_2_url', models.URLField(blank=True, null=True)),
                ('image_3_url', models.URLField(blank=True, null=True)),
                ('image_1_notes', models.TextField(blank=True)),
                ('image_2_notes', models.TextField(blank=True)),
                ('image_3_notes', models.TextField(blank=True)),
                ('structured_data', models.JSONField(default=dict)),
                ('reviewer_edits', models.JSONField(default=dict)),
                ('woocommerce_product_id', models.IntegerField(blank=True, null=True)),
                ('woocommerce_permalink', models.URLField(blank=True, null=True)),
                ('error_message', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('model_profile', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='catalog_pipeline.modelprofile')),
            ],
        ),
    ]
