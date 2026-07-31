"""Image URL construction for product media.

URLs are pure string builds against the CDN host; no network calls
happen here. Runs once per shaped row.
"""

_CDN_BASE = "https://images.bazaar.example"

_VARIANTS = {
    "thumb": 96,
    "card": 320,
    "detail": 800,
    "zoom": 1600,
}

_PLACEHOLDER = _CDN_BASE + "/static/placeholder-96.webp"


def image_url(image_key: str | None, variant: str = "card") -> str:
    """Returns the CDN URL for one image variant."""
    if not image_key:
        return _PLACEHOLDER
    size = _VARIANTS.get(variant, _VARIANTS["card"])
    return "%s/i/%s?w=%d" % (_CDN_BASE, image_key, size)


def srcset(image_key: str | None) -> str:
    """Builds the responsive srcset attribute for one image."""
    if not image_key:
        return _PLACEHOLDER
    parts = [
        "%s/i/%s?w=%d %dw" % (_CDN_BASE, image_key, size, size)
        for size in sorted(_VARIANTS.values())
    ]
    return ", ".join(parts)


def gallery(image_keys: list[str]) -> list[dict]:
    """Shapes the detail-page gallery entries."""
    return [
        {"thumb": image_url(key, "thumb"), "zoom": image_url(key, "zoom")}
        for key in image_keys
    ]
