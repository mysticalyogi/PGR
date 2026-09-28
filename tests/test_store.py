from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from pgrack.cli import main
from pgrack.parse import DailyRecord
from pgrack.store import COLUMNS, StoreError, load, upsert

URL = "https://www.petro-canada.ca/en/business/rack-prices"
FIX = Path(__file__).parent / "fixtures"


def rec(d="2026-09-28", mid="161.5", reg="151.2"):
    p = {
        "reg_87": Decimal(reg),
        "mid_89": None if mid is None else Decimal(mid),
        "sup_91": Decimal("171.4"),
        "uls_diesel": Decimal("158.9"),
        "ulsd_1": Decimal("166.3"),
    }
    return DailyRecord(date.fromisoformat(d), p)


@pytest.fixture
def paths(tmp_path):
    csv_path = tmp_path / "pg_rack_rates.csv"
    csv_path.write_text(",".join(COLUMNS) + "\n")
    return csv_path, tmp_path / "run_log.csv"


def test_append_then_same_date_rerun_is_noop(paths):
    csv_path, log = paths
    assert upsert(csv_path, log, rec(), "2026-09-28T08:17:00Z", URL)[0] == "appended"
    before = csv_path.read_bytes()
    assert upsert(csv_path, log, rec(), "2026-09-28T10:47:00Z", URL)[0] == "unchanged"
    assert csv_path.read_bytes() == before
    assert len(load(csv_path)) == 1


def test_trailing_zero_difference_is_not_a_revision(paths):
    csv_path, log = paths
    upsert(csv_path, log, rec(reg="151.2"), "t1", URL)
    assert upsert(csv_path, log, rec(reg="151.20"), "t2", URL)[0] == "unchanged"


def test_same_date_changed_value_revises_row(paths):
    csv_path, log = paths
    upsert(csv_path, log, rec(), "2026-09-28T08:17:00Z", URL)
    outcome, details = upsert(csv_path, log, rec(reg="152.0"), "2026-09-28T10:47:00Z", URL)
    assert outcome == "revised"
    assert "reg_87: 151.2 -> 152.0" in details
    rows = load(csv_path)
    assert len(rows) == 1 and rows[0]["reg_87"] == "152.0"
    assert rows[0]["retrieved_at_utc"] == "2026-09-28T10:47:00Z"
    assert "revised" in log.read_text()
    assert (csv_path.parent / "pg_rack_rates.prev.csv").read_text().count("151.2") == 1


def test_blank_to_value_is_a_revision(paths):
    csv_path, log = paths
    upsert(csv_path, log, rec(mid=None), "t1", URL)
    assert load(csv_path)[0]["mid_89"] == ""
    assert upsert(csv_path, log, rec(mid="161.5"), "t2", URL)[0] == "revised"


def test_rows_sorted_by_date(paths):
    csv_path, log = paths
    upsert(csv_path, log, rec("2026-09-28"), "t", URL)
    upsert(csv_path, log, rec("2026-09-26"), "t", URL)
    upsert(csv_path, log, rec("2026-09-27"), "t", URL)
    assert [r["effective_date"] for r in load(csv_path)] == ["2026-09-26", "2026-09-27", "2026-09-28"]


def test_corrupt_existing_csv_is_not_overwritten(paths):
    csv_path, log = paths
    csv_path.write_text(",".join(COLUMNS) + "\n2026-09-28,1,,1,1,1,t,u\n2026-09-28,1,,1,1,1,t,u\n")
    before = csv_path.read_bytes()
    with pytest.raises(StoreError, match="duplicate"):
        upsert(csv_path, log, rec("2026-09-29"), "t", URL)
    assert csv_path.read_bytes() == before


def test_parse_failure_leaves_dataset_byte_identical(paths, tmp_path):
    csv_path, log = paths
    upsert(csv_path, log, rec(), "t", URL)
    before = csv_path.read_bytes()
    bad = tmp_path / "bad.html"
    bad.write_text((FIX / "synthetic_tabs.html").read_text().replace("<th>MID 89</th>", "<th>MID</th>", 1))
    assert main(["parse-file", str(bad), "--upsert-into", str(csv_path)]) == 1
    assert csv_path.read_bytes() == before


def test_parse_file_dry_run_does_not_touch_real_csv(paths, tmp_path):
    csv_path, log = paths
    good = tmp_path / "good.html"
    good.write_text((FIX / "synthetic_tabs.html").read_text().replace("MIDVAL", "161.5"))
    before = csv_path.read_bytes()
    assert main(["parse-file", str(good), "--upsert-into", str(csv_path)]) == 0
    assert csv_path.read_bytes() == before
