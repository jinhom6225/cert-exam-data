import io
import unittest
from contextlib import redirect_stderr

from tools.certexam import build_jm_list
from tools.certexam.budget import CallBudget
from tools.certexam.errors import CallFailed
from tools.certexam.fetch import fetch_always_names, fetch_paged, fetch_qual_rows
from tools.certexam.qnet_client import ApiRequest, Client, RawResponse
from tools.certexam.tests.support import FIXTURES, temp_dir


def page(rows, total):
    items = ",".join('{"implSeq":%d}' % n for n in rows)
    body = '{"header":{"resultCode":"00"},"body":{"items":[%s],"totalCount":%d}}' % (items, total)
    return RawResponse(200, body.encode())


class PagedTransport:
    def __init__(self, pages, configured=True):
        self.pages = pages
        self.requests = []
        self.configured = configured

    def endpoint_configured(self, request):
        return self.configured

    def send(self, request, key, timeout):
        self.requests.append(request)
        return self.pages[int(request.param("pageNo")) - 1]


def client(transport):
    return Client(transport, "k", CallBudget(), lambda seconds: None)


class PagingTest(unittest.TestCase):
    def test_reads_every_page_until_total(self):
        transport = PagedTransport([page(range(100), 150), page(range(50), 150)])
        rows = fetch_paged(client(transport), ApiRequest("qual", "q"), 100, 20)
        self.assertEqual(len(rows), 150)
        self.assertEqual([r.param("pageNo") for r in transport.requests], ["1", "2"])
        self.assertEqual(transport.requests[0].param("numOfRows"), "100")

    def test_stops_on_empty_page(self):
        transport = PagedTransport([page(range(10), 74), page([], 74)])
        self.assertEqual(len(fetch_paged(client(transport), ApiRequest("qual", "q"), 100, 20)), 10)

    def test_too_many_pages_fails(self):
        transport = PagedTransport([page([1], 999)] * 3)
        with self.assertRaises(CallFailed):
            fetch_paged(client(transport), ApiRequest("qual", "q"), 1, 3)

    def test_qual_asks_both_years_with_json_and_t(self):
        transport = PagedTransport([page([], 0)])
        fetch_qual_rows(client(transport), (2026, 2027))
        self.assertEqual([r.param("implYy") for r in transport.requests], ["2026", "2027"])
        self.assertTrue(all(r.param("qualgbCd") == "T" and r.param("dataFormat") == "json" for r in transport.requests))

    def test_qual_page_size_stays_within_the_api_limit(self):
        # qualExamSchd answers resultCode 930 when numOfRows is over 50
        # (first real run, 2026-10-31).
        transport = PagedTransport([page([], 0)])
        fetch_qual_rows(client(transport), (2026,))
        self.assertTrue(all(int(r.param("numOfRows")) <= 50 for r in transport.requests))

    def test_always_list_skipped_when_endpoint_missing(self):
        logs = []
        transport = PagedTransport([], configured=False)
        self.assertIsNone(fetch_always_names(client(transport), logs.append))
        self.assertEqual(transport.requests, [])
        self.assertIn("not configured", logs[0])


class AlwaysListFetchTest(unittest.TestCase):
    def test_daily_call_reads_names_with_paging_params(self):
        from tools.certexam.tests.support import fixture_bytes

        class AlwTransport(PagedTransport):
            def send(self, request, key, timeout):
                self.requests.append(request)
                return RawResponse(200, fixture_bytes("alw_15088943_p1.xml"))

        transport = AlwTransport([])
        names = fetch_always_names(client(transport), [].append)
        self.assertEqual(len(names), 14)
        self.assertEqual(
            [(r.api, r.operation, r.param("numOfRows"), r.param("pageNo")) for r in transport.requests],
            [("alw", "getList", "100", "1")],
        )


class BuildJmListTest(unittest.TestCase):
    def test_main_writes_the_list(self):
        with temp_dir() as tmp:
            out = tmp / "jm_list.json"
            code = build_jm_list.main([str(FIXTURES / "jmlist_15003024_getList.xml"), str(out)])
            self.assertEqual(code, 0)
            self.assertEqual(out.read_bytes(), build_jm_list.build(FIXTURES / "jmlist_15003024_getList.xml"))

    def test_main_errors(self):
        with redirect_stderr(io.StringIO()):
            self.assertEqual(build_jm_list.main([]), 1)
            self.assertEqual(build_jm_list.main([str(FIXTURES / "missing.xml"), "/tmp/x.json"]), 1)
            self.assertEqual(
                build_jm_list.main([str(FIXTURES / "error_resultCode99_connection.xml"), "/tmp/x.json"]), 1
            )


if __name__ == "__main__":
    unittest.main()
