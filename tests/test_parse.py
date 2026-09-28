from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from pgrack.parse import ParseError, parse_daily

FIX = Path(__file__).parent / "fixtures"


def tabs(mid="161.50", **replace):
    html = (FIX / "synthetic_tabs.html").read_text().replace("MIDVAL", mid)
    for old, new in replace.items():
        html = html.replace(old.replace("_", " "), new)
    return html


def test_tabs_reads_daily_panel_only():
    rec = parse_daily(tabs())
    assert rec.effective_date == date(2026, 9, 28)
    assert rec.prices == {
        "reg_87": Decimal("151.20"),
        "mid_89": Decimal("161.50"),
        "sup_91": Decimal("171.40"),
        "uls_diesel": Decimal("158.90"),
        "ulsd_1": Decimal("166.30"),
    }


def test_mid_89_blank_stays_blank():
    rec = parse_daily(tabs(mid=""))
    assert rec.prices["mid_89"] is None
    assert rec.as_csv_fields()["mid_89"] == ""


def test_heading_layout_maps_by_heading_not_position():
    rec = parse_daily((FIX / "synthetic_heading.html").read_text())
    assert rec.effective_date == date(2026, 9, 28)
    assert rec.prices["reg_87"] == Decimal("151.2")
    assert rec.prices["ulsd_1"] == Decimal("166.3")
    assert rec.prices["mid_89"] is None


@pytest.mark.parametrize(
    "old,new,msg",
    [
        ("<th>MID 89</th>", "<th>MID 88</th>", "MID 89"),  # renamed
        ("<th>ULSD#1</th>", "", "ULSD#1"),  # missing
        ("<th>REG 87</th>", "<th>REG87X</th>", "REG 87"),
    ],
)
def test_missing_or_renamed_heading_fails(old, new, msg):
    html = tabs().replace(old, new, 1)
    with pytest.raises(ParseError, match=msg):
        parse_daily(html)


def test_duplicate_prince_george_row_fails():
    html = tabs().replace("Kamloops, BC", "Prince George, BC", 1)
    with pytest.raises(ParseError, match="exactly one"):
        parse_daily(html)


def test_missing_prince_george_row_fails():
    html = tabs().replace("Prince George, BC", "Prince Rupert, BC", 1)
    with pytest.raises(ParseError, match="exactly one"):
        parse_daily(html)


def test_location_label_must_be_exact():
    html = tabs().replace("Prince George, BC", "Prince George BC", 1)
    with pytest.raises(ParseError):
        parse_daily(html)


def test_missing_effective_date_fails():
    html = tabs().replace("Effective date: September 28, 2026", "", 1)
    with pytest.raises(ParseError, match="effective date"):
        parse_daily(html)


def test_non_numeric_value_fails():
    with pytest.raises(ParseError, match="non-numeric"):
        parse_daily(tabs(mid="TBD"))


def test_implausible_unit_fails():
    with pytest.raises(ParseError, match="range"):
        parse_daily(tabs(mid="1.615"))


def test_no_daily_label_fails():
    html = tabs().replace(">Daily<", ">Today<")
    with pytest.raises(ParseError, match="Daily"):
        parse_daily(html)


def test_page_text_is_not_instructions():
    # The fixture includes an injected sentence; it must have no effect.
    assert parse_daily(tabs()).prices["reg_87"] == Decimal("151.20")
