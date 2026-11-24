"""Gemini Developer API image generation using inline catalog references."""

import base64
from binascii import Error as Base64Error
from importlib.util import find_spec

from django.conf import settings


class GeminiImageError(ValueError):
    """An image-provider error safe to show in the dashboard."""


def gemini_image_config_error() -> str:
    if not settings.GEMINI_API_KEY:
        return 'Set GEMINI_API_KEY in .env before selecting Gemini.'
    if not settings.GEMINI_IMAGE_MODEL:
        return 'Set GEMINI_IMAGE_MODEL to a Gemini image generation model.'
    try:
        installed = find_spec('google.genai') is not None
    except ImportError:
        installed = False
    if not installed:
        return 'Install the project requirements to enable the Gemini image provider.'
    return ''


def _image_part(reference: dict) -> dict:
    try:
        url = reference['image_url']['url']
        header, data = url.split(',', 1)
        mime_type = header.removeprefix('data:').removesuffix(';base64')
        if not data or header != f'data:{mime_type};base64' or mime_type not in {
            'image/jpeg', 'image/png', 'image/webp',
        }:
            raise ValueError('Unsupported image')
    except (AttributeError, KeyError, TypeError, ValueError):
        raise GeminiImageError('Gemini requires JPEG, PNG or WebP image references.') from None
    return {'type': 'image', 'mime_type': mime_type, 'data': data}


def _gemini_error_message(error: Exception) -> str:
    code = getattr(error, 'status_code', None) or getattr(error, 'code', None)
    if code in (401, 403):
        return 'Gemini denied the request. Check the API key and its project access.'
    if code in (402, 429):
        return 'Gemini billing or quota blocked the image request. Check the API project in Google AI Studio.'
    if code == 404:
        return 'The configured Gemini image model is unavailable for this API project.'
    if code == 400:
        return 'Gemini rejected the image request. Check the configured image model and reference photos.'
    return 'Gemini could not complete the image request. Check the connection and try again.'


def generate_gemini_image(prompt: str, references: list[dict], *, aspect_ratio: str = '3:4') -> str:
    """Return the Gemini image as a plain base64 string for the shared image pipeline."""
    configuration_error = gemini_image_config_error()
    if configuration_error:
        raise GeminiImageError(configuration_error)
    if len(references) > 14:
        raise GeminiImageError('Gemini accepts up to 14 reference photos per image request.')
    contents = [{'type': 'text', 'text': prompt}, *map(_image_part, references)]
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        raise GeminiImageError('Install the project requirements to enable the Gemini image provider.') from None

    try:
        with genai.Client(
            api_key=settings.GEMINI_API_KEY,
            vertexai=False,
            http_options=types.HttpOptions(
                timeout=180_000,
                retry_options=types.HttpRetryOptions(attempts=1, http_status_codes=[503]),
            ),
        ) as client:
            result = client.interactions.create(
                model=settings.GEMINI_IMAGE_MODEL,
                input=contents,
                response_format={'type': 'image', 'aspect_ratio': aspect_ratio, 'image_size': '1K'},
                store=False,
                automatic_continuation=False,
            )
    except Exception as exc:
        # SDK exceptions can include request or response bodies. Never expose those to the workflow log.
        raise GeminiImageError(_gemini_error_message(exc)) from None

    image = result.output_image
    encoded = image.data if image else None
    if not isinstance(encoded, str) or not encoded:
        raise GeminiImageError('Gemini returned no image. Try another reference photo or prompt.')
    try:
        base64.b64decode(encoded, validate=True)
    except (Base64Error, ValueError):
        raise GeminiImageError('Gemini returned an unreadable image.') from None
    return encoded
