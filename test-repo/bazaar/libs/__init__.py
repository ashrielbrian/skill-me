"""Shared libraries for the bazaar marketplace platform.

Three packages live under this namespace:

``libs.dbkit``
    Async Postgres helpers: the shared connection pool, query
    executors, batch inserts, and named SQL template rendering.
    Every service reaches the database through this package.

``libs.eventkit``
    Redis stream plumbing: the buffered producer, consumer-group
    reads, and the codec that fixes the wire form of every event
    the services exchange.

``libs.commerce``
    Domain logic shared across services: money conversion and
    formatting, tax rates, SKU normalization, feature flags, and
    audit records, plus the reference tables (regions, currencies,
    categories) those rules depend on.

Dependency direction is one way: ``commerce`` may import from
``dbkit`` or ``eventkit``, and services import from all three.
Neither ``dbkit`` nor ``eventkit`` imports the others.

The libraries target Python 3.11 and lean on asyncpg for Postgres
and the redis package for streams. This module only names the
subpackages so tooling can discover them; importing it has no
side effects.
"""

__all__ = ["commerce", "dbkit", "eventkit"]
