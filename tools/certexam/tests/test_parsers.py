import unittest

from tools.certexam.grades import GROUP_C, GROUP_E, GROUP_MC, GROUP_PE
from tools.certexam.parse_fee import parse_fee
from tools.certexam.parse_grade import (
    combine_grade_rounds,
    parse_grade_list,
    parse_qual_schedule,
)
from tools.certexam.parse_item import parse_item_schedule
from tools.certexam.rounds import parse_title, pick_regular_rows
from tools.certexam.textutil import clean_text, normalize_date
from tools.certexam.tests.support import rows


def written_reg(round_):
    return round_["written"]["regStart"] if round_["written"] else None


class DateAndTextTest(unittest.TestCase):
    def test_yyyymmdd_to_iso(self):
        self.assertEqual(normalize_date("20260112"), "2026-01-12")

    def test_placeholder_and_blank_dates_are_null(self):
        for value in ("XXXXXXXX", "", "   ", None, "20260230", "2026-01-12"):
            self.assertIsNone(normalize_date(value), value)

    def test_clean_text_drops_control_and_invisible_chars(self):
        self.assertEqual(clean_text(" 정보​처리\x07기사 \n"), "정보처리기사")
        self.assertIsNone(clean_text(" \t "))

    def test_titles(self):
        self.assertEqual(parse_title("2026년 정기 기사 1회"), (2026, 1, "정기"))
        self.assertEqual(parse_title("기사,산업기사(2026년도 제3회)"), (2026, 3, None))
        self.assertEqual(parse_title("국가기술자격 기사 (2027년도 제1회)"), (2027, 1, None))
        self.assertEqual(parse_title("2026년 상시 기능사 201회"), (2026, 201, "상시"))
        self.assertEqual(
            parse_title("2026년 정기 기능사 산업별 맞춤형 고교등 필기면제검정"),
            (None, None, "정기"),
        )


class ItemScheduleTest(unittest.TestCase):
    def test_get_jm_list_1320(self):
        schedule = parse_item_schedule(rows("ntq_getJMList_1320.xml"))
        self.assertTrue(schedule.regular_only)
        self.assertEqual(schedule.years, frozenset({2026}))
        self.assertEqual([(r["year"], r["seq"]) for r in schedule.rounds], [(2026, 1), (2026, 2), (2026, 3)])
        first = schedule.rounds[0]
        self.assertEqual(first["basis"], "item")
        self.assertEqual(first["label"], "2026년 정기 기사 1회")
        self.assertEqual(
            first["written"],
            {"regStart": "2026-01-12", "regEnd": "2026-01-15", "examStart": "2026-01-30",
             "examEnd": "2026-03-03", "pass": "2026-03-11"},
        )
        self.assertEqual(first["docSubmit"], {"start": "2026-01-30", "end": "2026-03-20"})
        self.assertEqual(first["practical"]["passStart"], "2026-06-12")
        self.assertEqual(first["practical"]["passEnd"], "2026-08-11")

    def test_get_jm_list_0752_single_round(self):
        schedule = parse_item_schedule(rows("ntq_getJMList_0752.xml"))
        self.assertEqual([(r["year"], r["seq"]) for r in schedule.rounds], [(2026, 138)])
        self.assertIsNotNone(schedule.rounds[0]["docSubmit"])

    def test_always_row_makes_item_not_regular_and_is_dropped(self):
        data = [
            {"implplannm": "2026년 상시 기능사 1회", "docregstartdt": "20260106"},
            {"implplannm": "2026년 정기 기능사 2회", "docregstartdt": "20260316"},
        ]
        schedule = parse_item_schedule(data)
        self.assertIs(schedule.regular_only, False)
        self.assertEqual([r["seq"] for r in schedule.rounds], [2])

    def test_round_zero_and_unknown_titles_are_dropped(self):
        data = [
            {"implplannm": "2026년 정기 기능사 0회", "docregstartdt": "20260106"},
            {"implplannm": "정체불명", "docregstartdt": "20260106"},
        ]
        schedule = parse_item_schedule(data)
        self.assertEqual(schedule.rounds, ())
        self.assertIs(schedule.regular_only, True)

    def test_no_marker_means_unknown(self):
        schedule = parse_item_schedule([{"implplannm": "2026년 기사 1회", "docregstartdt": "20260106"}])
        self.assertIsNone(schedule.regular_only)

    def test_empty_answer(self):
        schedule = parse_item_schedule([])
        self.assertEqual((schedule.rounds, schedule.regular_only, schedule.years), ((), None, frozenset()))


class GradeListTest(unittest.TestCase):
    def test_e_list_picks_regular_window(self):
        rounds = parse_grade_list(GROUP_E, rows("ntq_getEList.xml"))
        self.assertEqual(sorted(rounds), [(2026, 1), (2026, 2), (2026, 3)])
        self.assertEqual(written_reg(rounds[(2026, 1)]), "2026-01-12")
        self.assertEqual(rounds[(2026, 1)]["written"]["regEnd"], "2026-01-15")
        self.assertEqual(rounds[(2026, 1)]["basis"], "grade")
        self.assertEqual(rounds[(2026, 1)]["label"], "2026년 정기 기사 1회")

    def test_single_day_fields_and_misspelt_doc_submit(self):
        round_ = parse_grade_list(GROUP_MC, rows("ntq_getMCList.xml"))[(2026, 79)]
        self.assertEqual(round_["written"]["examStart"], "2026-01-24")
        self.assertEqual(round_["written"]["examEnd"], "2026-01-24")
        self.assertEqual(round_["docSubmit"], {"start": "2026-01-26", "end": "2026-02-10"})
        self.assertEqual(round_["practical"]["passStart"], round_["practical"]["passEnd"])
        self.assertEqual(round_["practical"]["regStart"], "2026-02-02")

    def test_pe_list_three_rows_per_round(self):
        rounds = parse_grade_list(GROUP_PE, rows("ntq_getPEList.xml"))
        self.assertEqual(sorted(rounds), [(2026, 138), (2026, 139), (2026, 140)])
        self.assertEqual(rounds[(2026, 138)]["practical"]["regStart"], "2026-03-03")

    def test_c_list_regular_only_without_doc_submit(self):
        rounds = parse_grade_list(GROUP_C, rows("ntq_getCList.xml"))
        self.assertEqual(sorted(rounds), [(2026, 1), (2026, 2), (2026, 3), (2026, 4)])
        self.assertTrue(all(r["docSubmit"] is None for r in rounds.values()))
        self.assertEqual(written_reg(rounds[(2026, 1)]), "2026-01-06")

    def test_field_name_casing_does_not_matter(self):
        camel = b"""<?xml version="1.0" encoding="UTF-8"?><response><header><resultCode>00</resultCode>
        </header><body><items><item><description>\xea\xb8\xb0\xec\x88\xa0\xec\x82\xac(2026\xeb\x85\x84\xeb\x8f\x84 \xec\xa0\x9c138\xed\x9a\x8c)</description>
        <docRegStartDt>20260106</docRegStartDt><DOCEXAMDT>20260207</DOCEXAMDT></item></items></body></response>"""
        from tools.certexam.payload import decode

        round_ = parse_grade_list(GROUP_PE, decode("getPEList", camel).rows)[(2026, 138)]
        self.assertEqual(round_["written"]["regStart"], "2026-01-06")
        self.assertEqual(round_["written"]["examStart"], "2026-02-07")
        self.assertIsNone(round_["practical"])


def practical_reg(round_):
    return (round_["practical"]["regStart"], round_["practical"]["regEnd"])


class SplitPracticalWindowTest(unittest.TestCase):
    """Rows sharing the written window split the practical registration."""

    def test_pe_138_merges_practical_windows(self):
        rounds = parse_grade_list(GROUP_PE, rows("ntq_getPEList.xml"))
        self.assertEqual(practical_reg(rounds[(2026, 138)]), ("2026-03-03", "2026-04-02"))
        self.assertEqual(written_reg(rounds[(2026, 138)]), "2026-01-06")

    def test_e_3_merges_practical_windows(self):
        rounds = parse_grade_list(GROUP_E, rows("ntq_getEList.xml"))
        self.assertEqual(practical_reg(rounds[(2026, 3)]), ("2026-09-21", "2026-09-28"))
        self.assertEqual(rounds[(2026, 3)]["written"]["regEnd"], "2026-07-23")

    def test_matches_item_fixtures(self):
        pe = parse_grade_list(GROUP_PE, rows("ntq_getPEList.xml"))[(2026, 138)]
        item_0752 = parse_item_schedule(rows("ntq_getJMList_0752.xml")).rounds[0]
        self.assertEqual(practical_reg(pe), practical_reg(item_0752))
        e3 = parse_grade_list(GROUP_E, rows("ntq_getEList.xml"))[(2026, 3)]
        item_1320 = parse_item_schedule(rows("ntq_getJMList_1320.xml")).rounds[2]
        self.assertEqual(practical_reg(e3), practical_reg(item_1320))

    def test_other_written_window_is_not_merged(self):
        rounds = parse_grade_list(GROUP_E, rows("ntq_getEList.xml"))
        self.assertEqual(practical_reg(rounds[(2026, 1)]), ("2026-03-23", "2026-03-26"))


class QualScheduleTest(unittest.TestCase):
    def test_regular_row_is_chosen_by_date_not_order(self):
        # qual_2026_T_1320 lists the extra window (01-24) before the regular one (01-12).
        first_rows = rows("qual_2026_T_1320.json")
        self.assertEqual(first_rows[-2]["docregstartdt"], "20260124")
        rounds = parse_qual_schedule(first_rows)[GROUP_E]
        self.assertEqual(written_reg(rounds[(2026, 1)]), "2026-01-12")
        self.assertEqual(written_reg(rounds[(2026, 3)]), "2026-07-20")
        self.assertIsNone(rounds[(2026, 1)]["docSubmit"])

    def test_round_zero_is_dropped_and_regular_pairs_kept(self):
        rounds = parse_qual_schedule(rows("qual_2026_T_7780.json"))[GROUP_C]
        self.assertEqual(sorted(rounds), [(2026, 1), (2026, 2), (2026, 3), (2026, 4)])
        self.assertEqual(written_reg(rounds[(2026, 4)]), "2026-08-24")

    def test_always_held_rounds_are_not_taken_as_regular(self):
        self.assertEqual(parse_qual_schedule(rows("qual_2026_T_7910.json")).get(GROUP_C, {}), {})
        self.assertEqual(parse_qual_schedule(rows("qual_2026_T_p1.json")).get(GROUP_C, {}), {})

    def test_dropped_single_row_craftsman_rounds_are_logged_once(self):
        logs = []
        parse_qual_schedule(rows("qual_2026_T_7910.json"), log=logs.append)
        self.assertEqual(len(logs), 1)
        self.assertIn("41", logs[0])
        quiet = []
        parse_qual_schedule(rows("qual_2026_T_7780.json"), log=quiet.append)
        self.assertEqual(quiet, [])

    def test_mixed_regular_and_always_rows_keep_the_regular_pair(self):
        mixed = rows("qual_2026_T_7780.json") + rows("qual_2026_T_7910.json")
        rounds = parse_qual_schedule(mixed)[GROUP_C]
        self.assertEqual(sorted(rounds), [(2026, 1), (2026, 2), (2026, 3), (2026, 4)])
        self.assertEqual(rounds[(2026, 1)]["written"]["examStart"], "2026-01-20")

    def test_next_year_empty(self):
        self.assertEqual(parse_qual_schedule(rows("qual_2027_T_p1.json")), {})

    def test_next_year_grade_from_description(self):
        data = [
            {"implyy": "2027", "implseq": "1", "description": "국가기술자격 기술사 (2027년도 제141회)",
             "docregstartdt": "20270105"},
            {"implyy": "2027", "implseq": "1", "description": "국가기술자격 기사 (2027년도 제1회)",
             "docregstartdt": "20270112"},
            {"implyy": "2027", "implseq": "2", "description": "국가기술자격 기능장 (2027년도 제2회)",
             "docregstartdt": "20270301"},
        ]
        parsed = parse_qual_schedule(data)
        self.assertEqual(sorted(parsed), [GROUP_E, GROUP_MC, GROUP_PE])
        self.assertEqual(parsed[GROUP_E][(2027, 1)]["label"], "2027년 정기 기사 1회")

    def test_grade_lists_win_over_qual(self):
        lists = {GROUP_E: {(2026, 1): {"source": "list"}}}
        qual = {GROUP_E: {(2026, 1): {"source": "qual"}, (2027, 1): {"source": "qual"}}}
        combined = combine_grade_rounds(lists, qual)[GROUP_E]
        self.assertEqual(combined[(2026, 1)]["source"], "list")
        self.assertEqual(combined[(2027, 1)]["source"], "qual")


class PickRegularTest(unittest.TestCase):
    def test_earliest_written_window_wins_and_practical_is_merged(self):
        a = {"year": 2026, "seq": 1, "written": {"regStart": "2026-01-05", "regEnd": "2026-01-06"},
             "practical": {"regStart": "2026-03-01", "regEnd": "2026-03-02"}}
        b = {"year": 2026, "seq": 1, "written": {"regStart": "2026-01-01", "regEnd": "2026-01-02"},
             "practical": {"regStart": "2026-03-10", "regEnd": "2026-03-11"}}
        c = {"year": 2026, "seq": 1, "written": {"regStart": "2026-01-01", "regEnd": "2026-01-02"},
             "practical": {"regStart": "2026-03-05", "regEnd": None}}
        picked = pick_regular_rows([a, b, c])[(2026, 1)]
        self.assertEqual(picked["written"]["regStart"], "2026-01-01")
        self.assertEqual(picked["practical"], {"regStart": "2026-03-05", "regEnd": "2026-03-11"})
        self.assertEqual(b["practical"]["regStart"], "2026-03-10")  # inputs not mutated


class FeeTest(unittest.TestCase):
    def test_fee_1320(self):
        self.assertEqual(parse_fee(rows("ntq_getFeeList_1320.xml")), {"written": 19400, "practical": 22600})

    def test_other_shapes_are_null(self):
        for text in ("1차 : 19400", "필기 19400원", "", "1차 : 1,9400, 2차 : 22600"):
            self.assertIsNone(parse_fee([{"infogb": "응시수수료", "contents": text}]), text)

    def test_no_rows(self):
        self.assertIsNone(parse_fee([]))


if __name__ == "__main__":
    unittest.main()
