"""Entry point: python3 -m tools.certexam.collect [--full] [--offline-fixtures DIR]

Exit codes: 0 = unchanged or written, 1 = call/config failure (nothing
written), 2 = validation failure (nothing written), 64 = bad arguments.
"""

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Mapping, NoReturn, Optional, Sequence, TextIO, Tuple

from .budget import CallBudget, plan_run, should_fetch_jm_list
from .constants import (
    KEY_ENV,
    MAX_ITEM_COUNT_DROP_PERCENT,
    MAX_ITEM_FAILURE_PERCENT,
    RUN_DEADLINE_SECONDS,
    SCHEMA_VERSION,
    SOURCE,
    USAGE_EXIT_CODE,
    WORST_CALL_SECONDS,
)
from .errors import CallFailed, CollectorError, ConfigError, TooManyFailures, ValidationFailed
from .fetch import (
    ItemFetch,
    fetch_always_names,
    fetch_grade_lists,
    fetch_jm_entries,
    fetch_per_item,
    fetch_qual_rows,
)
from .merge import MergeInputs, merge_items, universe_from_previous
from .offline import FixtureTransport
from .parse_fee import parse_fee
from .parse_grade import combine_grade_rounds, parse_qual_schedule
from .parse_item import parse_item_schedule
from .parse_lists import JmEntry
from .qnet_client import Client, Transport, UrllibTransport
from .validate import ValidationReport, restore_previous, validate_document
from .write_output import WriteResult, format_generated_at, load_previous, write_if_changed

DEFAULT_OUT = Path("certexam/exams.json")
DEFAULT_JM_LIST = Path(__file__).resolve().parent / "jm_list.json"


@dataclass(frozen=True)
class Deps:
    env: Mapping[str, str]
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    sleep: Callable[[float], None] = time.sleep
    monotonic: Callable[[], float] = time.monotonic
    transport_factory: Callable[[], Transport] = UrllibTransport
    out: TextIO = field(default_factory=lambda: sys.stdout)
    err: TextIO = field(default_factory=lambda: sys.stderr)


class UsageError(Exception):
    """Bad command-line arguments (exit 64, distinct from validation's 2)."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:  # type: ignore[override]
        # Do not echo the arguments: a key pasted by mistake would be printed.
        raise UsageError()


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = _Parser(prog="collect", description="Build certexam/exams.json")
    parser.add_argument("--full", action="store_true", help="getJMList for every item")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--previous", type=Path, help="previous exams.json (default: --out)")
    parser.add_argument("--jm-list", type=Path, default=DEFAULT_JM_LIST)
    parser.add_argument("--offline-fixtures", type=Path, help="serve saved raw responses")
    parser.add_argument("--now", help="fixed UTC time (offline only), e.g. 2026-10-31T18:10:00Z")
    return parser.parse_args(list(argv))


def _now(args: argparse.Namespace, deps: Deps) -> datetime:
    if args.now is None:
        return deps.now()
    if args.offline_fixtures is None:
        raise ConfigError("--now is only allowed with --offline-fixtures")
    try:
        parsed = datetime.fromisoformat(args.now.replace("Z", "+00:00"))
    except ValueError:
        raise ConfigError("--now is not an ISO time") from None
    if parsed.tzinfo is None:
        raise ConfigError("--now needs a time zone")
    return parsed


def _load_jm_list(path: Path) -> List[JmEntry]:
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ConfigError("jm_list.json unreadable") from None
    if not isinstance(entries, list):
        raise ConfigError("jm_list.json is not a list")
    return entries


def check_failures(operation: str, fetched: ItemFetch, targets: Sequence[str]) -> None:
    """Up to 5% of today's planned targets may fail (they keep previous
    values). Targets skipped at the run deadline also keep previous values
    and are not failures."""
    failed = len(fetched.failed)
    if failed * 100 > len(targets) * MAX_ITEM_FAILURE_PERCENT:
        raise TooManyFailures(f"{operation} failed for {failed} of {len(targets)} items")


def check_item_count(previous: Optional[Dict[str, object]], items: Sequence[object]) -> None:
    if previous is None:
        return
    before = len(previous.get("items") or [])  # type: ignore[arg-type]
    if len(items) * 100 < before * (100 - MAX_ITEM_COUNT_DROP_PERCENT):
        raise ValidationFailed(f"item count fell from {before} to {len(items)}")


@dataclass(frozen=True)
class Collected:
    items: List[Dict[str, object]]
    schedules: ItemFetch
    fees: ItemFetch
    jm_targets: int
    fee_targets: int
    previous_items: Dict[str, Dict[str, object]]


def _open_client(args: argparse.Namespace, deps: Deps) -> Tuple[Client, Optional[FixtureTransport]]:
    if args.offline_fixtures is not None:
        offline = FixtureTransport(args.offline_fixtures)
        return Client(offline, "", CallBudget(), deps.sleep), offline
    key = (deps.env.get(KEY_ENV) or "").strip()
    if not key:
        raise ConfigError(f"{KEY_ENV} is not set")
    return Client(deps.transport_factory(), key, CallBudget(), deps.sleep), None


def _universe(
    args: argparse.Namespace,
    client: Client,
    now: datetime,
    previous: Optional[Dict[str, object]],
    log: Callable[[str], None],
) -> List[JmEntry]:
    """Item list: 15003024 on Mondays or --full; on failure (or other days)
    the previous file's items, else the bundled jm_list.json."""
    if should_fetch_jm_list(now, args.full):
        try:
            return fetch_jm_entries(client)
        except CallFailed as error:
            log(f"item list skipped: {error.describe()}")
    return universe_from_previous(previous) or _load_jm_list(args.jm_list)


def _collect(
    args: argparse.Namespace,
    deps: Deps,
    client: Client,
    offline: Optional[FixtureTransport],
    now: datetime,
    previous: Optional[Dict[str, object]],
    started: float,
) -> Collected:
    def log(message: str) -> None:
        print(message, file=deps.out)

    universe = _universe(args, client, now, previous, log)
    plan = plan_run(
        [str(entry["jmCd"]) for entry in universe],
        now,
        args.full,
        restrict_jm=offline.available_items("getJMList") if offline else None,
        restrict_fee=offline.available_items("getFeeList") if offline else None,
    )
    grade_lists = fetch_grade_lists(client)
    qual = parse_qual_schedule(fetch_qual_rows(client, (plan.kst_year, plan.kst_year + 1)), log)
    always = fetch_always_names(client, log) or frozenset()

    last_call_start = started + RUN_DEADLINE_SECONDS - WORST_CALL_SECONDS

    def out_of_time() -> bool:
        return deps.monotonic() > last_call_start

    schedules = fetch_per_item(
        client, "getJMList", plan.jm_targets, parse_item_schedule, out_of_time, log
    )
    fees = fetch_per_item(client, "getFeeList", plan.fee_targets, parse_fee, out_of_time, log)
    check_failures("getJMList", schedules, plan.jm_targets)
    check_failures("getFeeList", fees, plan.fee_targets)
    previous_items = {
        str(item.get("jmCd")): item
        for item in (previous or {}).get("items", [])  # type: ignore[union-attr]
        if isinstance(item, dict)
    }
    items = merge_items(
        MergeInputs(
            universe=universe,
            grade_rounds=combine_grade_rounds(grade_lists, qual),
            item_schedules=schedules.results,
            fees=fees.results,
            previous_items=previous_items,
            always_names=always,
        )
    )
    return Collected(
        items, schedules, fees, len(plan.jm_targets), len(plan.fee_targets), previous_items
    )


def _summary(
    collected: Collected, report: ValidationReport, client: Client, result: WriteResult
) -> str:
    items = report.document["items"]
    rounds = sum(len(item["rounds"]) for item in items)  # type: ignore[union-attr]
    calls = ",".join(f"{api}:{n}" for api, n in sorted(client.budget.used.items()))
    return (
        f"certexam: items={len(items)} rounds={rounds} "  # type: ignore[arg-type]
        f"getJMList={len(collected.schedules.results)}/{collected.jm_targets} "
        f"getFeeList={len(collected.fees.results)}/{collected.fee_targets} "
        f"skippedAtDeadline={len(collected.schedules.skipped) + len(collected.fees.skipped)} "
        f"droppedItems={len(report.dropped_items)} restoredItems={len(report.restored_items)} "
        f"droppedRounds={report.dropped_rounds} bytes={result.size} "
        f"calls={calls} result={'written' if result.written else 'unchanged'}"
    )


def main(argv: Sequence[str], deps: Deps) -> int:
    started = deps.monotonic()
    args = parse_args(argv)
    client, offline = _open_client(args, deps)
    now = _now(args, deps)
    previous = load_previous(args.previous or args.out)
    collected = _collect(args, deps, client, offline, now, previous, started)
    document = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": format_generated_at(now),
        "source": dict(SOURCE),
        "items": collected.items,
    }
    report = restore_previous(validate_document(document), collected.previous_items)
    check_item_count(previous, report.document["items"])  # type: ignore[arg-type]
    result = write_if_changed(args.out, report.document, previous)
    if offline is not None and offline.missing_pages:
        print(
            f"offline: {len(offline.missing_pages)} later page fixture(s) missing, "
            "treated as last page",
            file=deps.out,
        )
    print(_summary(collected, report, client, result), file=deps.out)
    return 0


def run(argv: Optional[Sequence[str]] = None, deps: Optional[Deps] = None) -> int:
    """Top-level guard: expected failures print a safe one-line reason;
    anything else prints only the exception type (a traceback could carry
    the request URL with the key)."""
    deps = deps or Deps(env=dict(os.environ))
    try:
        return main(sys.argv[1:] if argv is None else argv, deps)
    except UsageError:
        print("collect failed: invalid arguments (see --help)", file=deps.err)
        return USAGE_EXIT_CODE
    except ValidationFailed as error:
        print(f"collect failed (validation): {error}", file=deps.err)
        return 2
    except CollectorError as error:
        print(f"collect failed: {error}", file=deps.err)
        return 1
    except Exception as error:  # noqa: BLE001 - see docstring
        print(f"collect failed ({type(error).__name__})", file=deps.err)
        return 1


if __name__ == "__main__":
    sys.tracebacklimit = 0
    sys.exit(run())
