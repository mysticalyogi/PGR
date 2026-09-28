"""Deterministic parser for the Petro-Canada Daily rack price table.

The page is treated strictly as data. Every structural assumption is checked,
and anything unexpected raises ParseError rather than producing a guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from bs4 import BeautifulSoup, Tag

LOCATION = "Prince George, BC"

# Source heading -> CSV column. Matched on exact text after whitespace/case
# normalisation only; a renamed heading must fail, not fuzzy-match.
REQUIRED_HEADINGS = {
    "REG 87": "reg_87",
    "MID 89": "mid_89",
    "SUP 91": "sup_91",
    "ULS Diesel": "uls_diesel",
    "ULSD#1": "ulsd_1",
}
PRICE_FIELDS = list(REQUIRED_HEADINGS.values())

# Plausible cents-per-litre range. Guards against a silent unit change
# (e.g. dollars per litre) being stored as cents.
MIN_CENTS = Decimal("30")
MAX_CENTS = Decimal("500")

BLANK_TOKENS = {"", "-", "–", "—", "n/a", "na"}
PANEL_LABEL_RE = re.compile(r"^(daily|weekly|monthly|historical)(\s+rack\s+prices?)?$", re.I)
DAILY_LABEL_RE = re.compile(r"^daily(\s+rack\s+prices?)?$", re.I)

_MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("jan", "january"), ("feb", "february"), ("mar", "march"),
            ("apr", "april"), ("may",), ("jun", "june"), ("jul", "july"),
            ("aug", "august"), ("sep", "sept", "september"), ("oct", "october"),
            ("nov", "november"), ("dec", "december"),
        ],
        start=1,
    )
    for m in names
}
_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))
# "Effective date: September 28, 2026", "Effective Sept. 28, 2026",
# "Effective: 2026-09-28", "Effective 28 September 2026".
_EFFECTIVE_RE = re.compile(
    r"effective(?:\s+date)?\s*[:\-]?\s*(?:[A-Za-z]+,?\s+)??"
    r"(?P<d>"
    rf"(?:(?:{_MONTH_ALT})\.?\s+\d{{1,2}},?\s+\d{{4}})"
    rf"|(?:\d{{1,2}}\s+(?:{_MONTH_ALT})\.?,?\s+\d{{4}})"
    r"|(?:\d{4}-\d{2}-\d{2})"
    r")",
    re.I,
)


class ParseError(Exception):
    pass


@dataclass(frozen=True)
class DailyRecord:
    effective_date: date
    prices: dict  # csv column -> Decimal | None

    def as_csv_fields(self) -> dict:
        out = {"effective_date": self.effective_date.isoformat()}
        for f in PRICE_FIELDS:
            v = self.prices[f]
            out[f] = "" if v is None else str(v)
        return out


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def _key(text: str) -> str:
    return _norm(text).casefold()


def _text(el: Tag) -> str:
    return _norm(el.get_text(" "))


def _parse_date(raw: str) -> date:
    raw = _norm(raw)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return date.fromisoformat(raw)
    m = re.fullmatch(r"([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})", raw)
    if m:
        mon, day, year = m.group(1), m.group(2), m.group(3)
    else:
        m = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]+)\.?,?\s+(\d{4})", raw)
        if not m:
            raise ParseError(f"unrecognised effective date format: {raw!r}")
        day, mon, year = m.group(1), m.group(2), m.group(3)
    month = _MONTHS.get(mon.casefold())
    if month is None:
        raise ParseError(f"unrecognised month in effective date: {raw!r}")
    try:
        return date(int(year), month, int(day))
    except ValueError as e:
        raise ParseError(f"invalid effective date {raw!r}: {e}") from None


def _is_label(el: Tag, pattern: re.Pattern) -> bool:
    # Only short, leaf-ish elements count as labels (tabs, headings, captions).
    if el.find(["table", "div", "section"]):
        return False
    return bool(pattern.match(_text(el)))


def _daily_scopes(soup: BeautifulSoup) -> list[tuple[Tag, Tag]]:
    """Return (context_element, table) pairs associated with a Daily label."""
    labels = [
        el for el in soup.find_all(True)
        if el.name not in ("html", "body", "script", "style", "table", "tr", "tbody", "thead")
        and _is_label(el, DAILY_LABEL_RE)
    ]
    # Collapse nested matches (e.g. <li><a>Daily</a></li>) to the innermost.
    labels = [el for el in labels if not any(_is_label(c, DAILY_LABEL_RE) for c in el.find_all(True))]

    found: list[tuple[Tag, Tag]] = []
    for label in labels:
        scope = None
        # 1. Tab pattern: aria-controls / href="#panel" pointing at a panel.
        target_id = label.get("aria-controls")
        if not target_id:
            href = label.get("href") or ""
            if href.startswith("#") and len(href) > 1:
                target_id = href[1:]
        if target_id:
            panel = soup.find(id=target_id)
            if panel is not None:
                scope = panel
        # 2. Panel labelled by this element.
        if scope is None and label.get("id"):
            panel = soup.find(attrs={"aria-labelledby": label["id"]})
            if panel is not None:
                scope = panel
        if scope is not None:
            tables = scope.find_all("table")
            if len(tables) != 1:
                raise ParseError(f"Daily panel contains {len(tables)} tables, expected 1")
            found.append((scope, tables[0]))
            continue
        # 3. Caption inside the table.
        if label.name == "caption" and label.parent is not None and label.parent.name == "table":
            table = label.parent
            found.append((table.parent or table, table))
            continue
        # 4. Heading followed by a table, with no other panel label in between.
        table = label.find_next("table")
        if table is None:
            continue
        between = False
        for el in label.find_all_next(True):
            if el is table:
                break
            if el.name not in ("script", "style") and _is_label(el, PANEL_LABEL_RE):
                between = True
                break
        if between:
            continue
        ctx = label.parent
        while ctx is not None and table not in ctx.descendants:
            ctx = ctx.parent
        found.append((ctx or table, table))
    return found


def _select_daily(soup: BeautifulSoup) -> tuple[Tag, Tag]:
    scopes = _daily_scopes(soup)
    distinct = []
    for ctx, table in scopes:
        if not any(table is t for _, t in distinct):
            distinct.append((ctx, table))
    if not distinct:
        raise ParseError("no table labelled 'Daily' found")
    if len(distinct) > 1:
        raise ParseError(f"{len(distinct)} different tables are labelled 'Daily'; ambiguous")
    return distinct[0]


def _rows(table: Tag) -> list[list[Tag]]:
    rows = []
    for tr in table.find_all("tr"):
        if tr.find_parent("table") is not table:
            continue  # nested table
        rows.append(tr.find_all(["th", "td"], recursive=False))
    return rows


def _header_map(rows: list[list[Tag]]) -> tuple[int, dict]:
    wanted = {_key(h): col for h, col in REQUIRED_HEADINGS.items()}
    best = None
    for i, cells in enumerate(rows):
        texts = [_key(_text(c)) for c in cells]
        hits = sum(1 for t in texts if t in wanted)
        if hits and (best is None or hits > best[1]):
            best = (i, hits, texts)
    if best is None:
        raise ParseError("Daily table has no recognisable header row")
    idx, _, texts = best
    mapping = {}
    for pos, t in enumerate(texts):
        if t in wanted:
            col = wanted[t]
            if col in mapping:
                raise ParseError(f"heading {t!r} appears more than once")
            mapping[col] = pos
    missing = [h for h, col in REQUIRED_HEADINGS.items() if col not in mapping]
    if missing:
        raise ParseError(f"required heading(s) missing or renamed: {', '.join(missing)}")
    if any(c.has_attr("colspan") and int(c.get("colspan", 1)) > 1 for c in rows[idx]):
        raise ParseError("header row uses colspan; column mapping would be ambiguous")
    return idx, mapping


def _price(raw: str, heading: str) -> Decimal | None:
    s = _norm(raw)
    if s.casefold() in BLANK_TOKENS:
        return None
    s = re.sub(r"\s*(¢|c/l|¢/l)$", "", s, flags=re.I)
    if not re.fullmatch(r"\d+(\.\d+)?", s):
        raise ParseError(f"{heading}: non-numeric value {raw!r}")
    try:
        v = Decimal(s)
    except InvalidOperation:
        raise ParseError(f"{heading}: non-numeric value {raw!r}") from None
    if not (MIN_CENTS <= v <= MAX_CENTS):
        raise ParseError(f"{heading}: value {v} outside plausible cents/L range")
    return v


def _effective_date(ctx: Tag) -> date:
    found = {_parse_date(m.group("d")) for m in _EFFECTIVE_RE.finditer(_text(ctx))}
    if not found:
        raise ParseError("no effective date found in the Daily panel")
    if len(found) > 1:
        raise ParseError(f"multiple effective dates in the Daily panel: {sorted(found)}")
    return found.pop()


def parse_daily(html: str) -> DailyRecord:
    soup = BeautifulSoup(html, "lxml")
    for el in soup(["script", "style", "noscript", "template"]):
        el.decompose()
    ctx, table = _select_daily(soup)
    rows = _rows(table)
    header_idx, mapping = _header_map(rows)

    target = _key(LOCATION)
    matches = [
        cells for cells in rows[header_idx + 1:]
        if any(_key(_text(c)) == target for c in cells)
    ]
    if len(matches) != 1:
        raise ParseError(f"expected exactly one '{LOCATION}' row, found {len(matches)}")
    cells = matches[0]
    if _key(_text(cells[0])) != target:
        raise ParseError(f"'{LOCATION}' is not in the row's location column")
    if len(cells) != len(rows[header_idx]):
        raise ParseError(
            f"'{LOCATION}' row has {len(cells)} cells but header has {len(rows[header_idx])}"
        )

    heading_for = {col: h for h, col in REQUIRED_HEADINGS.items()}
    prices = {col: _price(_text(cells[pos]), heading_for[col]) for col, pos in mapping.items()}
    if all(v is None for v in prices.values()):
        raise ParseError(f"all prices blank for '{LOCATION}'; page looks incomplete")

    return DailyRecord(_effective_date(ctx), prices)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
