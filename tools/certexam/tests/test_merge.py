import unittest

from tools.certexam.grades import GROUP_C, GROUP_E, GROUP_PE
from tools.certexam.merge import MergeInputs, merge_items, universe_from_previous
from tools.certexam.parse_grade import parse_grade_list
from tools.certexam.parse_item import ItemSchedule, parse_item_schedule
from tools.certexam.tests.support import rows

ENGINEER = {"jmCd": "1320", "name": "정보처리기사", "grade": "기사", "field": "정보통신", "subField": "정보기술"}
GAS_PE = {"jmCd": "0752", "name": "가스기술사", "grade": "기술사", "field": "안전관리", "subField": "안전관리"}
COOK = {"jmCd": "7910", "name": "한식조리기능사", "grade": "기능사", "field": "음식서비스", "subField": "조리"}
ELEC = {"jmCd": "7780", "name": "전기기능사", "grade": "기능사", "field": "전기.전자", "subField": "전기"}


def grade_round(year, seq, reg="2027-01-12"):
    return {
        "year": year, "seq": seq, "label": f"{year}년 정기 기사 {seq}회", "basis": "grade",
        "written": {"regStart": reg, "regEnd": None, "examStart": None, "examEnd": None, "pass": None},
        "docSubmit": None, "practical": None,
    }


def item_round(year, seq, reg="2027-01-13"):
    return {**grade_round(year, seq, reg), "basis": "item", "label": f"{year}년 정기 기사 {seq}회"}


def keys(item):
    return [(r["year"], r["seq"], r["basis"]) for r in item["rounds"]]


def only(items, jm_cd):
    return next(item for item in items if item["jmCd"] == jm_cd)


class MergeTest(unittest.TestCase):
    def setUp(self):
        self.grades = {
            GROUP_E: parse_grade_list(GROUP_E, rows("ntq_getEList.xml")),
            GROUP_PE: parse_grade_list(GROUP_PE, rows("ntq_getPEList.xml")),
            GROUP_C: parse_grade_list(GROUP_C, rows("ntq_getCList.xml")),
        }
        self.grades[GROUP_E][(2027, 1)] = grade_round(2027, 1)

    def test_item_beats_grade_on_same_key_and_next_year_grade_stays(self):
        schedule = parse_item_schedule(rows("ntq_getJMList_1320.xml"))
        item = only(merge_items(MergeInputs([ENGINEER], self.grades, item_schedules={"1320": schedule})), "1320")
        self.assertEqual(keys(item), [(2026, 1, "item"), (2026, 2, "item"), (2026, 3, "item"), (2027, 1, "grade")])
        # getJMList says 06-12 for the final result; the grade list says 06-05.
        self.assertEqual(item["rounds"][0]["practical"]["passStart"], "2026-06-12")
        self.assertIs(item["regularOnly"], True)

    def test_grade_rounds_do_not_leak_into_years_the_item_covers(self):
        schedule = parse_item_schedule(rows("ntq_getJMList_0752.xml"))
        item = only(merge_items(MergeInputs([GAS_PE], self.grades, item_schedules={"0752": schedule})), "0752")
        self.assertEqual(keys(item), [(2026, 138, "item")])

    def test_item_without_item_data_gets_grade_rounds(self):
        item = only(merge_items(MergeInputs([GAS_PE], self.grades)), "0752")
        self.assertEqual([k[1] for k in keys(item)], [138, 139, 140])
        self.assertTrue(all(k[2] == "grade" for k in keys(item)))
        self.assertIs(item["regularOnly"], True)

    def test_january_switch_to_item_changes_basis(self):
        previous = {"1320": {"jmCd": "1320", "rounds": [grade_round(2027, 1)], "regularOnly": True}}
        schedule = ItemSchedule((item_round(2027, 1),), True, frozenset({2027}))
        item = only(
            merge_items(MergeInputs([ENGINEER], self.grades, item_schedules={"1320": schedule}, previous_items=previous)),
            "1320",
        )
        self.assertIn((2027, 1, "item"), keys(item))
        self.assertNotIn((2027, 1, "grade"), keys(item))
        self.assertEqual(item["rounds"][-1]["written"]["regStart"], "2027-01-13")

    def test_items_not_fetched_today_keep_previous_item_rounds_and_fee(self):
        previous = {
            "1320": {
                "jmCd": "1320", "fee": {"written": 1, "practical": 2}, "regularOnly": True,
                "rounds": [item_round(2026, 1, "2026-01-12"), grade_round(2026, 9)],
            }
        }
        item = only(merge_items(MergeInputs([ENGINEER], self.grades, previous_items=previous)), "1320")
        self.assertEqual(keys(item), [(2026, 1, "item"), (2027, 1, "grade")])
        self.assertEqual(item["fee"], {"written": 1, "practical": 2})
        self.assertIs(item["regularOnly"], True)

    def test_fee_fetched_today_replaces_previous_even_when_unparsed(self):
        previous = {"1320": {"jmCd": "1320", "fee": {"written": 1, "practical": 2}, "rounds": []}}
        merged = merge_items(MergeInputs([ENGINEER], self.grades, fees={"1320": None}, previous_items=previous))
        self.assertIsNone(only(merged, "1320")["fee"])

    def test_always_item_gets_no_regular_grade_rounds(self):
        item = only(merge_items(MergeInputs([COOK], self.grades, always_names=frozenset({"한식조리기능사"}))), "7910")
        self.assertIs(item["regularOnly"], False)
        self.assertEqual(item["rounds"], [])

    def test_regular_only_priority(self):
        always = frozenset({"전기기능사"})
        previous = {"7780": {"jmCd": "7780", "regularOnly": True, "rounds": []}}
        self.assertIs(
            only(merge_items(MergeInputs([ELEC], self.grades, previous_items=previous, always_names=always)), "7780")["regularOnly"],
            True,
        )
        today = {"7780": ItemSchedule((), False, frozenset({2026}))}
        self.assertIs(
            only(merge_items(MergeInputs([ELEC], self.grades, item_schedules=today, previous_items=previous)), "7780")["regularOnly"],
            False,
        )
        self.assertIsNone(only(merge_items(MergeInputs([ELEC], self.grades)), "7780")["regularOnly"])

    def test_non_craftsman_grades_default_to_regular(self):
        merged = merge_items(MergeInputs([ENGINEER, GAS_PE], self.grades))
        self.assertTrue(all(item["regularOnly"] is True for item in merged))

    def test_non_craftsman_default_yields_to_item_data_and_previous_value(self):
        today = {"1320": ItemSchedule((), False, frozenset({2026}))}
        self.assertIs(only(merge_items(MergeInputs([ENGINEER], self.grades, item_schedules=today)), "1320")["regularOnly"], False)
        previous = {"1320": {"jmCd": "1320", "regularOnly": False, "rounds": []}}
        self.assertIs(only(merge_items(MergeInputs([ENGINEER], self.grades, previous_items=previous)), "1320")["regularOnly"], False)
        stale_null = {"1320": {"jmCd": "1320", "regularOnly": None, "rounds": []}}
        self.assertIs(only(merge_items(MergeInputs([ENGINEER], self.grades, previous_items=stale_null)), "1320")["regularOnly"], True)

    def test_craftsman_without_data_stays_unknown(self):
        self.assertIsNone(only(merge_items(MergeInputs([ELEC], self.grades)), "7780")["regularOnly"])

    def test_empty_get_jm_list_answer_keeps_previous_item_rounds(self):
        previous = {"1320": {"jmCd": "1320", "regularOnly": True,
                             "rounds": [item_round(2026, 1, "2026-01-12"), item_round(2026, 2, "2026-04-20")]}}
        today = {"1320": parse_item_schedule([])}
        item = only(merge_items(MergeInputs([ENGINEER], self.grades, item_schedules=today, previous_items=previous)), "1320")
        self.assertEqual(keys(item), [(2026, 1, "item"), (2026, 2, "item"), (2027, 1, "grade")])
        self.assertIs(item["regularOnly"], True)

    def test_empty_get_jm_list_answer_without_previous_item_data_uses_grade_rounds(self):
        today = {"7780": parse_item_schedule([])}
        item = only(merge_items(MergeInputs([ELEC], self.grades, item_schedules=today)), "7780")
        self.assertEqual([k[1] for k in keys(item)], [1, 2, 3, 4])

    def test_output_sorted_and_shaped(self):
        merged = merge_items(MergeInputs([GAS_PE, ENGINEER], self.grades))
        self.assertEqual([i["jmCd"] for i in merged], ["0752", "1320"])
        self.assertEqual(
            sorted(merged[0]), ["fee", "field", "grade", "jmCd", "name", "regularOnly", "rounds", "subField"]
        )

    def test_universe_from_previous(self):
        previous = {"items": [{**ENGINEER, "rounds": [], "fee": None, "regularOnly": None}]}
        self.assertEqual(universe_from_previous(previous), [ENGINEER])
        self.assertEqual(universe_from_previous(None), [])


if __name__ == "__main__":
    unittest.main()
