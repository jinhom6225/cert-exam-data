"""Calls for one run. Daily calls are fatal on failure; per-item calls are
collected with a failure list so the caller can apply the 5% rule."""

from dataclasses import dataclass
from typing import Callable, Dict, FrozenSet, Generic, List, Optional, Sequence, Tuple, TypeVar

from .constants import (
    ALW_MAX_PAGES,
    ALW_PAGE_SIZE,
    API_ALW,
    API_JMLIST,
    API_NTQ,
    API_QUAL,
    QUAL_MAX_PAGES,
    QUAL_PAGE_SIZE,
)
from .errors import CallFailed
from .parse_grade import GRADE_OPERATIONS, GroupRounds, parse_grade_list
from .parse_lists import JmEntry, parse_always_names, parse_jm_list
from .payload import Row
from .qnet_client import ApiRequest, Client

T = TypeVar("T")
Log = Callable[[str], None]


@dataclass(frozen=True)
class ItemFetch(Generic[T]):
    results: Dict[str, T]
    failed: Tuple[str, ...]
    skipped: Tuple[str, ...] = ()


def fetch_grade_lists(client: Client) -> GroupRounds:
    return {
        group: parse_grade_list(group, client.call(ApiRequest(API_NTQ, op)).rows)
        for op, group in GRADE_OPERATIONS.items()
    }


def fetch_paged(
    client: Client,
    request: ApiRequest,
    page_size: int,
    max_pages: int,
) -> List[Row]:
    """Fetch every page (`numOfRows`, `pageNo`) until totalCount is reached
    or a page comes back empty."""
    rows: List[Row] = []
    for page in range(1, max_pages + 1):
        params = request.params + (("numOfRows", str(page_size)), ("pageNo", str(page)))
        payload = client.call(ApiRequest(request.api, request.operation, params))
        rows.extend(payload.rows)
        total = payload.total_count
        if not payload.rows or total is None or len(rows) >= total:
            return rows
    raise CallFailed(request.operation, "too many pages", retryable=False)


def fetch_qual_rows(client: Client, years: Sequence[int]) -> List[Row]:
    rows: List[Row] = []
    for year in years:
        params = (("dataFormat", "json"), ("implYy", str(year)), ("qualgbCd", "T"))
        request = ApiRequest(API_QUAL, "getQualExamSchdList", params)
        rows.extend(fetch_paged(client, request, QUAL_PAGE_SIZE, QUAL_MAX_PAGES))
    return rows


def fetch_jm_entries(client: Client) -> List[JmEntry]:
    return parse_jm_list(client.call(ApiRequest(API_JMLIST, "getList")).rows)


def fetch_always_names(client: Client, log: Log) -> Optional[FrozenSet[str]]:
    """Optional hint: failure or a missing endpoint only logs one line."""
    request = ApiRequest(API_ALW, "getList")
    if not client.endpoint_configured(request):
        log("always-held list: endpoint not configured, skipped")
        return None
    try:
        rows = fetch_paged(client, request, ALW_PAGE_SIZE, ALW_MAX_PAGES)
    except CallFailed as error:
        log(f"always-held list skipped: {error.describe()}")
        return None
    return parse_always_names(rows)


def fetch_per_item(
    client: Client,
    operation: str,
    targets: Sequence[str],
    parse: Callable[[Sequence[Row]], T],
    out_of_time: Callable[[], bool],
    log: Log,
) -> ItemFetch[T]:
    """Call `operation` with jmCd for each target. Failures are reported, not
    raised. Targets left when the run deadline passes are `skipped`: they keep
    their previous values and are not failures, so a slow `--full` run still
    saves its progress and the rotation picks up the rest."""
    results: Dict[str, T] = {}
    failed: List[str] = []
    skipped: List[str] = []
    for jm_cd in targets:
        if out_of_time():
            skipped.append(jm_cd)
            continue
        try:
            payload = client.call(ApiRequest(API_NTQ, operation, (("jmCd", jm_cd),)))
        except CallFailed as error:
            log(f"{jm_cd}: {error.describe()}")
            failed.append(jm_cd)
            continue
        results[jm_cd] = parse(payload.rows)
    return ItemFetch(results=results, failed=tuple(failed), skipped=tuple(skipped))
