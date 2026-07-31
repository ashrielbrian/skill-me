"""Product and catalog-page builders for the catalog suites."""


def build_product(sku="LAMP-0001-BRS", title="Brass Desk Lamp", price_cents=4599):
    return {
        "sku": sku,
        "title": title,
        "price_cents": price_cents,
        "in_stock": True,
    }


def build_catalog_page(page=1, size=25, total=120):
    """A page of products shaped like the search response body."""
    first = (page - 1) * size
    remaining = max(total - first, 0)
    count = min(size, remaining)
    items = []
    for offset in range(count):
        ordinal = first + offset + 1
        items.append(build_product(
            sku="LAMP-%04d-BRS" % ordinal,
            title="Desk Lamp %d" % ordinal,
            price_cents=2000 + 100 * ordinal,
        ))
    return {"page": page, "size": size, "total": total, "items": items}


def description_html(paragraph_count=4, sentence="A sturdy fixture for daily use."):
    # Repeated paragraphs, joined once rather than concatenated in the loop.
    paragraphs = []
    for _ in range(paragraph_count):
        paragraphs.append("<p>%s</p>" % sentence)
    return "".join(paragraphs)


def raw_sku_variants(base="lamp 0001 brs"):
    """Messy inputs that should all normalize to the same SKU."""
    return [
        base,
        base.upper(),
        base.replace(" ", "-"),
        "  %s  " % base,
        base.replace(" ", "_"),
    ]
