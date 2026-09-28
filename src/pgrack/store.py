"""Canonical CSV storage: one row per effective date, atomic writes."""

from __future__ import annotations

import csv
import io
import os
import shutil
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

from .parse import PRICE_FIELDS, DailyRecord

COLUMNS = ["effective_date", *PRICE_FIELDS, "retrieved_at_utc", "source_url"]
LOG_COLUMNS = ["logged_at_utc", "event", "effective_date", "details"]


class StoreError(Exception):
    pass


def _validate_rows(rows: list[dict], where: str) -> None:
    seen = set()
    prev = None
    for i, r in enumerate(rows, start=2):
        try:
            d = date.fromisoformat(r["effective_date"])
        except (ValueError, TypeError):
            raise StoreError(f"{where} line {i}: bad effective_date {r['effective_date']!r}")
        if d in seen:
            raise StoreError(f"{where} line {i}: duplicate effective_date {d}")
        if prev is not None and d < prev:
            raise StoreError(f"{where} line {i}: rows not in ascending date order")
        seen.add(d)
        prev = d
        for f in PRICE_FIELDS:
            v = r[f]
            if v != "":
                try:
                    Decimal(v)
                except Exception:
                    raise StoreError(f"{where} line {i}: {f} is not numeric: {v!r}")


def load(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames != COLUMNS:
        raise StoreError(f"{path}: unexpected header {reader.fieldnames}")
    rows = list(reader)
    _validate_rows(rows, str(path))
    return rows


def _serialise(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def _atomic_write(path: Path, rows: list[dict]) -> None:
    content = _serialise(rows)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        # Re-read what was written before it replaces the live file.
        load(Path(tmp))
        if path.exists():
            shutil.copy2(path, path.with_name(path.stem + ".prev" + path.suffix))
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def append_log(log_path: Path, logged_at: str, event: str, effective_date: str, details: str) -> None:
    new = not log_path.exists() or log_path.stat().st_size == 0
    with log_path.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        if new:
            w.writerow(LOG_COLUMNS)
        w.writerow([logged_at, event, effective_date, details])


def _same_prices(row: dict, fields: dict) -> bool:
    for f in PRICE_FIELDS:
        a, b = row[f], fields[f]
        if (a == "") != (b == ""):
            return False
        if a and Decimal(a) != Decimal(b):
            return False
    return True


def upsert(
    csv_path: Path,
    log_path: Path,
    record: DailyRecord,
    retrieved_at: str,
    source_url: str,
) -> tuple[str, str]:
    """Apply one parsed record. Returns (outcome, details).

    outcome is one of "appended", "revised", "unchanged".
    """
    rows = load(csv_path)
    fields = record.as_csv_fields()
    fields["retrieved_at_utc"] = retrieved_at
    fields["source_url"] = source_url
    key = fields["effective_date"]

    existing = next((r for r in rows if r["effective_date"] == key), None)
    if existing is None:
        rows.append(fields)
        rows.sort(key=lambda r: r["effective_date"])
        _atomic_write(csv_path, rows)
        details = " ".join(f"{f}={fields[f] or '(blank)'}" for f in PRICE_FIELDS)
        append_log(log_path, retrieved_at, "appended", key, details)
        return "appended", details

    if _same_prices(existing, fields):
        return "unchanged", "prices identical to stored row"

    changes = [
        f"{f}: {existing[f] or '(blank)'} -> {fields[f] or '(blank)'}"
        for f in PRICE_FIELDS
        if existing[f] != fields[f]
        and not (existing[f] and fields[f] and Decimal(existing[f]) == Decimal(fields[f]))
    ]
    existing.update(fields)
    _atomic_write(csv_path, rows)
    details = "; ".join(changes)
    append_log(log_path, retrieved_at, "revised", key, details)
    return "revised", details
