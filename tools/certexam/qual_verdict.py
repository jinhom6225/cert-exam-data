"""Plan A7: 정기·상시 verdict for one 기능사 item from its qualExamSchd rows.

qualExamSchd has no 정기/상시 marker. A regular round is listed as a pair
(regular + extra registration window) sharing the exam dates
(qual_2026_T_7780); 상시 rounds are single rows and there are many of them
(qual_2026_T_7910: 41). The verdict is only used for regularOnly; dates
still come from getJMList and the grade lists.
"""

from typing import Dict, Iterable, List, Mapping, Optional, Tuple

from .textutil import normalize_date

MAX_UNPAIRED_FOR_REGULAR = 10

Signature = Tuple[Optional[str], Optional[str]]


def _to_int(value: Optional[str]) -> Optional[int]:
    try:
        return int((value or "").strip())
    except ValueError:
        return None


def _signature(row: Mapping[str, str]) -> Signature:
    return (
        normalize_date(row.get("docexamstartdt")),
        normalize_date(row.get("pracexamstartdt")),
    )


def _has_pair(rows: List[Mapping[str, str]]) -> bool:
    counts: Dict[Signature, int] = {}
    for row in rows:
        signature = _signature(row)
        if signature != (None, None):
            counts[signature] = counts.get(signature, 0) + 1
    return any(count >= 2 for count in counts.values())


def count_rounds(rows: Iterable[Mapping[str, str]], year: int) -> Tuple[int, int]:
    """(paired, unpaired) round counts for `year`; round 0 is ignored."""
    rounds: Dict[int, List[Mapping[str, str]]] = {}
    for row in rows:
        seq = _to_int(row.get("implseq"))
        row_year = _to_int(row.get("implyy"))
        if not seq or (row_year is not None and row_year != year):
            continue
        rounds.setdefault(seq, []).append(row)
    paired = sum(1 for members in rounds.values() if _has_pair(members))
    return paired, len(rounds) - paired


def is_gray_zone(paired: int, unpaired: int) -> bool:
    """Regular pairs plus a few unpaired rounds: the plan rule says True,
    but it is worth a log line until real answers confirm it."""
    return paired >= 1 and 1 <= unpaired <= MAX_UNPAIRED_FOR_REGULAR


def verdict_from_counts(paired: int, unpaired: int) -> Optional[bool]:
    """More than 10 unpaired rounds -> False (상시, alone or mixed with
    regular pairs). Otherwise any paired round -> True, even with up to 10
    unpaired rounds beside it (gray zone). No paired round and at most 10
    unpaired -> None (no verdict); no rows -> None."""
    if unpaired > MAX_UNPAIRED_FOR_REGULAR:
        return False
    return True if paired else None


def classify_qual_item(rows: Iterable[Mapping[str, str]], year: int) -> Optional[bool]:
    return verdict_from_counts(*count_rounds(rows, year))
