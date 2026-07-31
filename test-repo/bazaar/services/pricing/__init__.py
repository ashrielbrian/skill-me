"""Pricing service.

Consumes price and promo events from Redis streams and writes current
prices to Postgres. The consumer pipeline lives under consumer/; the
process entry point is worker.py.

Load figures and the batch layout are described in README.md.
"""
