import unittest
from datetime import datetime, timezone

from tools.certexam.budget import CallBudget, kst_now, plan_run, rotation_targets, should_fetch_jm_list
from tools.certexam.errors import BudgetExceeded
from tools.certexam.tests.support import MONDAY_RUN, SUNDAY_RUN

CODES = [f"{n:04d}" for n in range(513)]


class RotationTest(unittest.TestCase):
    def test_seven_days_cover_every_item_once(self):
        seen = [code for day in range(7) for code in rotation_targets(CODES, day)]
        self.assertEqual(sorted(seen), CODES)
        self.assertEqual(len(seen), len(set(seen)))

    def test_daily_share(self):
        self.assertEqual(len(rotation_targets(CODES, 0)), 74)
        self.assertEqual(len(rotation_targets(CODES, 6)), 73)

    def test_order_independent_of_input_order(self):
        self.assertEqual(rotation_targets(list(reversed(CODES)), 2), rotation_targets(CODES, 2))
        self.assertEqual(rotation_targets(CODES, 2)[:2], ("0002", "0009"))

    def test_kst_weekday(self):
        self.assertEqual(kst_now(SUNDAY_RUN).weekday(), 6)
        self.assertEqual(kst_now(MONDAY_RUN).weekday(), 0)
        with self.assertRaises(ValueError):
            kst_now(datetime(2026, 11, 1, 18, 10))


class PlanTest(unittest.TestCase):
    def test_item_list_only_on_monday_or_full(self):
        self.assertTrue(should_fetch_jm_list(MONDAY_RUN, full=False))
        self.assertFalse(should_fetch_jm_list(SUNDAY_RUN, full=False))
        self.assertTrue(should_fetch_jm_list(SUNDAY_RUN, full=True))

    def test_rotation_plan(self):
        plan = plan_run(CODES, SUNDAY_RUN, full=False)
        self.assertEqual(plan.jm_targets, rotation_targets(CODES, 6))
        self.assertEqual(plan.fee_targets, plan.jm_targets)
        self.assertEqual(plan.kst_year, 2026)

    def test_full_plan_fetches_every_item_but_keeps_fee_rotation(self):
        plan = plan_run(CODES, SUNDAY_RUN, full=True)
        self.assertEqual(len(plan.jm_targets), 513)
        self.assertEqual(plan.fee_targets, rotation_targets(CODES, 6))
        # 513 getJMList + 73 getFeeList + 4 grade lists stay under the 900 guard.
        self.assertLess(len(plan.jm_targets) + len(plan.fee_targets) + 4, 900)

    def test_kst_year_turns_at_kst_midnight(self):
        plan = plan_run(CODES, datetime(2026, 12, 31, 15, 0, tzinfo=timezone.utc), full=False)
        self.assertEqual(plan.kst_year, 2027)

    def test_restrict_targets(self):
        plan = plan_run(CODES, SUNDAY_RUN, full=False, restrict_jm=frozenset({"0001"}), restrict_fee=frozenset())
        self.assertEqual((plan.jm_targets, plan.fee_targets), (("0001",), ()))


class CallBudgetTest(unittest.TestCase):
    def test_stops_before_the_limit_is_passed(self):
        budget = CallBudget(limits={"ntq": 2})
        budget.take("ntq")
        budget.take("ntq")
        with self.assertRaises(BudgetExceeded):
            budget.take("ntq")
        self.assertEqual(budget.used["ntq"], 2)

    def test_unknown_api_has_no_budget(self):
        with self.assertRaises(BudgetExceeded):
            CallBudget(limits={}).take("other")


if __name__ == "__main__":
    unittest.main()
