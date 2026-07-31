"""Settlement service.

Nightly batch that rolls up settled orders per seller, applies fees,
writes the payout ledger, submits bank transfers, and renders the
daily report. The entry point is cron.py.

Run cadence and volumes are described in README.md.
"""
