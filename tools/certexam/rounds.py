"""Round records shared by all schedule sources."""

import re
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

from .textutil import clean_text, first_value, normalize_date

Round = Dict[str, object]
RoundKey = Tuple[int, int]

BASIS_ITEM = "item"
BASIS_GRADE = "grade"

KIND_REGULAR = "정기"
KIND_ALWAYS = "상시"

_TITLE_YEAR_ROUND = re.compile(r"(\d{4})\s*년도\s*제\s*(\d+)\s*회")
_TITLE_PLAN = re.compile(r"(\d{4})\s*년(?!도).*?(\d+)\s*회\s*$")
_NO_DATE = "9999-99-99"


def parse_title(text: Optional[str]) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    """Return (year, seq, kind) from titles such as `2026년 정기 기사 1회`
    or `기사,산업기사(2026년도 제1회)`. Missing parts are None."""
    title = clean_text(text) or ""
    kind = KIND_ALWAYS if KIND_ALWAYS in title else KIND_REGULAR if KIND_REGULAR in title else None
    match = _TITLE_YEAR_ROUND.search(title) or _TITLE_PLAN.search(title)
    if not match:
        return None, None, kind
    return int(match.group(1)), int(match.group(2)), kind


def _phase(values: Mapping[str, Optional[str]]) -> Optional[Dict[str, Optional[str]]]:
    return dict(values) if any(v is not None for v in values.values()) else None


def _date(row: Mapping[str, str], *keys: str) -> Optional[str]:
    return normalize_date(first_value(row, *keys))


def round_from_row(
    row: Mapping[str, str], year: int, seq: int, label: str, basis: str
) -> Round:
    """Build a round from any source row (field names already lower-cased).

    Grade lists use single-day fields (`docexamdt`, `pracpassdt`) and the
    misspelt `docsubmitentdt`; getJMList and qualExamSchd use start/end pairs.
    """
    written = _phase(
        {
            "regStart": _date(row, "docregstartdt"),
            "regEnd": _date(row, "docregenddt"),
            "examStart": _date(row, "docexamstartdt", "docexamdt"),
            "examEnd": _date(row, "docexamenddt", "docexamdt"),
            "pass": _date(row, "docpassdt"),
        }
    )
    doc_submit = _phase(
        {
            "start": _date(row, "docsubmitstartdt"),
            "end": _date(row, "docsubmitenddt", "docsubmitentdt"),
        }
    )
    practical = _phase(
        {
            "regStart": _date(row, "pracregstartdt"),
            "regEnd": _date(row, "pracregenddt"),
            "examStart": _date(row, "pracexamstartdt"),
            "examEnd": _date(row, "pracexamenddt"),
            "passStart": _date(row, "pracpassstartdt", "pracpassdt"),
            "passEnd": _date(row, "pracpassenddt", "pracpassdt"),
        }
    )
    return {
        "year": year,
        "seq": seq,
        "label": label,
        "basis": basis,
        "written": written,
        "docSubmit": doc_submit,
        "practical": practical,
    }


def has_any_date(round_: Round) -> bool:
    return any(round_.get(name) is not None for name in ("written", "docSubmit", "practical"))


def reg_start(round_: Round, phase: str) -> Optional[str]:
    value = round_.get(phase)
    return value.get("regStart") if isinstance(value, dict) else None


def _written_window(round_: Round) -> Tuple[str, str]:
    value = round_.get("written")
    if not isinstance(value, dict):
        return _NO_DATE, _NO_DATE
    return value.get("regStart") or _NO_DATE, value.get("regEnd") or _NO_DATE


def _merge_practical_reg(rows: List[Round]) -> Round:
    """First row of the set, with the practical registration window widened
    to cover every row: regStart = min, regEnd = max (nulls ignored)."""
    base = rows[0]
    practical = base.get("practical")
    if not isinstance(practical, dict):
        return base
    phases = [r.get("practical") for r in rows if isinstance(r.get("practical"), dict)]
    starts = [p["regStart"] for p in phases if p.get("regStart")]
    ends = [p["regEnd"] for p in phases if p.get("regEnd")]
    merged = {
        **practical,
        "regStart": min(starts) if starts else None,
        "regEnd": max(ends) if ends else None,
    }
    return {**base, "practical": merged}


def pick_regular_rows(rounds: Iterable[Round]) -> Dict[RoundKey, Round]:
    """Keep one record per (year, seq).

    Sources list a round on two or three rows. Rows that share the written
    registration window are one set that splits the practical registration
    (getPEList 138회: 03-03~03-06 and 03-30~04-02; getJMList for 0752 gives
    03-03~04-02). A row with a later written window is the extra
    registration window, not used in 1.0. Row order is not reliable
    (qualExamSchd lists the extra window first), so the set with the
    earliest written window wins and its practical windows are merged.
    """
    grouped: Dict[RoundKey, List[Round]] = {}
    for round_ in rounds:
        grouped.setdefault((int(round_["year"]), int(round_["seq"])), []).append(round_)
    picked: Dict[RoundKey, Round] = {}
    for key, rows in grouped.items():
        sets: Dict[Tuple[str, str], List[Round]] = {}
        for round_ in rows:
            sets.setdefault(_written_window(round_), []).append(round_)
        earliest = min(sets)
        picked[key] = _merge_practical_reg(sets[earliest])
    return picked


def sort_rounds(rounds: Iterable[Round]) -> List[Round]:
    return sorted(rounds, key=lambda r: (int(r["year"]), int(r["seq"])))
