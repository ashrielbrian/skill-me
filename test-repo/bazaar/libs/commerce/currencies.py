"""Currency reference data.

The supported set matches the platform rate table release for
release. Minor-unit exponents follow ISO 4217: most currencies
carry two decimal places, a small set carries none, and a few
carry three.
"""

CURRENCIES = (
    "USD", "EUR", "JPY", "GBP", "AUD", "CAD", "CHF", "CNY", "HKD", "NZD",
    "SEK", "KRW", "SGD", "NOK", "MXN", "INR", "RUB", "ZAR", "TRY", "BRL",
    "TWD", "DKK", "PLN", "THB", "IDR", "HUF", "CZK", "ILS", "CLP", "PHP",
    "AED", "COP", "SAR", "MYR", "RON", "ARS", "BGN", "DZD", "EGP", "IQD",
    "KWD", "MAD", "NGN", "OMR", "PEN", "PKR", "QAR", "UAH", "VND", "BHD",
    "BDT", "BYN", "BOB", "BWP", "CRC", "CUP", "DOP", "ETB", "GEL", "GHS",
    "GTQ", "HNL", "ISK", "JMD", "JOD", "KES", "KZT", "LBP", "LKR", "MMK",
    "MOP", "MUR", "NAD", "NPR", "PAB", "PYG", "RSD", "SDG", "TND", "TTD",
    "TZS", "UGX", "UYU", "UZS", "XAF", "XOF", "ZMW", "AFN", "ALL", "AMD",
    "ANG", "AOA", "AWG", "AZN", "BAM", "BBD", "BIF", "BND", "BSD", "BTN",
    "BZD", "CDF", "CVE", "DJF", "ERN", "FJD", "FKP", "GIP", "GMD", "GNF",
    "GYD", "HTG", "KGS", "KHR", "KMF", "KYD", "LAK", "LRD", "LSL", "LYD",
    "MDL", "MGA", "MKD", "MNT", "MRU", "MVR", "MWK", "MZN", "NIO", "PGK",
    "RWF", "SBD", "SCR", "SLL", "SOS", "SRD", "SSP", "STN", "SZL", "TJS",
    "TMT", "TOP", "VUV", "WST", "XCD", "XPF", "YER", "ZWL", "SYP", "VES",
)

_CURRENCY_SET = frozenset(CURRENCIES)

# Currencies whose minor unit is the whole unit.
ZERO_DECIMAL = frozenset({
    "BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW",
    "PYG", "RWF", "UGX", "VND", "VUV", "XAF", "XOF", "XPF",
})

# Currencies with three minor-unit places.
THREE_DECIMAL = frozenset({"BHD", "IQD", "JOD", "KWD", "OMR", "TND"})


# Display symbols for the currencies receipts render most; codes
# absent here render with their three-letter code instead.
SYMBOLS = {
    "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "CNY": "¥",
    "KRW": "₩", "INR": "₹", "RUB": "₽", "TRY": "₺", "VND": "₫",
    "THB": "฿", "PHP": "₱", "ILS": "₪", "NGN": "₦", "UAH": "₴",
    "CAD": "$", "AUD": "$", "NZD": "$", "HKD": "$", "SGD": "$",
}


def symbol_for(code):
    """Display symbol for a currency, falling back to its code."""
    return SYMBOLS.get(code, code)


def is_supported(code):
    """True when the code is in the supported set."""
    return code in _CURRENCY_SET


def minor_units(code):
    """Exponent of the currency's minor unit (0, 2, or 3)."""
    if code in ZERO_DECIMAL:
        return 0
    if code in THREE_DECIMAL:
        return 3
    return 2


def currency_count():
    """Size of the supported set; used by health reports."""
    return len(CURRENCIES)
