"""`--offline-fixtures` transport: maps each request to a saved raw response.

No network and no key. A missing page after page 1 is served as an empty
page (the saved qualExamSchd page 1 says totalCount 74 but holds 10 rows).
"""

import re
from pathlib import Path
from typing import FrozenSet, List, Optional

from .constants import API_ALW, API_JMLIST, API_NTQ, API_QUAL
from .errors import CallFailed
from .qnet_client import ApiRequest, RawResponse

_SAFE_PART = re.compile(r"[A-Za-z0-9]{1,16}")
_PER_ITEM = ("getJMList", "getFeeList")

_EMPTY_JSON = b'{"header":{"resultCode":"00","resultMsg":"NORMAL SERVICE"},"body":{"items":[],"totalCount":0}}'
_EMPTY_XML = (
    b'<?xml version="1.0" encoding="UTF-8"?><response><header><resultCode>00</resultCode>'
    b"</header><body><items/><totalCount>0</totalCount></body></response>"
)


def _part(value: Optional[str]) -> str:
    text = value or ""
    if not _SAFE_PART.fullmatch(text):
        raise CallFailed("fixture", "unsafe fixture name", retryable=False)
    return text


class FixtureTransport:
    def __init__(self, directory: Path) -> None:
        self._dir = directory
        self.missing_pages: List[str] = []

    def endpoint_configured(self, request: ApiRequest) -> bool:
        return True

    def available_items(self, operation: str) -> FrozenSet[str]:
        pattern = re.compile(rf"ntq_{operation}_(\d{{4}})\.xml")
        names = (pattern.fullmatch(path.name) for path in self._dir.iterdir())
        return frozenset(match.group(1) for match in names if match)

    def _file_name(self, request: ApiRequest) -> str:
        op = request.operation
        if request.api == API_NTQ and op in _PER_ITEM:
            return f"ntq_{op}_{_part(request.param('jmCd'))}.xml"
        if request.api == API_NTQ:
            return f"ntq_{_part(op)}.xml"
        if request.api == API_JMLIST:
            return "jmlist_15003024_getList.xml"
        page = _part(request.param("pageNo") or "1")
        if request.api == API_QUAL:
            year, kind = _part(request.param("implYy")), _part(request.param("qualgbCd"))
            return f"qual_{year}_{kind}_p{page}.json"
        if request.api == API_ALW:
            return f"alw_15088943_p{page}.xml"
        raise CallFailed(op, "no fixture mapping", retryable=False)

    def send(self, request: ApiRequest, key: str, timeout: float) -> RawResponse:
        name = self._file_name(request)
        path = self._dir / name
        if path.is_file():
            return RawResponse(status=200, body=path.read_bytes())
        if (request.param("pageNo") or "1") != "1":
            self.missing_pages.append(name)
            empty = _EMPTY_JSON if request.api == API_QUAL else _EMPTY_XML
            return RawResponse(status=200, body=empty)
        raise CallFailed(request.operation, "fixture missing", retryable=False)
