"""Plan A7 ②: 정기·상시 verdict for one 기능사 item from qualExamSchd rows."""

import unittest

from tools.certexam.qual_verdict import classify_qual_item, count_rounds, is_gray_zone
from tools.certexam.tests.support import rows


def row(seq, written_exam="", practical_exam="", year="2026", reg=""):
    return {
        "implyy": year, "implseq": str(seq), "docregstartdt": reg,
        "docexamstartdt": written_exam, "pracexamstartdt": practical_exam,
    }


def always_rows(count, start=1):
    return [row(n, written_exam=f"2026{(n % 12) + 1:02d}{(n % 27) + 1:02d}") for n in range(start, start + count)]


class ClassifyTest(unittest.TestCase):
    def test_regular_fixture_7780_is_true(self):
        self.assertIs(classify_qual_item(rows("qual_2026_T_7780.json"), 2026), True)

    def test_always_fixture_7910_is_false(self):
        self.assertIs(classify_qual_item(rows("qual_2026_T_7910.json"), 2026), False)

    def test_mixed_regular_and_always_is_false(self):
        mixed = rows("qual_2026_T_7780.json") + rows("qual_2026_T_7910.json")
        self.assertIs(classify_qual_item(mixed, 2026), False)

    def test_no_rows_gives_no_verdict(self):
        self.assertIsNone(classify_qual_item([], 2026))

    def test_ten_unpaired_rounds_are_not_enough_for_always(self):
        self.assertIsNone(classify_qual_item(always_rows(10), 2026))
        self.assertIs(classify_qual_item(always_rows(11), 2026), False)

    def test_gray_zone_paired_with_up_to_ten_unpaired_is_true(self):
        data = [row(1, "20260120", "20260314"), row(1, "20260120", "20260314")] + always_rows(5, start=2)
        self.assertEqual(count_rounds(data, 2026), (1, 5))
        self.assertIs(classify_qual_item(data, 2026), True)
        self.assertTrue(is_gray_zone(1, 5))
        self.assertTrue(is_gray_zone(1, 10))
        self.assertFalse(is_gray_zone(1, 0))
        self.assertFalse(is_gray_zone(0, 5))
        self.assertFalse(is_gray_zone(1, 11))

    def test_round_zero_is_ignored(self):
        exemption_pair = [row(0, "", "20260613"), row(0, "", "20260613")]
        self.assertIsNone(classify_qual_item(exemption_pair, 2026))
        self.assertIs(classify_qual_item(exemption_pair + [row(1, "20260120", "20260314")] * 2, 2026), True)

    def test_pair_needs_a_shared_exam_date(self):
        different = [row(1, "20260120", "20260314"), row(1, "20260121", "20260315")]
        self.assertIsNone(classify_qual_item(different, 2026))
        no_dates = [row(1), row(1)]
        self.assertIsNone(classify_qual_item(no_dates, 2026))

    def test_rows_of_other_years_are_ignored(self):
        other_year = [row(1, "20270120", "20270314", year="2027")] * 2
        self.assertIsNone(classify_qual_item(other_year, 2026))

    def test_xxxxxxxx_dates_are_not_exam_dates(self):
        self.assertIsNone(classify_qual_item([row(1, "XXXXXXXX", ""), row(1, "XXXXXXXX", "")], 2026))


if __name__ == "__main__":
    unittest.main()
