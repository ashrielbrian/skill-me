"""Payout calculation and persistence for the nightly settlement run.

Order of operations: rollup -> adjustments -> fees -> ledger -> export.
"""
