"""Merge today's results with the previous exams.json (plan D24 ①, A2)."""

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Mapping, Optional, Sequence, Set, Tuple

from .grades import group_of
from .parse_grade import GroupRounds
from .parse_item import ItemSchedule
from .parse_lists import JmEntry
from .rounds import BASIS_ITEM, Round, RoundKey, sort_rounds

Item = Dict[str, object]

CRAFTSMAN = "기능사"


@dataclass(frozen=True)
class MergeInputs:
    universe: Sequence[JmEntry]
    grade_rounds: GroupRounds
    item_schedules: Mapping[str, ItemSchedule] = field(default_factory=dict)
    fees: Mapping[str, Optional[Dict[str, int]]] = field(default_factory=dict)
    previous_items: Mapping[str, Item] = field(default_factory=dict)
    always_names: FrozenSet[str] = frozenset()


def _item_rounds(
    schedule: Optional[ItemSchedule], previous: Optional[Item]
) -> Tuple[List[Round], Set[int]]:
    """Today's getJMList rounds, else the item-basis rounds kept from the
    previous file. Also returns the years those item rows speak for: grade
    rounds of those years are not mixed in.

    An empty getJMList answer (no rows at all) does not replace accumulated
    item rounds; only with no previous item data does the item fall back to
    grade rounds."""
    if schedule is not None and (schedule.rounds or schedule.years):
        return list(schedule.rounds), set(schedule.years)
    kept = [
        r
        for r in (previous or {}).get("rounds", []) or []
        if isinstance(r, dict) and r.get("basis") == BASIS_ITEM
    ]
    return kept, {int(r["year"]) for r in kept}


def _regular_only(
    schedule: Optional[ItemSchedule],
    previous: Optional[Item],
    name: str,
    grade: str,
    always_names: FrozenSet[str],
) -> Optional[bool]:
    """getJMList verdict today > previous value > default.

    상시 검정 exists only for 기능사, so every other grade defaults to True.
    기능사 falls back to the 15088943 name hint, then None (unknown)."""
    if schedule is not None and schedule.regular_only is not None:
        return schedule.regular_only
    previous_value = (previous or {}).get("regularOnly")
    if isinstance(previous_value, bool):
        return previous_value
    if grade != CRAFTSMAN:
        return True
    return False if name in always_names else None


def _grade_rounds_for(
    grade: str, regular_only: Optional[bool], covered: Set[int], grade_rounds: GroupRounds
) -> Dict[RoundKey, Round]:
    """Grade-level rounds fill only years without item data. 상시 items
    (regularOnly False) take no grade rounds: those are regular-round dates."""
    group = group_of(grade)
    if group is None or regular_only is False:
        return {}
    return {key: r for key, r in grade_rounds.get(group, {}).items() if key[0] not in covered}


def merge_item(entry: JmEntry, inputs: MergeInputs) -> Item:
    jm_cd = str(entry["jmCd"])
    name = str(entry["name"])
    grade = str(entry["grade"])
    schedule = inputs.item_schedules.get(jm_cd)
    previous = inputs.previous_items.get(jm_cd)
    item_rounds, covered = _item_rounds(schedule, previous)
    regular_only = _regular_only(schedule, previous, name, grade, inputs.always_names)
    merged = _grade_rounds_for(grade, regular_only, covered, inputs.grade_rounds)
    merged.update({(int(r["year"]), int(r["seq"])): r for r in item_rounds})
    if jm_cd in inputs.fees:
        fee = inputs.fees[jm_cd]
    else:
        fee = (previous or {}).get("fee")
    return {
        "jmCd": jm_cd,
        "name": name,
        "grade": grade,
        "field": entry.get("field"),
        "subField": entry.get("subField"),
        "fee": fee,
        "regularOnly": regular_only,
        "rounds": sort_rounds(merged.values()),
    }


def merge_items(inputs: MergeInputs) -> List[Item]:
    ordered = sorted(inputs.universe, key=lambda e: str(e["jmCd"]))
    return [merge_item(entry, inputs) for entry in ordered]


def universe_from_previous(previous: Optional[Mapping[str, object]]) -> List[JmEntry]:
    items = (previous or {}).get("items") or []
    return [
        {key: item.get(key) for key in ("jmCd", "name", "grade", "field", "subField")}
        for item in items
        if isinstance(item, dict)
    ]
