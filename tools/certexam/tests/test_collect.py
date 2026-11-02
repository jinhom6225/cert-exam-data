import json
import unittest
from datetime import timedelta
from unittest import mock

from tools.certexam import budget, write_output
from tools.certexam.budget import kst_now
from tools.certexam.errors import CallFailed
from tools.certexam.qnet_client import RawResponse
from tools.certexam.tests.support import (
    FIXTURES,
    MONDAY_RUN,
    SUNDAY_RUN,
    FakeClock,
    ScriptedTransport,
    fixture_bytes,
    read_json,
    run_collect,
    temp_dir,
)
from tools.certexam.validate import validate_document

JM_CODES = sorted(e["jmCd"] for e in json.loads((FIXTURES.parent.parent / "jm_list.json").read_text("utf-8")))
OFFLINE = ["--offline-fixtures", str(FIXTURES), "--now", "2026-10-31T18:10:00Z"]


def run_day_for(jm_cd):
    """A cron time whose KST weekday puts `jm_cd` in today's rotation."""
    wanted = JM_CODES.index(jm_cd) % 7
    for days in range(7):
        when = SUNDAY_RUN + timedelta(days=days)
        if kst_now(when).weekday() == wanted:
            return when
    raise AssertionError("unreachable")


def fail_items(operation, codes):
    def script(request):
        if request.operation == operation and request.param("jmCd") in codes:
            return CallFailed(operation, "request failed (timeout)")
        return None

    return script


CRAFTSMEN = sorted(
    e["jmCd"] for e in json.loads((FIXTURES.parent.parent / "jm_list.json").read_text("utf-8")) if e["grade"] == "기능사"
)


def qual_item_requests(transport):
    return [r for r in transport.requests if r.api == "qual" and r.param("jmCd")]


class RequestClock(FakeClock):
    """monotonic() jumps past every deadline once requests matching `match`
    have been sent for `count` distinct items (retries count once)."""

    def __init__(self, now, transport, match, count, late=10_000):
        super().__init__(now)
        self.transport, self.match, self.count, self.late = transport, match, count, late

    def monotonic(self):
        sent = {r.param("jmCd") for r in self.transport.requests if self.match(r)}
        return self.late if len(sent) >= self.count else 0


class ScriptedClock(FakeClock):
    """monotonic() returns the given values in order, then repeats the last."""

    def __init__(self, now, values):
        super().__init__(now)
        self.values = list(values)

    def monotonic(self):
        return self.values.pop(0) if len(self.values) > 1 else self.values[0]


def thirteen_rounds():
    items = "".join(
        f"<item><implplannm>2026년 정기 기사 {n}회</implplannm><docregstartdt>202601{n:02d}</docregstartdt></item>"
        for n in range(1, 14)
    )
    body = f'<?xml version="1.0" encoding="UTF-8"?><response><header><resultCode>00</resultCode></header><body><items>{items}</items></body></response>'
    return RawResponse(200, body.encode("utf-8"))


class OfflineDryRunTest(unittest.TestCase):
    def test_dry_run_builds_a_valid_document(self):
        with temp_dir() as tmp:
            out = tmp / "exams.json"
            result = run_collect(OFFLINE + ["--out", str(out)])
            self.assertEqual(result.code, 0, result.text)
            doc = read_json(out)
            self.assertEqual(len(validate_document(doc).document["items"]), 513)
            items = {item["jmCd"]: item for item in doc["items"]}
            self.assertEqual([r["basis"] for r in items["1320"]["rounds"]], ["item"] * 3)
            self.assertEqual(items["1320"]["fee"], {"written": 19400, "practical": 22600})
            self.assertEqual([r["seq"] for r in items["0752"]["rounds"]], [138])
            self.assertIs(items["7910"]["regularOnly"], False)
            self.assertIs(items["7780"]["regularOnly"], True)
            self.assertIs(items["0011"]["regularOnly"], True)
            craftsman_unknown = [i for i in doc["items"] if i["regularOnly"] is None]
            self.assertTrue(all(i["grade"] == "기능사" for i in craftsman_unknown))
            self.assertIn("result=written", result.out)
            self.assertIn("jmFailed=0 feeFailed=0 ", result.out)
            self.assertIn(f"bytes={len(out.read_bytes())} ", result.out)

    def test_second_run_is_unchanged_and_keeps_generated_at(self):
        with temp_dir() as tmp:
            out = tmp / "exams.json"
            run_collect(OFFLINE + ["--out", str(out)])
            before = out.read_bytes()
            later = ["--offline-fixtures", str(FIXTURES), "--now", "2026-11-01T18:10:00Z", "--out", str(out)]
            result = run_collect(later)
            self.assertEqual(result.code, 0, result.text)
            self.assertIn("result=unchanged", result.out)
            self.assertEqual(out.read_bytes(), before)

    def test_full_offline_run_reads_the_item_list_fixture(self):
        with temp_dir() as tmp:
            result = run_collect(OFFLINE + ["--full", "--out", str(tmp / "a.json")])
            self.assertEqual(result.code, 0, result.text)
            self.assertIn("jmlist:1", result.out)

    def test_bad_arguments_exit_64_without_echoing_them(self):
        result = run_collect(["--bogus", "FAKE+KEY/abc==SECRET123"])
        self.assertEqual(result.code, 64)
        self.assertNotIn("FAKE+KEY", result.text)
        self.assertIn("invalid arguments", result.err)

    def test_now_needs_offline(self):
        result = run_collect(["--now", "2026-10-31T18:10:00Z", "--out", "/nonexistent/x.json"])
        self.assertEqual(result.code, 1)


class LiveModeRulesTest(unittest.TestCase):
    """Live code path with a scripted transport (fixtures, no network)."""

    def previous_file(self, tmp):
        path = tmp / "exams.json"
        self.assertEqual(run_collect(OFFLINE + ["--out", str(path)]).code, 0)
        return path

    def test_rotation_only_calls_todays_items(self):
        with temp_dir() as tmp:
            result = run_collect(["--out", str(tmp / "exams.json")])
            self.assertEqual(result.code, 0, result.text)
            jm_calls = [r.param("jmCd") for r in result.transport.requests if r.operation == "getJMList"]
            self.assertEqual(len(jm_calls), 73)
            self.assertEqual(jm_calls, [c for i, c in enumerate(JM_CODES) if i % 7 == 6])
            self.assertNotIn("getList", [r.operation for r in result.transport.requests if r.api == "jmlist"])
            self.assertTrue(all(key == "FAKE+KEY/abc==SECRET123" for key in result.transport.keys))

    def test_monday_fetches_the_item_list(self):
        with temp_dir() as tmp:
            result = run_collect(["--out", str(tmp / "exams.json")], clock=FakeClock(MONDAY_RUN))
            self.assertEqual(result.code, 0, result.text)
            self.assertIn("jmlist:1", result.out)

    def todays_targets(self, jm_cd="1320"):
        return [c for i, c in enumerate(JM_CODES) if i % 7 == JM_CODES.index(jm_cd) % 7]

    def test_13_of_73_get_jm_list_failures_exit_0_and_keep_previous_values(self):
        # First scheduled run (run 41243712032): 13 of 73 failed -> was exit 1.
        with temp_dir() as tmp:
            out = self.previous_file(tmp)
            todays = self.todays_targets()
            failing = {"1320"} | set(c for c in todays if c != "1320")
            failing = set(sorted(failing, key=lambda c: c != "1320")[:13])
            self.assertEqual((len(todays), len(failing)), (73, 13))
            transport = ScriptedTransport(fail_items("getJMList", failing))
            result = run_collect(["--out", str(out)], transport=transport, clock=FakeClock(run_day_for("1320")))
            self.assertEqual(result.code, 0, result.text)
            item = {i["jmCd"]: i for i in read_json(out)["items"]}["1320"]
            self.assertEqual([r["basis"] for r in item["rounds"]], ["item"] * 3)
            self.assertIn("getJMList=60/73 ", result.out)
            self.assertIn("jmFailed=13 feeFailed=0 ", result.out)
            self.assertIn("1320: getJMList request failed (timeout)", result.out)
            # 13/73 = 17.8%: under the 20% warning line.
            self.assertNotIn("warning: getJMList", result.out)
            self.assertEqual(result.err, "")

    def test_over_twenty_percent_failures_warn_and_exit_0(self):
        with temp_dir() as tmp:
            out = self.previous_file(tmp)
            failing = set(self.todays_targets()[:15])  # 15/73 = 20.5%
            transport = ScriptedTransport(fail_items("getJMList", failing))
            result = run_collect(["--out", str(out)], transport=transport, clock=FakeClock(run_day_for("1320")))
        self.assertEqual(result.code, 0, result.text)
        self.assertIn("warning: getJMList failed for 15 of 73 items", result.out)
        self.assertEqual(result.out.count("warning:"), 1)

    def test_twenty_percent_exactly_does_not_warn(self):
        # 20% of 73 targets is 14.6, so 14 failures stay quiet.
        with temp_dir() as tmp:
            out = self.previous_file(tmp)
            failing = set(self.todays_targets()[:14])
            transport = ScriptedTransport(fail_items("getFeeList", failing))
            result = run_collect(["--out", str(out)], transport=transport, clock=FakeClock(run_day_for("1320")))
        self.assertEqual(result.code, 0, result.text)
        self.assertIn("feeFailed=14 ", result.out)
        self.assertNotIn("warning:", result.out)

    def test_all_per_item_calls_failing_exit_0_and_leave_the_file_unchanged(self):
        with temp_dir() as tmp:
            out = self.previous_file(tmp)
            before = out.read_bytes()
            def script(request):
                if request.operation in ("getJMList", "getFeeList"):
                    return CallFailed(request.operation, "result not OK", result_code="99")
                return None

            later = FakeClock(SUNDAY_RUN + timedelta(days=7))
            result = run_collect(["--out", str(out)], transport=ScriptedTransport(script), clock=later)
            self.assertEqual(result.code, 0, result.text)
            self.assertIn("warning: getJMList failed for 73 of 73 items", result.out)
            self.assertIn("warning: getFeeList failed for 73 of 73 items", result.out)
            self.assertIn("jmFailed=73 feeFailed=73 ", result.out)
            self.assertIn("result=unchanged", result.out)
            self.assertEqual(out.read_bytes(), before)

    def test_per_item_call_guard_is_not_fatal(self):
        with temp_dir() as tmp, mock.patch.dict(budget.DAILY_CALL_LIMITS, {"ntq": 20}):
            result = run_collect(["--out", str(tmp / "x.json")])
        self.assertEqual(result.code, 0, result.text)
        self.assertIn("getJMList stopped: daily call guard reached for ntq", result.out)
        self.assertIn("getFeeList stopped: daily call guard reached for ntq", result.out)
        self.assertIn("calls=alw:1,ntq:20,", result.out)

    def test_daily_grade_list_failure_exits_1(self):
        def script(request):
            if request.operation == "getCList":
                return RawResponse(200, fixture_bytes("error_resultCode99_connection.xml"))
            return None

        with temp_dir() as tmp:
            clock = FakeClock(SUNDAY_RUN)
            result = run_collect(["--out", str(tmp / "x.json")], transport=ScriptedTransport(script), clock=clock)
            self.assertEqual(result.code, 1)
            self.assertIn("getCList result not OK resultCode=99", result.err)
            self.assertEqual(clock.sleeps, [2.0, 4.0])
            self.assertFalse((tmp / "x.json").exists())

    def test_core_call_guard_exits_1(self):
        # The grade lists need 4 ntq calls; the guard is reached inside them.
        with temp_dir() as tmp, mock.patch.dict(budget.DAILY_CALL_LIMITS, {"ntq": 2}):
            result = run_collect(["--out", str(tmp / "x.json")])
            self.assertEqual(result.code, 1)
            self.assertIn("daily call guard reached for ntq", result.err)

    def test_run_deadline_skips_the_rest_and_keeps_previous_values(self):
        with temp_dir() as tmp:
            out = self.previous_file(tmp)
            when = run_day_for("1320")
            todays = [c for i, c in enumerate(JM_CODES) if i % 7 == JM_CODES.index("1320") % 7]
            self.assertGreater(todays.index("1320"), 12)
            result = run_collect(["--out", str(out)], clock=FakeClock(when, tick=120.0))
            self.assertEqual(result.code, 0, result.text)
            self.assertIn("skippedAtDeadline=", result.out)
            self.assertNotIn("skippedAtDeadline=0 ", result.out)
            calls = [r for r in result.transport.requests if r.param("jmCd")]
            self.assertLess(len(calls), 20)
            item = {i["jmCd"]: i for i in read_json(out)["items"]}["1320"]
            self.assertEqual([r["basis"] for r in item["rounds"]], ["item"] * 3)

    def test_failures_before_the_deadline_are_counted_skips_are_not(self):
        fail_all = ScriptedTransport(fail_items("getJMList", set(JM_CODES)))
        with temp_dir() as tmp:
            result = run_collect(["--out", str(tmp / "x.json")], transport=fail_all, clock=FakeClock(SUNDAY_RUN, tick=120.0))
        self.assertEqual(result.code, 0, result.text)
        self.assertRegex(result.out, r"jmFailed=[1-9]\d* ")
        self.assertNotIn("jmFailed=73 ", result.out)
        self.assertNotIn("warning: getJMList", result.out)

    def test_warning_ratio_uses_planned_targets(self):
        # 5 attempts before the deadline, 2 of them fail: 2/73 planned, no warning.
        attempted = [c for i, c in enumerate(JM_CODES) if i % 7 == 6][:2]
        transport = ScriptedTransport(fail_items("getJMList", set(attempted)))
        clock = RequestClock(SUNDAY_RUN, transport, lambda r: r.operation == "getJMList", 5)
        with temp_dir() as tmp:
            result = run_collect(["--out", str(tmp / "x.json")], transport=transport, clock=clock)
        self.assertEqual(result.code, 0, result.text)
        self.assertIn("getJMList=3/73", result.out)
        self.assertIn("jmFailed=2 ", result.out)
        self.assertNotIn("warning: getJMList", result.out)

    def test_deadline_is_measured_from_run_start_minus_worst_call(self):
        # 1410 s after start is past 25 min minus one worst-case call (96 s).
        clock = ScriptedClock(SUNDAY_RUN, [0, 1410])
        with temp_dir() as tmp:
            result = run_collect(["--out", str(tmp / "x.json")], clock=clock)
        self.assertEqual(result.code, 0, result.text)
        self.assertEqual([r for r in result.transport.requests if r.param("jmCd")], [])
        self.assertIn("skippedAtDeadline=146 ", result.out)
        self.assertIn("qualItem=0/163 qualSkipped=163 ", result.out)

    def test_item_dropped_by_validation_keeps_previous_record(self):
        def script(request):
            if request.operation == "getJMList" and request.param("jmCd") == "1320":
                return thirteen_rounds()
            return None

        with temp_dir() as tmp:
            out = self.previous_file(tmp)
            result = run_collect(["--out", str(out)], transport=ScriptedTransport(script), clock=FakeClock(run_day_for("1320")))
            self.assertEqual(result.code, 0, result.text)
            item = {i["jmCd"]: i for i in read_json(out)["items"]}["1320"]
            self.assertEqual([r["seq"] for r in item["rounds"]], [1, 2, 3])
            self.assertIn("restoredItems=1 ", result.out)

    def test_monday_item_list_failure_falls_back(self):
        def script(request):
            return CallFailed("getList", "request failed (timeout)") if request.api == "jmlist" else None

        with temp_dir() as tmp:
            result = run_collect(["--out", str(tmp / "x.json")], transport=ScriptedTransport(script), clock=FakeClock(MONDAY_RUN))
            self.assertEqual(result.code, 0, result.text)
            self.assertIn("item list skipped", result.out)
            self.assertEqual(len(read_json(tmp / "x.json")["items"]), 513)

    def test_item_count_drop_over_ten_percent_exits_2(self):
        with temp_dir() as tmp:
            out = self.previous_file(tmp)
            doc = read_json(out)
            extra = [dict(doc["items"][0], jmCd=f"99{n:02d}") for n in range(60)]
            out.write_text(json.dumps({**doc, "items": doc["items"] + extra}), encoding="utf-8")
            before = out.read_bytes()
            result = run_collect(["--out", str(out)], clock=FakeClock(MONDAY_RUN))
            self.assertEqual(result.code, 2)
            self.assertIn("item count fell from 573 to 513", result.err)
            self.assertEqual(out.read_bytes(), before)

    def test_output_over_cap_exits_2(self):
        with temp_dir() as tmp, mock.patch.object(write_output, "MAX_OUTPUT_BYTES", 1000):
            result = run_collect(OFFLINE + ["--out", str(tmp / "x.json")])
            self.assertEqual(result.code, 2)
            self.assertFalse((tmp / "x.json").exists())

    def test_unreadable_previous_exits_2(self):
        with temp_dir() as tmp:
            (tmp / "x.json").write_text("{", encoding="utf-8")
            self.assertEqual(run_collect(["--out", str(tmp / "x.json")]).code, 2)

    def test_always_list_failure_is_not_fatal(self):
        def script(request):
            return CallFailed("getList", "request failed (timeout)") if request.api == "alw" else None

        with temp_dir() as tmp:
            result = run_collect(["--out", str(tmp / "x.json")], transport=ScriptedTransport(script))
            self.assertEqual(result.code, 0, result.text)
            self.assertIn("always-held list skipped", result.out)

    # Plan A10/A11: only a run started with --fees-first (the workflow adds it
    # for the 15:10 KST schedule) asks for fees before getJMList. The clock
    # does not decide: GitHub starts schedules hours late (run 41472965555
    # began 9h24m late, after midnight KST).
    AFTERNOON_RUN = SUNDAY_RUN + timedelta(hours=12)  # 2026-11-01 15:10 KST

    def first_index(self, requests, operation):
        return [r.operation for r in requests if r.param("jmCd")].index(operation)

    def test_without_fees_first_get_jm_list_comes_first(self):
        for when in (SUNDAY_RUN, self.AFTERNOON_RUN):  # morning and afternoon clocks alike
            with temp_dir() as tmp:
                result = run_collect(["--out", str(tmp / "x.json")], clock=FakeClock(when))
            self.assertEqual(result.code, 0, result.text)
            requests = result.transport.requests
            self.assertLess(self.first_index(requests, "getJMList"), self.first_index(requests, "getFeeList"), when)

    def test_fees_first_calls_get_fee_list_first_for_the_same_targets(self):
        with temp_dir() as tmp:
            plain = run_collect(["--out", str(tmp / "m.json")])
            fees_first = run_collect(["--out", str(tmp / "a.json"), "--fees-first"])
        self.assertEqual(fees_first.code, 0, fees_first.text)
        requests = fees_first.transport.requests
        last_fee = max(i for i, r in enumerate(requests) if r.operation == "getFeeList")
        first_jm = next(i for i, r in enumerate(requests) if r.operation == "getJMList")
        self.assertLess(last_fee, first_jm)
        for operation in ("getJMList", "getFeeList"):
            def codes(result):
                return sorted(r.param("jmCd") for r in result.transport.requests if r.operation == operation)
            self.assertEqual(codes(fees_first), codes(plain))
        # Qual verdicts still come first.
        self.assertLess(self.first_index(requests, "getQualExamSchdList"), self.first_index(requests, "getFeeList"))

    def test_fees_first_holds_even_when_the_run_starts_after_midnight_kst(self):
        late = SUNDAY_RUN + timedelta(hours=21)  # next day 00:10 KST, like run 41472965555
        self.assertEqual(kst_now(late).hour, 0)
        with temp_dir() as tmp:
            result = run_collect(["--out", str(tmp / "x.json"), "--fees-first"], clock=FakeClock(late))
        self.assertEqual(result.code, 0, result.text)
        requests = result.transport.requests
        self.assertLess(self.first_index(requests, "getFeeList"), self.first_index(requests, "getJMList"))

    def test_fees_first_deadline_fills_fees_and_skips_get_jm_list(self):
        transport = ScriptedTransport(lambda request: None)
        fee_count = len([c for i, c in enumerate(JM_CODES) if i % 7 == 6])
        clock = RequestClock(SUNDAY_RUN, transport, lambda r: r.operation == "getFeeList", fee_count)
        with temp_dir() as tmp:
            result = run_collect(["--out", str(tmp / "x.json"), "--fees-first"], transport=transport, clock=clock)
        self.assertEqual(result.code, 0, result.text)
        self.assertIn(f"getFeeList={fee_count}/{fee_count} ", result.out)
        self.assertIn(f"getJMList=0/{fee_count} ", result.out)
        self.assertEqual([r for r in transport.requests if r.operation == "getJMList"], [])

    def test_daily_qual_item_calls_for_every_craftsman_before_get_jm_list(self):
        with temp_dir() as tmp:
            result = run_collect(["--out", str(tmp / "x.json")])
        self.assertEqual(result.code, 0, result.text)
        calls = qual_item_requests(result.transport)
        self.assertEqual(sorted(r.param("jmCd") for r in calls), CRAFTSMEN)
        self.assertEqual(len(CRAFTSMEN), 163)
        self.assertTrue(all(r.param("implYy") == "2026" and r.param("numOfRows") == "50" for r in calls))
        order = [r.operation for r in result.transport.requests if r.param("jmCd")]
        self.assertLess(order.index("getQualExamSchdList"), order.index("getJMList"))
        last_qual = max(i for i, op in enumerate(order) if op == "getQualExamSchdList")
        self.assertLess(last_qual, order.index("getJMList"))
        self.assertIn("qualItem=163/163 ", result.out)
        self.assertIn("qualVerdict=true:1,false:1,none:161 ", result.out)
        self.assertLess(result.transport.requests.__len__(), 900)

    def test_qual_verdicts_land_in_the_output(self):
        with temp_dir() as tmp:
            out = tmp / "x.json"
            result = run_collect(["--out", str(out)])
            self.assertEqual(result.code, 0, result.text)
            items = {i["jmCd"]: i for i in read_json(out)["items"]}
        self.assertIs(items["7780"]["regularOnly"], True)
        self.assertIs(items["7910"]["regularOnly"], False)
        self.assertEqual([r["basis"] for r in items["7780"]["rounds"]], ["grade"] * 4)

    def test_qual_item_failure_keeps_previous_verdict_and_run_succeeds(self):
        with temp_dir() as tmp:
            out = tmp / "x.json"
            self.assertEqual(run_collect(["--out", str(out)]).code, 0)
            failing = ScriptedTransport(fail_items("getQualExamSchdList", {"7780", "7910"}))
            result = run_collect(["--out", str(out)], transport=failing)
            self.assertEqual(result.code, 0, result.text)
            items = {i["jmCd"]: i for i in read_json(out)["items"]}
        self.assertIs(items["7780"]["regularOnly"], True)
        self.assertIs(items["7910"]["regularOnly"], False)
        self.assertIn("qualItem=161/163 ", result.out)
        self.assertNotIn("warning", result.out)

    def test_more_than_twenty_percent_qual_failures_only_warn(self):
        failing = ScriptedTransport(fail_items("getQualExamSchdList", set(CRAFTSMEN[:40])))
        with temp_dir() as tmp:
            result = run_collect(["--out", str(tmp / "x.json")], transport=failing)
        self.assertEqual(result.code, 0, result.text)
        self.assertIn("warning: qualExamSchd item verdict failed or skipped for 40 of 163", result.out)

    def test_qual_budget_guard_is_not_fatal_and_year_lists_come_first(self):
        with temp_dir() as tmp, mock.patch.dict(budget.DAILY_CALL_LIMITS, {"qual": 10}):
            result = run_collect(["--out", str(tmp / "x.json")])
        self.assertEqual(result.code, 0, result.text)
        self.assertIn("call guard", result.out)
        self.assertIn("calls=alw:1,ntq:", result.out)
        self.assertIn("qual:10 ", result.out)

    def test_qual_items_stop_at_their_share_of_the_run(self):
        # Past one third of the run budget: no more qual item calls, but
        # getJMList still runs.
        transport = ScriptedTransport()
        is_qual_item = lambda r: r.api == "qual" and r.param("jmCd") is not None  # noqa: E731
        clock = RequestClock(SUNDAY_RUN, transport, is_qual_item, 5, late=500)
        with temp_dir() as tmp:
            result = run_collect(["--out", str(tmp / "x.json")], transport=transport, clock=clock)
        self.assertEqual(result.code, 0, result.text)
        self.assertEqual(len(qual_item_requests(result.transport)), 5)
        self.assertIn("getJMList=73/73 ", result.out)
        self.assertIn("qualItem=5/163 qualSkipped=158 ", result.out)
        self.assertIn("warning: qualExamSchd item verdict failed or skipped for 158 of 163", result.out)

    def test_qual_window_starts_at_the_per_item_stage(self):
        # Daily lists took 900 s: the qual window is a third of what is left
        # (900 + (1404 - 900) / 3 = 1068), not a third from the run start (468).
        values = iter([0])
        clock = FakeClock(SUNDAY_RUN)
        clock.monotonic = lambda: next(values, 900)
        with temp_dir() as tmp:
            result = run_collect(["--out", str(tmp / "x.json")], clock=clock)
        self.assertEqual(result.code, 0, result.text)
        self.assertIn("qualItem=163/163 qualSkipped=0 ", result.out)
        self.assertIn("getJMList=73/73 ", result.out)

    def test_qual_window_is_empty_when_the_deadline_already_passed(self):
        values = iter([0])
        clock = FakeClock(SUNDAY_RUN)
        clock.monotonic = lambda: next(values, 2000)
        with temp_dir() as tmp:
            result = run_collect(["--out", str(tmp / "x.json")], clock=clock)
        self.assertEqual(result.code, 0, result.text)
        self.assertIn("qualItem=0/163 qualSkipped=163 ", result.out)

    def test_missing_key_exits_1(self):
        result = run_collect(["--out", "/nonexistent/x.json"], env={})
        self.assertEqual(result.code, 1)
        self.assertIn("DATA_GO_KR_SERVICE_KEY is not set", result.err)
        self.assertEqual(result.transport.requests, [])


if __name__ == "__main__":
    unittest.main()
