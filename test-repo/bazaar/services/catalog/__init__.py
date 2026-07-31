"""Catalog service package.

Serves product search and product detail pages for the bazaar
marketplace. Search handles about 300 req/s and detail about
500 req/s in production; search p50 is about 30 ms.
"""

SERVICE = "catalog"
