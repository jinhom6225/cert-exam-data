"""Plan D23 + A5: with a fake key, no failure path prints the key, an
encoded form of it or `serviceKey=`."""

import io
import socket
import unittest
import urllib.error
import urllib.parse
from unittest import mock

from tools.certexam import budget, mask_key
from tools.certexam.errors import CallFailed
from tools.certexam.qnet_client import UrllibTransport
from tools.certexam.tests.support import (
    FAKE_KEY,
    FakeClock,
    ScriptedTransport,
    SUNDAY_RUN,
    fixture_bytes,
    run_collect,
    temp_dir,
)
from tools.certexam.tests.test_client import FakeHttpHandler

LEAKS = (FAKE_KEY, urllib.parse.quote(FAKE_KEY, safe=""), urllib.parse.quote_plus(FAKE_KEY), "serviceKey=")
URL_WITH_KEY = "http://openapi.q-net.or.kr/x?serviceKey=" + urllib.parse.quote_plus(FAKE_KEY)


class KeyLeakTest(unittest.TestCase):
    def assert_no_leak(self, text):
        for leak in LEAKS:
            self.assertNotIn(leak, text)

    def run_http(self, handler, args=()):
        with temp_dir() as tmp:
            return run_collect(
                list(args) + ["--out", str(tmp / "x.json")],
                transport=UrllibTransport([handler]),
                clock=FakeClock(SUNDAY_RUN),
            )

    def test_transport_failures_through_the_whole_run(self):
        handlers = [
            FakeHttpHandler(status=302, location="http://evil.example/?k=" + FAKE_KEY),
            FakeHttpHandler(status=500),
            FakeHttpHandler(status=429),
            FakeHttpHandler(error=urllib.error.URLError("no route to " + URL_WITH_KEY)),
            FakeHttpHandler(error=socket.timeout("timed out " + URL_WITH_KEY)),
            FakeHttpHandler(error=TimeoutError(URL_WITH_KEY)),
            FakeHttpHandler(error=ConnectionResetError(URL_WITH_KEY)),
            FakeHttpHandler(error=ValueError(URL_WITH_KEY)),
            FakeHttpHandler(body=fixture_bytes("error_resultCode99_connection.xml")),
            FakeHttpHandler(body=fixture_bytes("error_https_gateway_no_service.xml")),
            FakeHttpHandler(body=b"<!DOCTYPE x [<!ENTITY k '" + FAKE_KEY.encode() + b"'>]><x>&k;</x>"),
            FakeHttpHandler(body=b"x" * (1024 * 1024 + 10)),
            FakeHttpHandler(body=b"<html>" + URL_WITH_KEY.encode() + b"</html>"),
            FakeHttpHandler(body=b'{"header":{"resultCode":"' + FAKE_KEY.encode() + b'"}}'),
        ]
        for handler in handlers:
            result = self.run_http(handler)
            self.assertEqual(result.code, 1, result.text)
            self.assertTrue(result.err.startswith("collect failed"), result.err)
            self.assert_no_leak(result.text)
            self.assertTrue(handler.seen, "request should have reached the fake HTTP layer")

    def test_unexpected_exception_prints_only_its_type(self):
        def script(request):
            return RuntimeError("boom " + URL_WITH_KEY)

        with temp_dir() as tmp:
            result = run_collect(["--out", str(tmp / "x.json")], transport=ScriptedTransport(script))
        self.assertEqual(result.code, 1)
        self.assertEqual(result.err.strip(), "collect failed (RuntimeError)")
        self.assert_no_leak(result.text)

    def test_per_item_failures_and_validation_failures(self):
        def script(request):
            if request.param("jmCd"):
                return CallFailed(request.operation, "request failed (timeout)")
            return None

        with temp_dir() as tmp:
            result = run_collect(["--out", str(tmp / "x.json")], transport=ScriptedTransport(script))
            self.assertEqual(result.code, 1)
            self.assert_no_leak(result.text)
            (tmp / "x.json").write_text("{", encoding="utf-8")
            result = run_collect(["--out", str(tmp / "x.json")])
            self.assertEqual(result.code, 2)
            self.assert_no_leak(result.text)

    def test_budget_and_success_paths(self):
        with temp_dir() as tmp, mock.patch.dict(budget.DAILY_CALL_LIMITS, {"qual": 0}):
            result = run_collect(["--out", str(tmp / "x.json")])
            self.assertEqual(result.code, 1)
            self.assert_no_leak(result.text)
        with temp_dir() as tmp:
            result = run_collect(["--out", str(tmp / "x.json")])
            self.assertEqual(result.code, 0)
            self.assert_no_leak(result.text)
            self.assert_no_leak((tmp / "x.json").read_text(encoding="utf-8"))

    def test_success_through_urllib_transport(self):
        # Every operation gets the getEList body; the run still completes.
        handler = FakeHttpHandler(body=fixture_bytes("ntq_getEList.xml"))
        result = self.run_http(handler)
        self.assertEqual(result.code, 0, result.text)
        self.assertEqual(sorted({seen[0] for seen in handler.seen}), ["apis.data.go.kr", "openapi.q-net.or.kr"])
        self.assertTrue(all(not seen[1] for seen in handler.seen))
        self.assert_no_leak(result.text)


class MaskKeyTest(unittest.TestCase):
    def test_outside_actions_prints_nothing_secret(self):
        out = io.StringIO()
        self.assertEqual(mask_key.main({"DATA_GO_KR_SERVICE_KEY": FAKE_KEY}, out), 1)
        for leak in LEAKS:
            self.assertNotIn(leak, out.getvalue())

    def test_registers_raw_and_encoded_forms(self):
        out = io.StringIO()
        env = {"GITHUB_ACTIONS": "true", "DATA_GO_KR_SERVICE_KEY": FAKE_KEY}
        self.assertEqual(mask_key.main(env, out), 0)
        lines = set(out.getvalue().splitlines())
        for form in LEAKS[:3]:
            self.assertIn("::add-mask::" + mask_key.escape_command_value(form), lines)
        self.assertIn("::add-mask::FAKE%252BKEY%252Fabc%253D%253DSECRET123", lines)

    def test_escape_command_value(self):
        self.assertEqual(mask_key.escape_command_value("a%2Bb\r\n"), "a%252Bb%0D%0A")

    def test_missing_key(self):
        out = io.StringIO()
        self.assertEqual(mask_key.main({"GITHUB_ACTIONS": "true"}, out), 1)


if __name__ == "__main__":
    unittest.main()
