from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from .models import ChatFamily, ChatFormat, Clock, DateOrder

DATE_PART = r"(?P<d1>\d{1,4})\s*[/.\-]\s*(?P<d2>\d{1,2})\s*[/.\-]\s*(?P<d3>\d{2,4})"
TIME_PART = r"(?P<h>\d{1,2}):(?P<mi>\d{2})(?::(?P<s>\d{2}))?"
AMPM_PART = r"(?P<ampm>[AaPp]\.?\s?[Mm]\.?)"

BRACKET_PATTERN = re.compile(
    rf"^\[(?P<date>{DATE_PART})\s*,\s*(?P<time>{TIME_PART})\s*(?:{AMPM_PART})?\s*\]\s*(?P<rest>.*)$"
)
DASH_PATTERN = re.compile(
    rf"^(?P<date>{DATE_PART})\s*,\s*(?P<time>{TIME_PART})\s*(?:{AMPM_PART})?\s+-\s+(?P<rest>.*)$"
)

FAMILY_PATTERNS: dict[ChatFamily, re.Pattern[str]] = {
    ChatFamily.BRACKET: BRACKET_PATTERN,
    ChatFamily.DASH: DASH_PATTERN,
}


@dataclass
class _Stats:
    matches: int = 0
    day_evidence: int = 0
    month_evidence: int = 0
    year_first: int = 0
    has_ampm: int = 0


def _collect(pattern: re.Pattern[str], line: str, stats: _Stats) -> bool:
    match = pattern.match(line)
    if not match:
        return False
    stats.matches += 1
    d1 = int(match.group("d1"))
    d2 = int(match.group("d2"))
    if len(match.group("d1")) == 4:
        stats.year_first += 1
    else:
        if d1 > 12:
            stats.day_evidence += 1
        if d2 > 12:
            stats.month_evidence += 1
    if match.group("ampm"):
        stats.has_ampm += 1
    return True


def detect_format(
    lines: Iterable[str],
    *,
    date_order: DateOrder | None = None,
    default_order: DateOrder = DateOrder.MONTH_FIRST,
) -> ChatFormat:
    stats = {family: _Stats() for family in FAMILY_PATTERNS}
    for line in lines:
        for family, pattern in FAMILY_PATTERNS.items():
            _collect(pattern, line, stats[family])

    ranked = sorted(stats.items(), key=lambda item: (-item[1].matches, item[0].value))
    family, chosen = ranked[0]
    if chosen.matches == 0:
        raise ValueError(
            "no WhatsApp message headers found. Expected lines like "
            "'[10/20/26, 3:37:39 PM] Alice: hello' or '10/20/26, 3:37 - Alice: hello'."
        )

    ambiguous = False
    if date_order is not None:
        order = date_order
    elif chosen.year_first == chosen.matches:
        order = DateOrder.YEAR_FIRST
    elif chosen.day_evidence:
        order = DateOrder.DAY_FIRST
    elif chosen.month_evidence:
        order = DateOrder.MONTH_FIRST
    else:
        order = default_order
        ambiguous = True

    clock = Clock.H12 if chosen.has_ampm else Clock.H24
    return ChatFormat(
        family=family,
        date_order=order,
        clock=clock,
        ambiguous=ambiguous,
        pattern=FAMILY_PATTERNS[family],
    )


def _two_digit_year(year: int) -> int:
    return 2000 + year if year < 100 else year


def _build_date(year: int, month: int, day: int) -> datetime | None:
    try:
        return datetime(year, month, day)
    except ValueError:
        return None


def build_timestamp(
    d1: str,
    d2: str,
    d3: str,
    hour: int,
    minute: int,
    second: int,
    ampm: str | None,
    order: DateOrder,
) -> datetime | None:
    year = _two_digit_year(int(d3))
    if len(d1) == 4:
        year = int(d1)
        month, day = int(d2), int(d3)
    elif order is DateOrder.DAY_FIRST:
        day, month = int(d1), int(d2)
    else:
        month, day = int(d1), int(d2)

    if ampm:
        marker = ampm.replace(".", "").replace(" ", "").upper()
        hour = hour % 12
        if marker == "PM":
            hour += 12

    stamp = _build_date(year, month, day)
    if stamp is None and len(d1) != 4:
        stamp = _build_date(year, day, month)
    if stamp is None:
        return None
    return stamp.replace(hour=hour, minute=minute, second=second, microsecond=0)
