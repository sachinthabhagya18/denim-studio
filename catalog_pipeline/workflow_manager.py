import logging
from threading import Thread

from django.db import connection
from django.core.files.storage import default_storage

from .catalog_media import mark_progress
from .langgraph_workflow import SHOT_NAMES, create_pipeline_graph, generated_file_path, replacement_fields
from .models import ProductJob

logger = logging.getLogger(__name__)


def start_workflow(job, stage):
    thread = Thread(target=run_workflow_async, args=(job.pk, stage), daemon=True)
    thread.start()


def run_workflow_async(job_pk, stage):
    try:
        job = ProductJob.objects.get(pk=job_pk)
        replacement = replacement_fields(stage)
        result = create_pipeline_graph(stage).invoke({
            'job_id': job.job_id,
            'raw_product_text': job.raw_product_text,
            'garment_image_paths': job.garment_images,
            'model_reference': job.model_reference,
            'generation_notes': job.generation_notes,
            'replace_shots': replacement,
            'image_provider': job.image_provider or 'openrouter',
            'fabric_reference': job.fabric_reference.name,
            'fabric_image_url': job.fabric_image_url,
            'structured_data': job.get_final_data(),
            **{field: getattr(job, field) for field in SHOT_NAMES},
        })
        if stage == 'extract':
            fields = ('structured_data', 'extracted_jean_type')
        else:
            fields = (*SHOT_NAMES, 'fabric_image_url')
        updates = {field: result[field] for field in fields if field in result}
        if replacement:
            changed = [field for field in replacement if result.get(field) and result[field] != getattr(job, field)]
            if result.get('status') == 'FAILED' or len(changed) != len(replacement):
                for field in changed:
                    updates.pop(field, None)
                    try:
                        default_storage.delete(generated_file_path(result[field]))
                    except (OSError, ValueError):
                        logger.warning('Could not clean up staged image for job %s', job.job_id)
            else:
                updates.update(job.pending_gallery_change(*changed))
                mark_progress(job.job_id, 'review', 'Replacement images are ready for review')
            updates['status'] = 'PUBLISHED' if job.woocommerce_product_id else 'PENDING_REVIEW'
        else:
            updates['status'] = result['status']
        updates['error_message'] = result.get('error_message', '')
        for field, value in updates.items():
            setattr(job, field, value)
        job.save(update_fields=[*updates, 'updated_at'])
    except Exception:
        logger.exception('Workflow %s failed for job %s', stage, job_pk)
        jobs = ProductJob.objects.filter(pk=job_pk)
        published = stage.startswith('regenerate:') and jobs.exclude(woocommerce_product_id=None).exists()
        jobs.update(
            status='PUBLISHED' if published else 'PENDING_REVIEW' if stage.startswith('regenerate:') else 'FAILED',
            error_message='The background task failed. Check the server log.',
        )
    finally:
        connection.close()
