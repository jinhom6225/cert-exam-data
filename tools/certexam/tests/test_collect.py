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
            self.assertIs(items["0011"]["regularOnly"], True)
            self.assertIsNone(items["7780"]["regularOnly"])
            craftsman_unknown = [i for i in doc["items"] if i["regularOnly"] is None]
            self.assertTrue(all(i["grade"] == "기능사" for i in craftsman_unknown))
            self.assertIn("result=written", result.out)
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

    def test_up_to_five_percent_failures_keep_previous_values(self):
        with temp_dir() as tmp:
            out = self.previous_file(tmp)
            when = run_day_for("1320")
            todays = [c for i, c in enumerate(JM_CODES) if i % 7 == JM_CODES.index("1320") % 7]
            failing = {"1320"} | set(todays[:2])
            transport = ScriptedTransport(fail_items("getJMList", failing))
            result = run_collect(["--out", str(out)], transport=transport, clock=FakeClock(when))
            self.assertEqual(result.code, 0, result.text)
            item = {i["jmCd"]: i for i in read_json(out)["items"]}["1320"]
            self.assertEqual([r["basis"] for r in item["rounds"]], ["item"] * 3)

    def test_more_than_five_percent_failures_exit_1_without_writing(self):
        with temp_dir() as tmp:
            out = self.previous_file(tmp)
            before = out.read_bytes()
            transport = ScriptedTransport(fail_items("getFeeList", set(JM_CODES[:100])))
            result = run_collect(["--out", str(out)], transport=transport)
            self.assertEqual(result.code, 1)
            self.assertIn("getFeeList failed for", result.err)
            self.assertEqual(out.read_bytes(), before)

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

    def test_call_guard_exits_1(self):
        with temp_dir() as tmp, mock.patch.dict(budget.DAILY_CALL_LIMITS, {"ntq": 20}):
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

    def test_failures_among_attempted_items_still_count_after_deadline(self):
        fail_all = ScriptedTransport(fail_items("getJMList", set(JM_CODES)))
        with temp_dir() as tmp:
            result = run_collect(["--out", str(tmp / "x.json")], transport=fail_all, clock=FakeClock(SUNDAY_RUN, tick=120.0))
            self.assertEqual(result.code, 1)
            self.assertIn("getJMList failed for", result.err)

    def test_five_percent_rule_uses_planned_targets(self):
        # 5 attempts before the deadline, 2 of them fail: 2/73 planned is under 5%.
        attempted = [c for i, c in enumerate(JM_CODES) if i % 7 == 6][:2]
        transport = ScriptedTransport(fail_items("getJMList", set(attempted)))
        clock = ScriptedClock(SUNDAY_RUN, [0, 0, 0, 0, 0, 0, 10_000])
        with temp_dir() as tmp:
            result = run_collect(["--out", str(tmp / "x.json")], transport=transport, clock=clock)
        self.assertEqual(result.code, 0, result.text)
        self.assertIn("getJMList=3/73", result.out)

    def test_deadline_is_measured_from_run_start_minus_worst_call(self):
        # 1410 s after start is past 25 min minus one worst-case call (96 s).
        clock = ScriptedClock(SUNDAY_RUN, [0, 1410])
        with temp_dir() as tmp:
            result = run_collect(["--out", str(tmp / "x.json")], clock=clock)
        self.assertEqual(result.code, 0, result.text)
        self.assertEqual([r for r in result.transport.requests if r.param("jmCd")], [])
        self.assertIn("skippedAtDeadline=146 ", result.out)

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

    def test_missing_key_exits_1(self):
        result = run_collect(["--out", "/nonexistent/x.json"], env={})
        self.assertEqual(result.code, 1)
        self.assertIn("DATA_GO_KR_SERVICE_KEY is not set", result.err)
        self.assertEqual(result.transport.requests, [])


if __name__ == "__main__":
    unittest.main()
