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


class QualVerdictFetchTest(unittest.TestCase):
    def run_fetch(self, script, targets=("7780", "7910"), out_of_time=lambda: False, limit=900):
        from tools.certexam.fetch import fetch_qual_verdicts
        from tools.certexam.tests.support import ScriptedTransport

        transport = ScriptedTransport(script)
        api = Client(transport, "k", CallBudget(limits={"qual": limit}), lambda seconds: None)
        logs = []
        result = fetch_qual_verdicts(api, targets, 2026, out_of_time, logs.append)
        return result, transport, logs

    def test_request_shape_and_fixture_verdicts(self):
        result, transport, _ = self.run_fetch(lambda r: None)
        self.assertEqual(result.results, {"7780": True, "7910": False})
        first = transport.requests[0]
        self.assertEqual((first.api, first.operation), ("qual", "getQualExamSchdList"))
        self.assertEqual(
            (first.param("jmCd"), first.param("implYy"), first.param("qualgbCd"),
             first.param("numOfRows"), first.param("pageNo"), first.param("dataFormat")),
            ("7780", "2026", "T", "50", "1", "json"),
        )

    def test_reads_every_page(self):
        def script(request):
            if request.param("jmCd") != "7910":
                return None
            seqs = range(1, 51) if request.param("pageNo") == "1" else range(51, 62)
            items = ",".join(
                '{"implYy":"2026","implSeq":%d,"docExamStartDt":"2026%02d%02d"}' % (n, n % 12 + 1, n % 28 + 1)
                for n in seqs
            )
            body = '{"header":{"resultCode":"00"},"body":{"items":[%s],"totalCount":61}}' % items
            return RawResponse(200, body.encode())

        result, transport, _ = self.run_fetch(script, targets=("7910",))
        self.assertEqual([r.param("pageNo") for r in transport.requests], ["1", "2"])
        self.assertIs(result.results["7910"], False)

    def test_gray_zone_is_logged_with_jm_cd_only(self):
        def script(request):
            pair = '{"implYy":"2026","implSeq":1,"docExamStartDt":"20260120","pracExamStartDt":"20260314"}'
            singles = ",".join(
                '{"implYy":"2026","implSeq":%d,"docExamStartDt":"202602%02d"}' % (n, n) for n in range(2, 5)
            )
            body = '{"header":{"resultCode":"00"},"body":{"items":[%s,%s,%s],"totalCount":5}}' % (pair, pair, singles)
            return RawResponse(200, body.encode())

        result, _, logs = self.run_fetch(script, targets=("7780",))
        self.assertIs(result.results["7780"], True)
        self.assertEqual(logs, ["7780: qual verdict gray zone paired=1 unpaired=3"])

    def test_fixture_items_are_not_gray(self):
        _, _, logs = self.run_fetch(lambda r: None)
        self.assertEqual(logs, [])

    def test_failure_is_reported_not_raised(self):
        def script(request):
            return CallFailed("getQualExamSchdList", "request failed (timeout)") if request.param("jmCd") == "7780" else None

        result, _, logs = self.run_fetch(script)
        self.assertEqual(result.failed, ("7780",))
        self.assertEqual(result.results, {"7910": False})
        self.assertIn("7780: getQualExamSchdList request failed (timeout)", logs)

    def test_budget_guard_stops_item_calls_without_raising(self):
        result, transport, logs = self.run_fetch(lambda r: None, targets=("7780", "7910", "7780"), limit=1)
        self.assertEqual(len(transport.requests), 1)
        self.assertEqual(result.failed, ("7910", "7780"))
        self.assertTrue(any("call guard" in line for line in logs))

    def test_deadline_skips_remaining_targets_without_rechecking(self):
        checks = []

        def out_of_time():
            checks.append(1)
            return len(checks) > 1

        result, transport, _ = self.run_fetch(lambda r: None, targets=("7780", "7910", "7780"), out_of_time=out_of_time)
        self.assertEqual(result.skipped, ("7910", "7780"))
        self.assertEqual(len(checks), 2)


class OfflineQualItemTest(unittest.TestCase):
    def send(self, transport, jm_cd, page):
        params = (("dataFormat", "json"), ("implYy", "2026"), ("qualgbCd", "T"), ("jmCd", jm_cd),
                  ("numOfRows", "50"), ("pageNo", page))
        return transport.send(ApiRequest("qual", "getQualExamSchdList", params), "", 30)

    def test_page_one_fixture_and_missing_answers(self):
        from tools.certexam.offline import FixtureTransport

        transport = FixtureTransport(FIXTURES)
        self.assertEqual(self.send(transport, "7780", "1").body, (FIXTURES / "qual_2026_T_7780.json").read_bytes())
        self.assertEqual(self.send(transport, "1234", "1").status, 200)
        self.assertEqual(transport.missing_pages, [])
        self.send(transport, "7780", "2")
        self.assertEqual(transport.missing_pages, ["qual_2026_T_7780_p2.json"])


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
