"""Normalize product text copied from storefronts and recover explicit catalog facts."""

import html
import re
import unicodedata

PRICE = r'(?:රු\.?|Rs\.?|LKR|USD|\$|£|€)\s*(?P<amount>\d+(?:,\d{3})*(?:\.\d{1,2})?)(?![\d,.])'
CHROME = re.compile(
    r'^(?:select\s+(?:waist\s+)?size|size chart|out of stock|currently out of stock|'
    r'in stock|add to cart|add to wishlist|islandwide delivery.*|cash on delivery.*|'
    r'product details\s*[-−+]?)$', re.I,
)
JEAN_TYPES = (
    (r'wide[\s-]+straight[\s-]+leg', 'Wide Straight Leg'),
    (r'wide[\s-]+leg', 'Wide Leg'),
    (r'straight[\s-]+leg', 'Straight Leg'),
    (r'boot[\s-]*cut', 'Bootcut'),
    (r'\b(?:flared?|flare cut)\b', 'Flared'),
    (r'\bskinny\b', 'Skinny'),
    (r'\btaper(?:ed)?\b', 'Tapered'),
    (r'\bbaggy\b', 'Baggy'),
)

ATTRIBUTE_NAMES = {'size': 'Size', 'sizes': 'Size', 'waist size': 'Size',
                   'color': 'Color', 'colors': 'Color', 'colour': 'Color', 'colours': 'Color'}


def catalog_attributes(generated, explicit):
    primary = {'Size': [], 'Color': []}
    special = []
    for attribute in [*generated, *explicit]:
        if not isinstance(attribute, dict) or not isinstance(attribute.get('options'), list):
            continue
        name = str(attribute.get('name', '')).strip()
        options = list(dict.fromkeys(option.strip() for option in attribute['options']
                                     if isinstance(option, str) and option.strip()))
        key = ATTRIBUTE_NAMES.get(name.lower())
        if key and options:
            primary[key] = options
        elif name and options and attribute.get('special') is True:
            special.append({'name': name, 'options': options, 'visible': True})
    return [{'name': name, 'options': options, 'visible': True} for name, options in primary.items()] + special


def storefront_facts(raw_text):
    text = unicodedata.normalize('NFKC', html.unescape(raw_text))
    text = re.sub(r'\[([^\]]+)\]\(https?://[^)]+\)', r'\1', text)
    text = text.replace('**', '').replace('__', '')
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    heading = next((line[2:].strip() for line in lines if line.startswith('# ')), '')
    heading = heading or next((
        line for line in lines if len(line) <= 200 and re.search(r'\b(?:jeans?|denim)\b', line, re.I)
        and not line.startswith(('#', '-')) and not re.match(r'SKU\b', line, re.I)
    ), '')
    title = heading[:200]
    description_lines = [
        line for line in lines
        if not CHROME.fullmatch(line)
        and not line.startswith('#')
        and line != title
        and not re.match(r'SKU\s*:', line, re.I)
        and not re.fullmatch(PRICE, line, re.I)
    ]
    details = {}
    if title and (any(line.startswith('# ') for line in lines) or any(re.match(r'SKU\s*:', line, re.I) for line in lines)):
        details['title'] = title
    for source in (title, ' '.join(description_lines)):
        kind = next((name for pattern, name in JEAN_TYPES if re.search(pattern, source, re.I)), None)
        if kind:
            details['jean_type'] = kind
            break

    sku = re.search(r'\bSKU\s*[:#]\s*([A-Z0-9][A-Z0-9._/-]{0,99})', text, re.I)
    if sku:
        details['sku'] = sku[1]
    # A single explicit price is safe to preserve. Let AI or the reviewer sort out multiple prices.
    amounts = {f"{amount.replace(',', '').split('.')[0]}.{(amount.partition('.')[2] + '00')[:2]}"
               for amount in re.findall(PRICE, text, re.I)}
    if len(amounts) == 1:
        details['regular_price'] = amounts.pop()
    quantity = re.search(r'\b(?:stock(?: quantity)?\s*[:=]\s*(\d+)|(\d+)\s+in stock)\b', text, re.I)
    if re.search(r'\b(?:out[ -]of[ -]stock|sold out)\b', text, re.I):
        details.update(stock_quantity=0, manage_stock=True)
    elif quantity:
        details.update(stock_quantity=int(quantity[1] or quantity[2]), manage_stock=True)

    attributes = []
    color = next((
        re.match(r'colou?r\s*:\s*(.+)', line, re.I) for line in description_lines
        if re.match(r'colou?r\s*:', line, re.I)
    ), None)
    if color:
        attributes.append({'name': 'Color', 'options': re.split(r'\s*[,;|]\s*', color[1][:120]), 'visible': True})
    sizes = re.search(r'^\s*(?:available\s+|waist\s+)?sizes?\s*[:=]\s*(.+)$', text, re.I | re.M)
    if sizes:
        attributes.append({'name': 'Size', 'options': re.split(r'[,;|/\s]+', sizes[1]), 'visible': True})
    rises = {value.lower() for value in re.findall(r'\b(high|mid|low)[\s-]+rise\b', text, re.I)}
    notice = ''
    if len(rises) > 1:
        notice = 'The source contains conflicting rise details. Confirm the rise and correct the description before generating.'

    source_text = '\n'.join(([title] if title else []) + description_lines)
    fallback = {
        'title': title, 'jean_type': details.get('jean_type', ''),
        'regular_price': None, 'sale_price': None, 'stock_quantity': None, 'manage_stock': False,
        'description': '\n'.join(description_lines)[:6000],
        'short_description': next((line[:1200] for line in description_lines if len(line) >= 45), ''),
        'attributes': attributes, **details,
    }
    return source_text, details, fallback, notice
