import copy
import unittest
from datetime import datetime, timezone

from tools.certexam.errors import ValidationFailed
from tools.certexam.validate import validate_document
from tools.certexam.write_output import (
    format_generated_at,
    load_previous,
    render,
    write_if_changed,
)
from tools.certexam.tests.support import temp_dir


def round_(seq=1, **overrides):
    base = {
        "year": 2026, "seq": seq, "label": f"2026년 정기 기사 {seq}회", "basis": "item",
        "written": {"regStart": "2026-01-12", "regEnd": "2026-01-15", "examStart": "2026-01-30",
                    "examEnd": "2026-03-03", "pass": "2026-03-11"},
        "docSubmit": {"start": "2026-01-30", "end": "2026-03-20"},
        "practical": None,
    }
    return {**base, **overrides}


def item(jm_cd="1320", **overrides):
    base = {
        "jmCd": jm_cd, "name": "정보처리기사", "grade": "기사", "field": "정보통신", "subField": "정보기술",
        "fee": {"written": 19400, "practical": 22600}, "regularOnly": True, "rounds": [round_()],
    }
    return {**base, **overrides}


def document(items):
    return {
        "schemaVersion": 1,
        "generatedAt": "2026-10-31T18:10:00Z",
        "source": {"label": "공공데이터포털 (한국산업인력공단 제공)", "url": "https://www.data.go.kr/data/15003029/openapi.do"},
        "items": items,
    }


def many(count):
    return [item(f"{n:04d}") for n in range(count)]


class ValidateTest(unittest.TestCase):
    def test_valid_document_passes_unchanged(self):
        doc = document(many(3))
        report = validate_document(doc)
        self.assertEqual(report.document, doc)
        self.assertEqual((report.dropped_items, report.dropped_rounds), ((), 0))

    def test_bad_items_are_dropped(self):
        bad = [
            item("132"), item("abcd"), item(name=""), item(name="가" * 61), item(grade="특급"),
            item(field="x" * 41), item(fee={"written": "19400", "practical": 1}), item(regularOnly="yes"),
            item(name="정보\x07처리"), item(rounds=[round_(n) for n in range(1, 14)]),
        ]
        bad = [{**b, "jmCd": b["jmCd"] if b["jmCd"] != "1320" else f"9{n:03d}"} for n, b in enumerate(bad)]
        report = validate_document(document(many(60) + bad))
        self.assertEqual(len(report.dropped_items), len(bad))
        self.assertEqual(len(report.document["items"]), 60)

    def test_bad_rounds_are_dropped_not_the_item(self):
        bad_rounds = [
            round_(2, written={**round_()["written"], "regStart": "2026-01-16"}),  # start after end
            round_(3, written={**round_()["written"], "pass": "20260311"}),
            round_(4, basis="other"),
            round_(5, label="x" * 61),
            round_(6, written=None, docSubmit=None, practical=None),
            round_(0),
            round_(7, docSubmit={"start": "2026-01-01"}),
        ]
        report = validate_document(document([item(rounds=[round_()] + bad_rounds)]))
        self.assertEqual([r["seq"] for r in report.document["items"][0]["rounds"]], [1])
        self.assertEqual(report.dropped_rounds, len(bad_rounds))

    def test_more_than_twenty_percent_invalid_rejects_document(self):
        items = many(8) + [item(f"9{n:03d}", name="") for n in range(3)]
        with self.assertRaises(ValidationFailed):
            validate_document(document(items))

    def test_more_than_hundred_invalid_rejects_document(self):
        items = many(1000) + [item(f"x{n:03d}") for n in range(101)]
        with self.assertRaises(ValidationFailed):
            validate_document(document(items))

    def test_document_rules(self):
        for key, value in (
            ("schemaVersion", 2), ("generatedAt", "2026-10-31"), ("items", {}),
            ("source", {"label": "", "url": "https://x"}), ("source", {"label": "a", "url": "http://x"}),
        ):
            doc = {**document([item()]), key: value}
            with self.assertRaises(ValidationFailed, msg=key):
                validate_document(doc)

    def test_duplicate_items_dropped(self):
        report = validate_document(document(many(10) + [item("0001")]))
        self.assertEqual(len(report.document["items"]), 10)


class RestorePreviousTest(unittest.TestCase):
    def test_dropped_item_keeps_previous_valid_record(self):
        from tools.certexam.validate import restore_previous

        previous_items = {"0001": item("0001"), "0002": item("0002", name="")}
        report = validate_document(document(many(1) + [item("0001", name=""), item("0002", name="")] + many(10)[3:]))
        restored = restore_previous(report, previous_items)
        codes = [i["jmCd"] for i in restored.document["items"]]
        self.assertIn("0001", codes)
        self.assertNotIn("0002", codes)  # previous record is invalid too
        self.assertEqual(restored.restored_items, ("0001",))


class WriterTest(unittest.TestCase):
    def test_render_is_deterministic_and_sorted(self):
        doc = document([item("2290"), item("1320", rounds=[round_(3), round_(1)])])
        shuffled = copy.deepcopy(doc)
        shuffled["items"].reverse()
        self.assertEqual(render(doc), render(shuffled))
        text = render(doc).decode("utf-8")
        self.assertLess(text.index('"jmCd":"1320"'), text.index('"jmCd":"2290"'))
        self.assertLess(text.index('"seq":1'), text.index('"seq":3'))
        self.assertTrue(text.startswith('{"generatedAt"'))
        self.assertIn("정보처리기사", text)

    def test_same_input_twice_gives_identical_bytes(self):
        with temp_dir() as tmp:
            a, b = tmp / "a.json", tmp / "b.json"
            write_if_changed(a, document(many(5)), None)
            write_if_changed(b, document(many(5)), None)
            self.assertEqual(a.read_bytes(), b.read_bytes())

    def test_unchanged_content_is_not_rewritten(self):
        with temp_dir() as tmp:
            path = tmp / "exams.json"
            self.assertTrue(write_if_changed(path, document(many(2)), None).written)
            previous = load_previous(path)
            later = {**document(many(2)), "generatedAt": "2026-11-01T18:10:00Z"}
            self.assertFalse(write_if_changed(path, later, previous).written)
            self.assertEqual(load_previous(path)["generatedAt"], "2026-10-31T18:10:00Z")

    def test_changed_content_is_written_with_new_generated_at(self):
        with temp_dir() as tmp:
            path = tmp / "exams.json"
            write_if_changed(path, document(many(2)), None)
            later = {**document(many(3)), "generatedAt": "2026-11-01T18:10:00Z"}
            self.assertTrue(write_if_changed(path, later, load_previous(path)).written)
            self.assertEqual(load_previous(path)["generatedAt"], "2026-11-01T18:10:00Z")

    def test_over_two_megabytes_is_refused(self):
        big = [item(f"{n:04d}", name="가" * 60, rounds=[round_(s) for s in range(1, 13)]) for n in range(2000)]
        with temp_dir() as tmp:
            with self.assertRaises(ValidationFailed):
                write_if_changed(tmp / "exams.json", document(big), None)
            self.assertFalse((tmp / "exams.json").exists())

    def test_unreadable_previous_is_a_validation_failure(self):
        with temp_dir() as tmp:
            path = tmp / "exams.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(ValidationFailed):
                load_previous(path)
            path.write_text('{"items": 3}', encoding="utf-8")
            with self.assertRaises(ValidationFailed):
                load_previous(path)
            self.assertIsNone(load_previous(tmp / "missing.json"))

    def test_write_result_reports_size(self):
        with temp_dir() as tmp:
            path = tmp / "exams.json"
            result = write_if_changed(path, document(many(2)), None)
            self.assertEqual(result.size, len(path.read_bytes()))

    def test_generated_at_format(self):
        when = datetime(2026, 11, 1, 3, 10, 5, tzinfo=timezone.utc)
        self.assertEqual(format_generated_at(when), "2026-11-01T03:10:05Z")


if __name__ == "__main__":
    unittest.main()
