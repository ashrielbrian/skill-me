"""Region codes the marketplace ships to.

Codes follow ISO 3166-1 alpha-2. The list is the source of truth
for shipping eligibility, tax rule assembly, and address checks;
it changes only with a library release.
"""

REGIONS = [
    "AD", "AE", "AF", "AL", "AM", "AO", "AR", "AT", "AU", "AZ",
    "BA", "BB", "BD", "BE", "BF", "BG", "BH", "BJ", "BN", "BO",
    "BR", "BS", "BT", "BW", "BY", "BZ", "CA", "CD", "CG", "CH",
    "CI", "CL", "CM", "CN", "CO", "CR", "CU", "CV", "CY", "CZ",
    "DE", "DJ", "DK", "DO", "DZ", "EC", "EE", "EG", "ES", "ET",
    "FI", "FJ", "FR", "GA", "GB", "GE", "GH", "GM", "GN", "GR",
    "GT", "GY", "HN", "HR", "HT", "HU", "ID", "IE", "IL", "IN",
    "IQ", "IS", "IT", "JM", "JO", "JP", "KE", "KG", "KH", "KM",
    "KR", "KW", "KZ", "LA", "LB", "LI", "LK", "LR", "LS", "LT",
    "LU", "LV", "MA", "MC", "MD", "ME", "MG", "MK", "ML", "MN",
    "MR", "MT", "MU", "MV", "MW", "MX", "MY", "MZ", "NA", "NE",
    "NG", "NI", "NL", "NO", "NP", "NZ", "OM", "PA", "PE", "PG",
    "PH", "PK", "PL", "PT", "PY", "QA", "RO", "RS", "RU", "RW",
    "SA", "SE", "SG", "SI", "SK", "SN", "SV", "TH", "TN", "TR",
    "TT", "TZ", "UA", "UG", "US", "UY", "UZ", "VN", "ZA", "ZM",
]

_REGION_SET = frozenset(REGIONS)

# European Union members present in the shipping list; VAT-style
# invoicing rules key off this grouping.
EU_REGIONS = frozenset({
    "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "ES", "FI",
    "FR", "GR", "HR", "HU", "IE", "IT", "LT", "LU", "LV", "MT",
    "NL", "PL", "PT", "RO", "SE", "SI", "SK",
})


# Coarse operating groupings used for routing and reporting.
AMERICAS = frozenset({
    "AR", "BB", "BO", "BR", "BS", "BZ", "CA", "CL", "CO", "CR",
    "CU", "DO", "EC", "GT", "GY", "HN", "HT", "JM", "MX", "NI",
    "PA", "PE", "PY", "SV", "TT", "US", "UY",
})

APAC = frozenset({
    "AU", "BD", "BN", "BT", "CN", "FJ", "ID", "IN", "JP", "KG",
    "KH", "KR", "KZ", "LA", "LK", "MN", "MV", "MY", "NP", "NZ",
    "PG", "PH", "PK", "SG", "TH", "UZ", "VN",
})


def grouping_for(code):
    """Operating grouping for a region: americas, apac, or emea."""
    if code in AMERICAS:
        return "americas"
    if code in APAC:
        return "apac"
    return "emea"


def is_known_region(code):
    """True when the code is in the shipping list."""
    return code in _REGION_SET


def is_eu(code):
    """True when the region invoices under the EU grouping."""
    return code in EU_REGIONS


def region_count():
    """Size of the shipping list; used by health reports."""
    return len(REGIONS)
