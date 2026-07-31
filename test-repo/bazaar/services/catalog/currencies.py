"""Currency display helpers for feed and interface text.

The symbol table covers the currencies the marketplace settles in;
it loads once at import.
"""

_SYMBOLS = {
    "AUD": "A$",
    "BRL": "R$",
    "CAD": "C$",
    "CHF": "Fr",
    "EUR": "€",
    "GBP": "£",
    "INR": "₹",
    "JPY": "¥",
    "KRW": "₩",
    "MXN": "Mex$",
    "PLN": "zł",
    "SEK": "kr",
    "USD": "$",
    "VND": "₫",
}

# Minor units are not used for these; amounts arrive as whole units.
_ZERO_DECIMAL = frozenset(("JPY", "KRW", "VND"))


def symbol(currency: str) -> str:
    """Returns the display symbol, or the code itself when unknown."""
    return _SYMBOLS.get(currency, currency)


def format_amount(price_cents: int, currency: str) -> str:
    """Formats a minor-unit amount with its symbol.

    Runs once per rendered price in feed text output.
    """
    if currency in _ZERO_DECIMAL:
        return "%s%d" % (symbol(currency), price_cents)
    return "%s%.2f" % (symbol(currency), price_cents / 100)


def supported() -> tuple[str, ...]:
    """Codes the table covers, in sorted order."""
    return tuple(sorted(_SYMBOLS))
