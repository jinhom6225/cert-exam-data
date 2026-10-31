"""Grade-level schedules: getPEList/getMCList/getEList/getCList and qualExamSchd."""

from typing import Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from .grades import GROUP_C, GROUP_E, GROUP_LABEL, GROUP_MC, GROUP_PE, group_from_text
from .rounds import (
    BASIS_GRADE,
    KIND_ALWAYS,
    Round,
    RoundKey,
    has_any_date,
    parse_title,
    pick_regular_rows,
    reg_start,
    round_from_row,
)
from .textutil import clean_text

GRADE_OPERATIONS = {
    "getPEList": GROUP_PE,
    "getMCList": GROUP_MC,
    "getEList": GROUP_E,
    "getCList": GROUP_C,
}

GroupRounds = Dict[str, Dict[RoundKey, Round]]


def grade_label(group: str, year: int, seq: int) -> str:
    return f"{year}년 정기 {GROUP_LABEL[group]} {seq}회"


def parse_grade_list(group: str, rows: Iterable[Mapping[str, str]]) -> Dict[RoundKey, Round]:
    """Regular rounds of one grade list; 상시 and exemption rows are dropped."""
    rounds: List[Round] = []
    for row in rows:
        year, seq, kind = parse_title(row.get("description"))
        if kind == KIND_ALWAYS or year is None or not seq:
            continue
        built = round_from_row(row, year, seq, grade_label(group, year, seq), BASIS_GRADE)
        if has_any_date(built):
            rounds.append(built)
    return pick_regular_rows(rounds)


def _to_int(value: Optional[str]) -> Optional[int]:
    try:
        return int((value or "").strip())
    except ValueError:
        return None


def _exam_signature(round_: Round) -> Tuple[Optional[str], Optional[str]]:
    def exam_start(phase: str) -> Optional[str]:
        value = round_.get(phase)
        return value.get("examStart") if isinstance(value, dict) else None

    return exam_start("written"), exam_start("practical")


def _regular_c_rows(rows: List[Round]) -> List[Round]:
    """qualExamSchd has no 정기/상시 marker and 상시 기능사 rounds reuse the
    same numbers (qual_2026_T_7910: 상시 1~41회). A regular 기능사 round is
    listed as a pair (regular + extra registration window) sharing the same
    exam dates (qual_2026_T_7780); 상시 rounds are single rows. Keep the one
    cluster of two or more rows with identical exam dates and both phases;
    anything else is ambiguous and dropped."""
    clusters: Dict[Tuple[Optional[str], Optional[str]], List[Round]] = {}
    for round_ in rows:
        if reg_start(round_, "written") and reg_start(round_, "practical"):
            clusters.setdefault(_exam_signature(round_), []).append(round_)
    pairs = [members for members in clusters.values() if len(members) >= 2]
    return pairs[0] if len(pairs) == 1 else []


def _select_rows(
    group: str, rounds: List[Round], log: Optional[Callable[[str], None]]
) -> Dict[RoundKey, Round]:
    if group != GROUP_C:
        return pick_regular_rows(r for r in rounds if has_any_date(r))
    by_key: Dict[RoundKey, List[Round]] = {}
    for round_ in rounds:
        by_key.setdefault((int(round_["year"]), int(round_["seq"])), []).append(round_)
    kept = {key: _regular_c_rows(rows) for key, rows in by_key.items()}
    dropped = sorted(key for key, rows in kept.items() if not rows)
    if dropped and log is not None:
        log(f"qualExamSchd: {len(dropped)} 기능사 round(s) without a regular pair dropped")
    return pick_regular_rows(r for rows in kept.values() for r in rows)


def parse_qual_schedule(
    rows: Iterable[Mapping[str, str]], log: Optional[Callable[[str], None]] = None
) -> GroupRounds:
    """qualExamSchd rows (qualgbCd=T, no jmCd) grouped by grade group."""
    buckets: Dict[str, List[Round]] = {}
    for row in rows:
        description = clean_text(row.get("description")) or ""
        title_year, title_seq, _ = parse_title(description)
        year = _to_int(row.get("implyy")) or title_year
        seq = _to_int(row.get("implseq"))
        seq = title_seq if seq is None else seq
        group = group_from_text(description.split("(")[0])
        if group is None or year is None or not seq:
            continue
        label = grade_label(group, year, seq)
        buckets.setdefault(group, []).append(round_from_row(row, year, seq, label, BASIS_GRADE))
    return {group: _select_rows(group, rounds, log) for group, rounds in buckets.items()}


def combine_grade_rounds(lists: GroupRounds, qual: GroupRounds) -> GroupRounds:
    """Grade lists win; qualExamSchd fills (year, seq) keys they lack."""
    combined: GroupRounds = {}
    for group in sorted(set(lists) | set(qual)):
        merged = dict(qual.get(group, {}))
        merged.update(lists.get(group, {}))
        combined[group] = merged
    return combined
