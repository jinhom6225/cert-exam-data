import json
import unittest
from collections import Counter
from pathlib import Path

from tools.certexam.build_jm_list import build
from tools.certexam.grades import derive_grade, group_from_text
from tools.certexam.parse_lists import parse_always_names, parse_jm_list
from tools.certexam.tests.support import FIXTURES, rows, temp_dir

JM_LIST = Path(__file__).resolve().parent.parent / "jm_list.json"


class GradeDerivationTest(unittest.TestCase):
    def test_series_direct(self):
        self.assertEqual(derive_grade("가스기술사", "01"), "기술사")
        self.assertEqual(derive_grade("용접기능장", "02"), "기능장")
        self.assertEqual(derive_grade("전기기능사", "04"), "기능사")

    def test_series_03_by_name(self):
        self.assertEqual(derive_grade("정보처리기사", "03"), "기사")
        self.assertEqual(derive_grade("정보처리산업기사", "03"), "산업기사")
        self.assertEqual(derive_grade("사회조사분석사2급", "03"), "서비스")

    def test_parentheses_are_stripped_and_industrial_checked_first(self):
        self.assertEqual(derive_grade("소방설비기사(기계분야)", "03"), "기사")
        self.assertEqual(derive_grade("신재생에너지발전설비산업기사(태양광)", "03"), "산업기사")
        self.assertEqual(derive_grade("생물분류기사(동물)", "03"), "기사")
        self.assertEqual(derive_grade("가상종목(산업기사)", "03"), "서비스")

    def test_unknown_series_falls_back_to_name(self):
        self.assertEqual(derive_grade("가스기술사", None), "기술사")
        self.assertEqual(derive_grade("직업상담사1급", "99"), "서비스")

    def test_description_groups(self):
        self.assertEqual(group_from_text("국가기술자격 기술사 "), "PE")
        self.assertEqual(group_from_text("국가기술자격 기능사 "), "C")
        self.assertEqual(group_from_text("국가기술자격 산업기사 "), "E")
        self.assertEqual(group_from_text("국가기술자격 기능장 "), "MC")
        self.assertIsNone(group_from_text("국가전문자격"))


class JmListTest(unittest.TestCase):
    def test_fixture_counts(self):
        entries = parse_jm_list(rows("jmlist_15003024_getList.xml"))
        self.assertEqual(len(entries), 513)
        counts = Counter(entry["grade"] for entry in entries)
        # Name rule from plan A3 (parentheses stripped, 산업기사 before 기사).
        self.assertEqual(
            counts,
            Counter({"기능사": 163, "산업기사": 117, "기사": 109, "기술사": 79, "기능장": 29, "서비스": 16}),
        )
        self.assertEqual([e["jmCd"] for e in entries], sorted(e["jmCd"] for e in entries))

    def test_professional_qualifications_excluded(self):
        names = {e["name"] for e in parse_jm_list(rows("jmlist_15003024_getList.xml"))}
        self.assertNotIn("손해평가사", names)

    def test_entry_shape(self):
        entries = {e["jmCd"]: e for e in parse_jm_list(rows("jmlist_15003024_getList.xml"))}
        self.assertEqual(
            entries["1320"],
            {"jmCd": "1320", "name": "정보처리기사", "grade": "기사", "field": "정보통신", "subField": "정보기술"},
        )

    def test_bundled_jm_list_matches_fixture(self):
        self.assertEqual(JM_LIST.read_bytes(), build(FIXTURES / "jmlist_15003024_getList.xml"))
        self.assertEqual(len(json.loads(JM_LIST.read_text(encoding="utf-8"))), 513)

    def test_build_rejects_utf16_doctype(self):
        from tools.certexam.errors import CallFailed

        with temp_dir() as tmp:
            source = tmp / "list.xml"
            source.write_bytes('<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE r [<!ENTITY a "b">]><r/>'.encode("utf-16"))
            with self.assertRaises(CallFailed) as caught:
                build(source)
            self.assertFalse(caught.exception.retryable)

    def test_always_names(self):
        names = parse_always_names(rows("alw_15088943_p1.xml"))
        self.assertEqual(len(names), 14)
        self.assertIn("한식조리기능사", names)
        self.assertIn("미용사(네일)", names)


if __name__ == "__main__":
    unittest.main()
