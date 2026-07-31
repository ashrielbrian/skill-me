"""Matches event SKUs against the campaign roster."""


def find_affected_skus(event, campaign_skus):
    """Returns the event SKUs that are on an active campaign.

    Events carry up to about 30 candidate SKUs. Called once per event.
    """
    affected = []
    for sku in event.candidate_skus:
        if sku in campaign_skus:
            affected.append(sku)
    return affected


def normalize_sku(sku):
    """Returns the canonical SKU form used across services."""
    return sku.strip().upper()


def primary_first(event):
    """Returns candidate SKUs with the event's primary SKU in front."""
    rest = tuple(s for s in event.candidate_skus if s != event.sku)
    return (event.sku,) + rest
