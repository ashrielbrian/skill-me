"""Attribute display shaping for the detail page template.

Attributes arrive as key/value pairs on the product row; ordering
and labels are static and load once at import.
"""

_DISPLAY_ORDER = (
    "brand",
    "model",
    "color",
    "material",
    "weight_g",
    "width_mm",
    "height_mm",
    "depth_mm",
    "battery",
    "warranty_months",
)

_LABELS = {
    "brand": "Brand",
    "model": "Model",
    "color": "Color",
    "material": "Material",
    "weight_g": "Weight (g)",
    "width_mm": "Width (mm)",
    "height_mm": "Height (mm)",
    "depth_mm": "Depth (mm)",
    "battery": "Battery",
    "warranty_months": "Warranty (months)",
}

_ORDER_INDEX = {key: position for position, key in enumerate(_DISPLAY_ORDER)}


def ordered_attributes(attributes: dict) -> list[dict]:
    """Returns display rows in the standard order, extras last.

    Runs once per rendered detail template.
    """
    known = [key for key in attributes if key in _ORDER_INDEX]
    known.sort(key=_ORDER_INDEX.__getitem__)
    rows = [
        {"key": key, "label": _LABELS[key], "value": attributes[key]}
        for key in known
    ]
    extras = sorted(key for key in attributes if key not in _ORDER_INDEX)
    for key in extras:
        rows.append({"key": key, "label": key, "value": attributes[key]})
    return rows
