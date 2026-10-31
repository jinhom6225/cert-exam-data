"""HTTP client for the public-data APIs with the service key kept out of logs.

Safety rules (plan D22, D23, A5):
- the base URL is checked against an allow-list before the key is added;
- redirects are refused, proxy environment variables are ignored;
- responses are capped at 1MB and XML with DOCTYPE/ENTITY is rejected;
- errors carry only the operation name, HTTP status and resultCode.
"""

import http.client
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional, Protocol, Sequence, Tuple

from .constants import (
    ALLOWED_ORIGINS,
    BACKOFF_BASE_SECONDS,
    ENDPOINTS,
    KEY_PARAM,
    MAX_RESPONSE_BYTES,
    RETRIES,
    TIMEOUT_SECONDS,
)
from .budget import CallBudget
from .errors import CallFailed
from .payload import Payload, decode


@dataclass(frozen=True)
class ApiRequest:
    api: str
    operation: str
    params: Tuple[Tuple[str, str], ...] = ()

    def param(self, name: str) -> Optional[str]:
        return dict(self.params).get(name)


@dataclass(frozen=True)
class RawResponse:
    status: int
    body: bytes


class Transport(Protocol):
    def endpoint_configured(self, request: ApiRequest) -> bool: ...

    def send(self, request: ApiRequest, key: str, timeout: float) -> RawResponse: ...


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects so the key in the query never reaches another URL."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


def check_base_url(url: str) -> None:
    """Allow only known scheme/host pairs, no query, no credentials."""
    parts = urllib.parse.urlsplit(url)
    bad_chars = any(ch <= " " or ch == "\x7f" for ch in url)
    if (
        bad_chars
        or (parts.scheme, parts.netloc) not in ALLOWED_ORIGINS
        or parts.query
        or parts.fragment
        or "@" in parts.netloc
    ):
        raise CallFailed("endpoint", "base URL refused", retryable=False)


class UrllibTransport:
    """Real network transport (urllib, no proxy, no redirects)."""

    def __init__(self, extra_handlers: Sequence[urllib.request.BaseHandler] = ()) -> None:
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect(), *extra_handlers
        )

    def endpoint_configured(self, request: ApiRequest) -> bool:
        return ENDPOINTS.get((request.api, request.operation)) is not None

    def send(self, request: ApiRequest, key: str, timeout: float) -> RawResponse:
        op = request.operation
        base = ENDPOINTS.get((request.api, op))
        if base is None:
            raise CallFailed(op, "endpoint not configured", retryable=False)
        check_base_url(base)
        query = list(request.params) + [(KEY_PARAM, key)]
        http_request = urllib.request.Request(base + "?" + urllib.parse.urlencode(query))
        try:
            with self._opener.open(http_request, timeout=timeout) as response:
                status = response.status
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            if 300 <= status < 400:
                raise CallFailed(op, "redirect refused", status=status, retryable=False) from None
            raise CallFailed(op, "HTTP error", status=status) from None
        except (urllib.error.URLError, socket.timeout, TimeoutError) as error:
            raise CallFailed(op, f"request failed ({type(error).__name__})") from None
        except (http.client.HTTPException, OSError, ValueError) as error:
            raise CallFailed(op, f"request failed ({type(error).__name__})") from None
        return RawResponse(status=status, body=body)


class Client:
    """Calls one operation with retries and returns decoded rows."""

    def __init__(
        self,
        transport: Transport,
        key: str,
        budget: CallBudget,
        sleep: Callable[[float], None],
        timeout: float = TIMEOUT_SECONDS,
        retries: int = RETRIES,
    ) -> None:
        self._transport = transport
        self._key = key
        self._budget = budget
        self._sleep = sleep
        self._timeout = timeout
        self._retries = retries

    @property
    def budget(self) -> CallBudget:
        return self._budget

    def endpoint_configured(self, request: ApiRequest) -> bool:
        return self._transport.endpoint_configured(request)

    def call(self, request: ApiRequest) -> Payload:
        last: Optional[CallFailed] = None
        for attempt in range(self._retries + 1):
            if attempt:
                self._sleep(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
            self._budget.take(request.api)
            try:
                return self._attempt(request)
            except CallFailed as error:
                if not error.retryable:
                    raise
                last = error
        assert last is not None
        raise last

    def _attempt(self, request: ApiRequest) -> Payload:
        op = request.operation
        response = self._transport.send(request, self._key, self._timeout)
        if len(response.body) > MAX_RESPONSE_BYTES:
            raise CallFailed(op, "response over 1MB", retryable=False)
        if 300 <= response.status < 400:
            raise CallFailed(op, "redirect refused", status=response.status, retryable=False)
        if response.status != 200:
            raise CallFailed(op, "HTTP error", status=response.status)
        return decode(op, response.body)
