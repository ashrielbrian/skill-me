"""Errors raised by the settlement batch."""


class SettlementError(Exception):
    """Base class for settlement failures."""


class ExportError(SettlementError):
    """Raised when the bank export file cannot be written."""

    def __init__(self, path):
        super().__init__(f"export write failed: {path}")
        self.path = path


class BankSubmitError(SettlementError):
    """Raised when the banking partner rejects the transfer batch."""

    def __init__(self, status_code):
        super().__init__(f"transfer batch rejected with status {status_code}")
        self.status_code = status_code


class AdjustmentError(SettlementError):
    """Raised when a manual adjustment row is malformed."""


class ReconcileMismatch(SettlementError):
    """Raised when ledger and statement totals disagree past tolerance."""

    def __init__(self, count):
        super().__init__(f"{count} sellers mismatched")
        self.count = count
