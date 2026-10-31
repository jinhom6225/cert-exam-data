"""getJMList (per item, current year) -> item-basis rounds and verdict."""

from dataclasses import dataclass
from typing import FrozenSet, Iterable, List, Mapping, Optional, Tuple

from .rounds import (
    BASIS_ITEM,
    KIND_ALWAYS,
    KIND_REGULAR,
    Round,
    parse_title,
    pick_regular_rows,
    round_from_row,
    sort_rounds,
)
from .textutil import clean_text


@dataclass(frozen=True)
class ItemSchedule:
    rounds: Tuple[Round, ...]
    regular_only: Optional[bool]
    years: FrozenSet[int]


def parse_item_schedule(rows: Iterable[Mapping[str, str]]) -> ItemSchedule:
    """Regular rounds only; any 상시 row makes regularOnly False.

    Rows with round 0 (written-exam exemption) or an unreadable title are
    skipped. `years` lists every year the response speaks for, so grade-level
    rounds of those years are not mixed in for this item.
    """
    rounds: List[Round] = []
    years = set()
    has_regular = has_always = False
    for row in rows:
        title = clean_text(row.get("implplannm"))
        year, seq, kind = parse_title(title)
        if year is not None:
            years.add(year)
        if kind == KIND_ALWAYS:
            has_always = True
            continue
        if kind == KIND_REGULAR:
            has_regular = True
        if year is None or not seq:
            continue
        rounds.append(round_from_row(row, year, seq, title or "", BASIS_ITEM))
    verdict = False if has_always else True if has_regular else None
    picked = pick_regular_rows(rounds).values()
    return ItemSchedule(tuple(sort_rounds(picked)), verdict, frozenset(years))
