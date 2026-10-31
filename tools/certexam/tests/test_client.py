import email.message
import io
import os
import unittest
import urllib.error
import urllib.request
import urllib.response
from unittest import mock

from tools.certexam.budget import CallBudget
from tools.certexam.errors import BudgetExceeded, CallFailed
from tools.certexam.payload import decode
from tools.certexam.qnet_client import ApiRequest, Client, NoRedirect, RawResponse, UrllibTransport, check_base_url
from tools.certexam.tests.support import FAKE_KEY, fixture_bytes

OK_XML = fixture_bytes("ntq_getEList.xml")
REQ = ApiRequest("ntq", "getEList")


class SequenceTransport:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def endpoint_configured(self, request):
        return True

    def send(self, request, key, timeout):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def client(transport, limit=900):
    sleeps = []
    return Client(transport, FAKE_KEY, CallBudget(limits={"ntq": limit}), sleeps.append), sleeps


class DecodeTest(unittest.TestCase):
    def test_result_code_99_with_http_200_is_failure(self):
        with self.assertRaises(CallFailed) as caught:
            decode("getJMList", fixture_bytes("error_resultCode99_connection.xml"))
        self.assertEqual(caught.exception.result_code, "99")
        self.assertTrue(caught.exception.retryable)

    def test_gateway_error_body(self):
        with self.assertRaises(CallFailed) as caught:
            decode("getJMList", fixture_bytes("error_https_gateway_no_service.xml"))
        self.assertEqual(caught.exception.result_code, "12")

    def test_doctype_and_entity_are_rejected(self):
        for body in (
            b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "b">]><r>&a;</r>',
            b'<?xml version="1.0"?><!entity x SYSTEM "file:///etc/passwd"><r/>',
            b"<!doctype html><html></html>",
        ):
            with self.assertRaises(CallFailed) as caught:
                decode("getEList", body)
            self.assertFalse(caught.exception.retryable)

    def test_utf16_doctype_is_rejected_without_parsing(self):
        body = '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE r [<!ENTITY a "b">]><r>&a;</r>'.encode("utf-16")
        with self.assertRaises(CallFailed) as caught:
            decode("getEList", body)
        self.assertFalse(caught.exception.retryable)

    def test_non_utf8_and_nul_are_rejected(self):
        for body in (
            "<response><header><resultCode>00</resultCode></header>\uc815</response>".encode("euc-kr"),
            b"<response><header><resultCode>00</resultCode></header>\x00</response>",
            b"<!\x00DOCTYPE x><x/>",
        ):
            with self.assertRaises(CallFailed, msg=body) as caught:
                decode("getEList", body)
            self.assertFalse(caught.exception.retryable)

    def test_utf8_bom_is_accepted(self):
        self.assertEqual(len(decode("getEList", b"\xef\xbb\xbf" + OK_XML).rows), 7)

    def test_malformed_bodies(self):
        for body in (b"<response><header>", b"{not json", b"[1, 2]", b"<html>gateway</html>"):
            with self.assertRaises(CallFailed):
                decode("op", body)

    def test_json_items_object_form(self):
        body = b'{"header":{"resultCode":"00"},"body":{"items":{"item":{"implYy":"2027"}},"totalCount":1}}'
        payload = decode("q", body)
        self.assertEqual(payload.rows, ({"implyy": "2027"},))
        self.assertEqual(payload.total_count, 1)

    def test_result_code_text_is_sanitised(self):
        body = b"<response><header><resultCode>bad code; serviceKey=x</resultCode></header></response>"
        with self.assertRaises(CallFailed) as caught:
            decode("op", body)
        self.assertEqual(caught.exception.result_code, "?")


class ClientRetryTest(unittest.TestCase):
    def test_two_retries_with_exponential_backoff_then_success(self):
        error99 = RawResponse(200, fixture_bytes("error_resultCode99_connection.xml"))
        transport = SequenceTransport(error99, CallFailed("getEList", "request failed (timeout)"), RawResponse(200, OK_XML))
        api, sleeps = client(transport)
        self.assertEqual(len(api.call(REQ).rows), 7)
        self.assertEqual(sleeps, [2.0, 4.0])
        self.assertEqual(api.budget.used["ntq"], 3)

    def test_gives_up_after_three_attempts(self):
        transport = SequenceTransport(*[RawResponse(500, b"")] * 3)
        api, _ = client(transport)
        with self.assertRaises(CallFailed) as caught:
            api.call(REQ)
        self.assertEqual((caught.exception.status, transport.calls), (500, 3))

    def test_safety_failures_are_not_retried(self):
        for response in (
            RawResponse(200, b"x" * (1024 * 1024 + 1)),
            RawResponse(200, b"<!DOCTYPE x><x/>"),
            RawResponse(302, b""),
        ):
            transport = SequenceTransport(response, RawResponse(200, OK_XML))
            api, _ = client(transport)
            with self.assertRaises(CallFailed):
                api.call(REQ)
            self.assertEqual(transport.calls, 1)

    def test_budget_counts_retries_and_stops_before_limit(self):
        transport = SequenceTransport(RawResponse(500, b""), RawResponse(500, b""), RawResponse(200, OK_XML))
        api, _ = client(transport, limit=2)
        with self.assertRaises(BudgetExceeded):
            api.call(REQ)
        self.assertEqual(transport.calls, 2)


def _response(req, status, body=b"", location=None):
    headers = email.message.Message()
    if location:
        headers["Location"] = location
    response = urllib.response.addinfourl(io.BytesIO(body), headers, req.full_url, status)
    response.msg = "OK" if status == 200 else "Moved"
    return response


class FakeHttpHandler(urllib.request.HTTPHandler, urllib.request.HTTPSHandler):
    """Answers http and https without a socket; records what reached the
    HTTP layer (replaces both default handlers in build_opener)."""

    def __init__(self, status=200, body=OK_XML, location=None, error=None):
        super().__init__()
        self.status, self.body, self.location, self.error = status, body, location, error
        self.seen = []

    def http_open(self, req):
        self.seen.append((req.host, req.has_proxy(), req.full_url))
        if self.error:
            raise self.error
        return _response(req, self.status, self.body, self.location)

    https_open = http_open


class UrllibTransportTest(unittest.TestCase):
    def test_success_adds_key_only_inside_the_request(self):
        handler = FakeHttpHandler()
        response = UrllibTransport([handler]).send(ApiRequest("ntq", "getJMList", (("jmCd", "1320"),)), FAKE_KEY, 30)
        self.assertEqual(response.status, 200)
        host, proxied, url = handler.seen[0]
        self.assertEqual((host, proxied), ("openapi.q-net.or.kr", False))
        self.assertTrue(url.startswith("http://openapi.q-net.or.kr/api/service/rest/InquiryTestInformationNTQSVC/getJMList?jmCd=1320&serviceKey="))

    def test_redirect_is_refused_and_not_followed(self):
        handler = FakeHttpHandler(status=302, location="http://evil.example/collect")
        with self.assertRaises(CallFailed) as caught:
            UrllibTransport([handler]).send(REQ, FAKE_KEY, 30)
        self.assertEqual((caught.exception.status, caught.exception.retryable), (302, False))
        self.assertEqual(len(handler.seen), 1)
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "http://x"))

    def test_proxy_environment_is_ignored(self):
        proxies = {"HTTP_PROXY": "http://proxy.invalid:3128", "http_proxy": "http://proxy.invalid:3128",
                   "HTTPS_PROXY": "http://proxy.invalid:3128", "ALL_PROXY": "http://proxy.invalid:3128"}
        with mock.patch.dict(os.environ, proxies):
            handler = FakeHttpHandler()
            UrllibTransport([handler]).send(REQ, FAKE_KEY, 30)
        host, proxied, _ = handler.seen[0]
        self.assertEqual((host, proxied), ("openapi.q-net.or.kr", False))

    def test_http_and_network_errors_become_safe_failures(self):
        url = "http://openapi.q-net.or.kr/x?serviceKey=" + FAKE_KEY
        errors = [
            urllib.error.URLError(f"cannot reach {url}"),
            TimeoutError(url),
            ConnectionResetError(url),
        ]
        for error in errors:
            with self.assertRaises(CallFailed) as caught:
                UrllibTransport([FakeHttpHandler(error=error)]).send(REQ, FAKE_KEY, 30)
            self.assertNotIn(FAKE_KEY, str(caught.exception))
            self.assertIsNone(caught.exception.__cause__)
        with self.assertRaises(CallFailed) as caught:
            UrllibTransport([FakeHttpHandler(status=503)]).send(REQ, FAKE_KEY, 30)
        self.assertEqual(caught.exception.status, 503)

    def test_always_list_endpoint(self):
        handler = FakeHttpHandler(body=fixture_bytes("alw_15088943_p1.xml"))
        request = ApiRequest("alw", "getList", (("numOfRows", "100"), ("pageNo", "1")))
        transport = UrllibTransport([handler])
        self.assertTrue(transport.endpoint_configured(request))
        transport.send(request, FAKE_KEY, 30)
        self.assertTrue(handler.seen[0][2].startswith(
            "http://openapi.q-net.or.kr/api/service/rest/InquiryAlwExamSVC/getList?numOfRows=100&pageNo=1&serviceKey="
        ))

    def test_unconfigured_endpoint(self):
        transport = UrllibTransport([FakeHttpHandler()])
        request = ApiRequest("alw", "unknownOperation")
        self.assertFalse(transport.endpoint_configured(request))
        with self.assertRaises(CallFailed):
            transport.send(request, FAKE_KEY, 30)


class BaseUrlCheckTest(unittest.TestCase):
    def test_allowed(self):
        check_base_url("http://openapi.q-net.or.kr/api/service/rest/X/getList")
        check_base_url("https://apis.data.go.kr/B490007/qualExamSchd/getQualExamSchdList")

    def test_refused(self):
        for url in (
            "https://openapi.q-net.or.kr/a", "http://apis.data.go.kr/a", "http://evil.example/a",
            "http://openapi.q-net.or.kr/a?x=1", "http://user@openapi.q-net.or.kr/a",
            "http://openapi.q-net.or.kr/a b", "http://openapi.q-net.or.kr/a#f", "file:///etc/passwd",
        ):
            with self.assertRaises(CallFailed, msg=url):
                check_base_url(url)


if __name__ == "__main__":
    unittest.main()
