"""Shared helpers for the collector tests (fixtures only, no network)."""

import io
import json
import shutil
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Sequence, Union

from tools.certexam.collect import Deps, run
from tools.certexam.offline import FixtureTransport
from tools.certexam.payload import Row, decode
from tools.certexam.qnet_client import ApiRequest, RawResponse

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FAKE_KEY = "FAKE+KEY/abc==SECRET123"

# 2026-11-01 03:10 KST (Sunday) = the cron time 18:10 UTC on Saturday.
SUNDAY_RUN = datetime(2026, 10, 31, 18, 10, tzinfo=timezone.utc)
# 2026-11-02 03:10 KST (Monday).
MONDAY_RUN = datetime(2026, 11, 1, 18, 10, tzinfo=timezone.utc)


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def rows(name: str) -> List[Row]:
    return list(decode("test", fixture_bytes(name)).rows)


Outcome = Union[RawResponse, Exception, None]


class ScriptedTransport(FixtureTransport):
    """Fixture transport whose answers can be overridden per request."""

    def __init__(self, script: Optional[Callable[[ApiRequest], Outcome]] = None) -> None:
        super().__init__(FIXTURES)
        self.script = script or (lambda request: None)
        self.requests: List[ApiRequest] = []
        self.keys: List[str] = []

    def send(self, request: ApiRequest, key: str, timeout: float) -> RawResponse:
        self.requests.append(request)
        self.keys.append(key)
        outcome = self.script(request)
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, RawResponse):
            return outcome
        try:
            return super().send(request, key, timeout)
        except Exception:
            # Per-item requests without a fixture: answer with an empty list.
            if request.param("jmCd"):
                return RawResponse(200, _EMPTY_ITEMS)
            raise


_EMPTY_ITEMS = (
    b'<?xml version="1.0" encoding="UTF-8"?><response><header><resultCode>00</resultCode>'
    b"</header><body><items/></body></response>"
)


class FakeClock:
    def __init__(self, now: datetime, tick: float = 0.0) -> None:
        self._now = now
        self.sleeps: List[float] = []
        self.ticks = 0.0
        self.tick = tick

    def now(self) -> datetime:
        return self._now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def monotonic(self) -> float:
        self.ticks += self.tick
        return self.ticks


@contextmanager
def temp_dir() -> Iterator[Path]:
    path = Path(tempfile.mkdtemp(prefix="certexam-test-"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


class RunResult:
    def __init__(self, code: int, out: str, err: str, transport: object) -> None:
        self.code = code
        self.out = out
        self.err = err
        self.transport = transport

    @property
    def text(self) -> str:
        return self.out + self.err


def run_collect(
    args: Sequence[str],
    transport: Optional[object] = None,
    env: Optional[Dict[str, str]] = None,
    clock: Optional[FakeClock] = None,
) -> RunResult:
    transport = transport or ScriptedTransport()
    clock = clock or FakeClock(SUNDAY_RUN)
    out, err = io.StringIO(), io.StringIO()
    deps = Deps(
        env={"DATA_GO_KR_SERVICE_KEY": FAKE_KEY} if env is None else env,
        now=clock.now,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        transport_factory=lambda: transport,
        out=out,
        err=err,
    )
    code = run(list(args), deps)
    return RunResult(code, out.getvalue(), err.getvalue(), transport)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
