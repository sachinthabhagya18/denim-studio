"""FASHN Try-On Max, using one fixed model photo per requested view."""

import re
import time

import requests
from django.conf import settings

from .models import ProductJob

FASHN_MODEL = 'tryon-max'
FASHN_API = 'https://api.fashn.ai/v1'
TRYON_ROLES = {'image_1_url': 'front view', 'image_3_url': 'side profile'}


class TryOnError(ValueError):
    """A short, safe error suitable for the dashboard."""


def model_pose_path(profile, shot):
    paths = profile.get('photos', [])
    roles = profile.get('photo_roles') or ['front view', 'side profile'][:len(paths)]
    return next((path for path, role in zip(paths, roles) if role == TRYON_ROLES[shot]), '')


def run_tryon(job_id, shot, model_image, product_image, notes):
    jobs = ProductJob.objects.filter(job_id=job_id)
    predictions = jobs.values_list('tryon_predictions', flat=True).get()
    prediction = predictions.get(shot)
    headers = {'Authorization': f'Bearer {settings.FASHN_API_KEY}'}

    def forget_prediction():
        predictions.pop(shot, None)
        jobs.update(tryon_predictions=predictions)

    if not prediction:
        prompt = (
            'Edit the supplied model image directly. Replace only the trousers on that person with the exact denim jeans '
            'from the product image. Keep the original face, expression, hair, skin, body shape, height, proportions, '
            'pose, hands, top, footwear, accessories, background, framing and light. Preserve the product wash, '
            'waistband, rise, pockets, seams, leg width and hem. Ignore any person or styling in the product image. '
            'Use the model photo as the sole source of identity and pose. Do not add writing or overlays.'
        )
        if notes:
            prompt += ' Optional garment-only notes, subject to those constraints: ' + notes
        try:
            response = requests.post(
                f'{FASHN_API}/run', headers=headers, timeout=25, allow_redirects=False,
                json={'model_name': FASHN_MODEL, 'inputs': {
                    'model_image': model_image, 'product_image': product_image, 'prompt': prompt,
                    'return_base64': True, 'output_format': 'jpeg', 'num_images': 1,
                }},
            )
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise TryOnError(f'FASHN rejected the request (HTTP {exc.response.status_code}).') from exc
        except requests.RequestException as exc:
            raise TryOnError('FASHN did not confirm it received the request. Check its dashboard before retrying.') from exc
        prediction = response.json().get('id', '')
        if not isinstance(prediction, str) or not re.fullmatch(r'[\w-]{1,100}', prediction):
            raise TryOnError('FASHN did not return a prediction ID.')
        predictions[shot] = prediction
        jobs.update(tryon_predictions=predictions)

    if not isinstance(prediction, str) or not re.fullmatch(r'[\w-]{1,100}', prediction):
        raise TryOnError('The saved FASHN prediction ID is invalid.')
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        try:
            status = requests.get(f'{FASHN_API}/status/{prediction}', headers=headers, timeout=15, allow_redirects=False)
            if status.status_code == 404:
                forget_prediction()
                raise TryOnError('The saved FASHN result expired. Retry this shot to start a new prediction.')
            status.raise_for_status()
        except requests.RequestException as exc:
            raise TryOnError('Could not check FASHN. Retry the shot to resume the saved prediction.') from exc
        result = status.json()
        if result.get('status') == 'completed':
            outputs = result.get('output') or []
            if not outputs or not re.match(r'^data:image/(?:jpeg|png);base64,', outputs[0]):
                forget_prediction()
                raise TryOnError('FASHN returned no usable image for this prediction.')
            return outputs[0].split(',', 1)[1], predictions
        if result.get('status') not in ('starting', 'in_queue', 'processing'):
            forget_prediction()
            raise TryOnError('FASHN could not complete this try-on. Check the selected model pose and product photo.')
        time.sleep(2)
    raise TryOnError('FASHN is still processing. Retry this shot to resume the saved prediction.')
