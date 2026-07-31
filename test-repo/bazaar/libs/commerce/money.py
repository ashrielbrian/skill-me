"""Currency conversion and money formatting.

Amounts travel through the platform as integer minor units (cents
for most currencies). ``convert`` moves an amount between
currencies through the USD pivot; ``format_cents`` renders an
amount for receipts, emails, and dashboards.
"""

import json
from decimal import Decimal, ROUND_HALF_UP

from .currencies import THREE_DECIMAL, ZERO_DECIMAL
from .errors import CurrencyError

# Rates are quoted as United States dollars per one unit of each
# currency, refreshed with each library release.
_RATES_JSON = """{
"USD": 1.0, "EUR": 1.09, "JPY": 0.0067, "GBP": 1.27, "AUD": 0.66,
"CAD": 0.74, "CHF": 1.13, "CNY": 0.14, "HKD": 0.128, "NZD": 0.61,
"SEK": 0.095, "KRW": 0.00074, "SGD": 0.75, "NOK": 0.094, "MXN": 0.058,
"INR": 0.012, "RUB": 0.011, "ZAR": 0.054, "TRY": 0.031, "BRL": 0.2,
"TWD": 0.031, "DKK": 0.146, "PLN": 0.25, "THB": 0.028, "IDR": 0.000063,
"HUF": 0.0028, "CZK": 0.044, "ILS": 0.27, "CLP": 0.0011, "PHP": 0.017,
"AED": 0.272, "COP": 0.00025, "SAR": 0.267, "MYR": 0.21, "RON": 0.22,
"ARS": 0.0011, "BGN": 0.56, "DZD": 0.0074, "EGP": 0.021, "IQD": 0.00076,
"KWD": 3.25, "MAD": 0.1, "NGN": 0.00065, "OMR": 2.6, "PEN": 0.27,
"PKR": 0.0036, "QAR": 0.275, "UAH": 0.024, "VND": 0.000039, "BHD": 2.65,
"BDT": 0.0085, "BYN": 0.31, "BOB": 0.145, "BWP": 0.073, "CRC": 0.0019,
"CUP": 0.042, "DOP": 0.017, "ETB": 0.0087, "GEL": 0.37, "GHS": 0.065,
"GTQ": 0.13, "HNL": 0.04, "ISK": 0.0072, "JMD": 0.0064, "JOD": 1.41,
"KES": 0.0071, "KZT": 0.0021, "LBP": 0.000011, "LKR": 0.0031, "MMK": 0.00048,
"MOP": 0.124, "MUR": 0.022, "NAD": 0.054, "NPR": 0.0075, "PAB": 1.0,
"PYG": 0.00013, "RSD": 0.0093, "SDG": 0.0017, "TND": 0.32, "TTD": 0.147,
"TZS": 0.00039, "UGX": 0.00027, "UYU": 0.025, "UZS": 0.000079, "XAF": 0.0017,
"XOF": 0.0017, "ZMW": 0.038, "AFN": 0.014, "ALL": 0.011, "AMD": 0.0026,
"ANG": 0.56, "AOA": 0.0012, "AWG": 0.56, "AZN": 0.59, "BAM": 0.56,
"BBD": 0.5, "BIF": 0.00035, "BND": 0.75, "BSD": 1.0, "BTN": 0.012,
"BZD": 0.5, "CDF": 0.00036, "CVE": 0.0099, "DJF": 0.0056, "ERN": 0.067,
"FJD": 0.44, "FKP": 1.27, "GIP": 1.27, "GMD": 0.015, "GNF": 0.00012,
"GYD": 0.0048, "HTG": 0.0076, "KGS": 0.011, "KHR": 0.00024, "KMF": 0.0022,
"KYD": 1.2, "LAK": 0.000046, "LRD": 0.0052, "LSL": 0.054, "LYD": 0.21,
"MDL": 0.056, "MGA": 0.00022, "MKD": 0.018, "MNT": 0.00029, "MRU": 0.025,
"MVR": 0.065, "MWK": 0.00058, "MZN": 0.016, "NIO": 0.027, "PGK": 0.25,
"RWF": 0.00077, "SBD": 0.12, "SCR": 0.074, "SLL": 0.000044, "SOS": 0.0018,
"SRD": 0.027, "SSP": 0.00077, "STN": 0.044, "SZL": 0.054, "TJS": 0.091,
"TMT": 0.29, "TOP": 0.42, "VUV": 0.0084, "WST": 0.36, "XCD": 0.37,
"XPF": 0.0091, "YER": 0.004, "ZWL": 0.0031, "SYP": 0.00008, "VES": 0.027
}"""


def convert(amount_cents, from_ccy, to_ccy) -> int:
    """Convert an integer minor-unit amount between currencies."""
    rates = json.loads(_RATES_JSON)
    if from_ccy not in rates:
        raise CurrencyError(from_ccy)
    if to_ccy not in rates:
        raise CurrencyError(to_ccy)
    usd = amount_cents * rates[from_ccy]
    return int(round(usd / rates[to_ccy]))


# ---------------------------------------------------------------
# Display formatting.
#
# Quantize constants are built once here; every format call shares
# them. Two-decimal currencies round half up to the cent, the
# three-decimal set renders mills, and the zero-decimal set renders
# whole units with no separator.
# ---------------------------------------------------------------

_CENT = Decimal("0.01")
_MILL = Decimal("0.001")
_WHOLE = Decimal("1")


def _quantum_for(ccy):
    """Return the display quantum and scale for a currency code."""
    if ccy in ZERO_DECIMAL:
        return _WHOLE, 1
    if ccy in THREE_DECIMAL:
        return _MILL, 1000
    return _CENT, 100


def format_cents(cents, ccy) -> str:
    """Render an integer minor-unit amount as display text."""
    quantum, scale = _quantum_for(ccy)
    amount = (Decimal(cents) / scale).quantize(quantum, rounding=ROUND_HALF_UP)
    return "%s %s" % (amount, ccy)
