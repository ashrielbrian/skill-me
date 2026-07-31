"""Unit tests for query normalization."""

from services.catalog import config
from services.catalog.search.normalize import join_terms, normalize_query, tokenize


def test_lowercases_and_strips_symbols():
    assert normalize_query("Blue!! Kettle??") == "blue kettle"


def test_collapses_whitespace():
    assert normalize_query("  desk   lamp ") == "desk lamp"


def test_keeps_digits_and_dashes():
    assert normalize_query("usb-c 65w") == "usb-c 65w"


def test_truncates_long_queries():
    assert len(normalize_query("a" * 500)) <= config.MAX_QUERY_LENGTH


def test_tokenize_deduplicates_terms():
    assert tokenize("mug mug cup") == ["mug", "cup"]


def test_tokenize_drops_bare_dashes():
    assert tokenize("desk - lamp") == ["desk", "lamp"]


def test_tokenize_caps_term_count():
    text = " ".join("t%d" % i for i in range(30))
    assert len(tokenize(text)) == config.MAX_TERMS


def test_join_terms_round_trip():
    assert join_terms(["desk", "lamp"]) == "desk lamp"
