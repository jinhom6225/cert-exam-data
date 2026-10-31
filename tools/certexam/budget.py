"""Daily call plan: weekday rotation for per-item operations (plan A2)."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional, Sequence, Tuple

from .constants import DAILY_CALL_LIMITS, KST, ROTATION_DAYS
from .errors import BudgetExceeded


@dataclass(frozen=True)
class RunPlan:
    fetch_jm_list: bool
    jm_targets: Tuple[str, ...]
    fee_targets: Tuple[str, ...]
    kst_year: int


def kst_now(now_utc: datetime) -> datetime:
    if now_utc.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now_utc.astimezone(KST)


def rotation_targets(jm_codes: Sequence[str], weekday: int) -> Tuple[str, ...]:
    """Items whose position in the jmCd-sorted list % 7 equals the KST
    weekday (Monday 0 .. Sunday 6). Seven days cover every item once."""
    ordered = sorted(jm_codes)
    return tuple(code for index, code in enumerate(ordered) if index % ROTATION_DAYS == weekday)


def should_fetch_jm_list(now_utc: datetime, full: bool) -> bool:
    """15003024 item list: Mondays (KST) or a full run."""
    return full or kst_now(now_utc).weekday() == 0


def plan_run(
    jm_codes: Sequence[str],
    now_utc: datetime,
    full: bool,
    restrict_jm: Optional[frozenset] = None,
    restrict_fee: Optional[frozenset] = None,
) -> RunPlan:
    """`full` fetches getJMList for every item. getFeeList stays on the
    rotation even then: 513 x 2 calls would pass the 900-call guard on the
    shared 15003029 quota. `restrict_*` limits targets (offline fixtures)."""
    kst = kst_now(now_utc)
    ordered = tuple(sorted(jm_codes))
    rotated = rotation_targets(ordered, kst.weekday())
    jm_targets = ordered if full else rotated
    fee_targets = rotated
    if restrict_jm is not None:
        jm_targets = tuple(code for code in ordered if code in restrict_jm)
    if restrict_fee is not None:
        fee_targets = tuple(code for code in ordered if code in restrict_fee)
    return RunPlan(
        fetch_jm_list=should_fetch_jm_list(now_utc, full),
        jm_targets=jm_targets,
        fee_targets=fee_targets,
        kst_year=kst.year,
    )


@dataclass
class CallBudget:
    """Counts calls (including retries) per API and refuses the call that
    would pass the daily guard, so the run fails before the quota does."""

    limits: Dict[str, int] = field(default_factory=lambda: dict(DAILY_CALL_LIMITS))
    used: Dict[str, int] = field(default_factory=dict)

    def take(self, api: str) -> None:
        count = self.used.get(api, 0)
        if count >= self.limits.get(api, 0):
            raise BudgetExceeded(f"daily call guard reached for {api}")
        self.used[api] = count + 1
