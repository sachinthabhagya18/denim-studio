"""Local catalog assets and campaign export. These operations make no AI calls."""

import csv
import io
import zipfile

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils.text import slugify
from django.utils.html import format_html, format_html_join
from django.utils import timezone
from PIL import Image, ImageDraw, ImageFont, ImageOps

SOCIAL_CHANNELS = ('facebook', 'instagram', 'tiktok')


def mark_progress(job_id, step, label, model='', **updates):
    from .models import ProductJob

    jobs = ProductJob.objects.filter(job_id=job_id)
    previous = jobs.values_list('workflow_progress', flat=True).first() or {}
    entry = {'step': step, 'label': label, 'model': model, 'at': timezone.now().isoformat()}
    history = [*previous.get('history', [])[-79:], entry]
    jobs.update(workflow_progress={**entry, 'history': history}, **updates)


def fabric_crop(source_path, destination, center_x=50, center_y=50):
    if not source_path or not default_storage.exists(source_path):
        raise ValueError('Upload a fabric reference or a garment photo first.')
    with default_storage.open(source_path, 'rb') as source, Image.open(source) as image:
        photo = ImageOps.exif_transpose(image).convert('RGB')
    width, height = photo.size
    side = max(1, min(width, height) // 2)
    x = min(max(0, round(width * center_x / 100) - side // 2), width - side)
    y = min(max(0, round(height * center_y / 100) - side // 2), height - side)
    crop = photo.crop((x, y, x + side, y + side))
    crop.thumbnail((900, 900), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    crop.save(output, 'JPEG', quality=96)
    return default_storage.url(default_storage.save(destination, ContentFile(output.getvalue())))


def read_chart_table(table):
    try:
        rows = [row for row in csv.reader(io.StringIO(table), strict=True) if any(cell.strip() for cell in row)]
    except csv.Error as exc:
        raise ValueError('Enter the chart as comma-separated rows.') from exc
    if not 2 <= len(rows) <= 17 or not 2 <= len(rows[0]) <= 7:
        raise ValueError('Use a header, 1 to 16 size rows, and 2 to 7 columns.')
    if any(len(row) != len(rows[0]) or any(not cell.strip() or len(cell) > 32 for cell in row) for row in rows):
        raise ValueError('Keep every row the same width and each cell between 1 and 32 characters.')
    return rows


def size_chart_png(title, rows):
    body = ImageFont.load_default(size=24)
    heading = ImageFont.load_default(size=32)
    widths = [max(106, int(max(body.getlength(row[col].strip()) for row in rows)) + 36) for col in range(len(rows[0]))]
    width = max(560, sum(widths) + 48, int(heading.getlength(title)) + 48)
    if width > 2200:
        raise ValueError('Shorten the chart title or column labels.')
    image = Image.new('RGB', (width, 128 + len(rows) * 49), '#f2f5fa')
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width, 75), fill='#111d32')
    draw.text((24, 22), title, fill='white', font=heading)
    for index, row in enumerate(rows):
        y = 94 + 49 * index
        draw.rectangle((24, y, width - 24, y + 48), fill='#203553' if index == 0 else ('#ffffff' if index % 2 else '#e3ebf6'))
        x = 42
        for cell, column_width in zip(row, widths):
            draw.text((x, y + 10), cell.strip(), fill='white' if index == 0 else '#182238', font=body)
            x += column_width
    output = io.BytesIO()
    image.save(output, 'PNG', optimize=True)
    return ContentFile(output.getvalue(), name=(slugify(title)[:75] or 'size-chart') + '.png')


def size_chart_html(title, rows):
    headers = format_html_join('', '<th scope="col" style="padding:10px;text-align:left;border:1px solid #dbe2ee">{}</th>',
                               ((cell,) for cell in rows[0]))
    body = format_html_join('', '<tr>{}</tr>', (
        (format_html_join('', '<td style="padding:10px;border:1px solid #dbe2ee">{}</td>',
                          ((cell,) for cell in row)),)
        for row in rows[1:]
    ))
    return format_html(
        '<h2>Size chart</h2><div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse">'
        '<caption>{}</caption><thead style="background:#203553;color:#fff"><tr>{}</tr></thead><tbody>{}</tbody>'
        '</table></div>', title, headers, body,
    )


def campaign_photos(images, logo=None, *, frame_size=None, max_dimension=None):
    brand = None
    if logo:
        with Image.open(logo) as source:
            brand = ImageOps.exif_transpose(source).convert('RGBA')
        bounds = brand.getbbox()
        if not bounds:
            raise ValueError('The uploaded logo is fully transparent.')
        brand = brand.crop(bounds)
    for label, path in images:
        with default_storage.open(path, 'rb') as source, Image.open(source) as image:
            photo = ImageOps.exif_transpose(image).convert('RGBA')
        if frame_size:
            photo = ImageOps.pad(photo, frame_size, method=Image.Resampling.LANCZOS, color='white')
        if max_dimension:
            photo.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
        if brand:
            mark = brand.copy()
            mark.thumbnail((max(1, photo.width // 4), max(1, photo.height // 6)), Image.Resampling.LANCZOS)
            margin = max(6, photo.width // 30)
            position = (max(0, photo.width - mark.width - margin), max(0, photo.height - mark.height - margin))
            photo.alpha_composite(mark, position)
        jpeg = io.BytesIO()
        photo.convert('RGB').save(jpeg, 'JPEG', quality=94)
        yield f'{label}.jpg', jpeg.getvalue()


def social_archive(images, logo, captions):
    output = io.BytesIO()
    rendered = list(campaign_photos(images, logo))
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for platform in SOCIAL_CHANNELS:
            archive.writestr(f'{platform}/caption.txt', captions[platform])
            for filename, image in rendered:
                archive.writestr(f'{platform}/{filename}', image)
    output.seek(0)
    return output
