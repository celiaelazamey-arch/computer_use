import unittest

from cela_sh.planner.models import Action, PlannerInput, PlanStatus, RawPlan, RiskLevel
from cela_sh.planner.policy import apply_policy


def _plan(goal: str, steps: list[tuple[Action, str, str]]):
    raw = RawPlan.model_validate(
        {
            "analysis": "test plan",
            "steps": [
                {"action": action, "target": target, "value": value, "reason": "test reason"}
                for action, target, value in steps
            ],
        }
    )
    return apply_policy(raw, PlannerInput(goal=goal))


class ConsequentialActionPolicyTests(unittest.TestCase):
    """Sending, submitting, or publishing must never be classified as plain ``ready``.

    The planner is dry-run only, but a ``ready`` status reads as a safe signal to any
    consumer. These cases failed before the keyword fix (see docs/threat-model-cela.md, F1).
    """

    def assert_needs_confirmation(self, goal, steps):
        plan = _plan(goal, steps)
        self.assertEqual(plan.status, PlanStatus.REQUIRES_CONFIRMATION, goal)
        self.assertTrue(plan.requires_confirmation)
        self.assertEqual(plan.steps[0].risk, RiskLevel.REQUIRES_CONFIRMATION)
        self.assertTrue(plan.steps[0].requires_confirmation)

    def test_send_email_is_not_ready(self):
        self.assert_needs_confirmation(
            "send an email to my boss saying hi",
            [(Action.CLICK, "Send button", ""), (Action.STOP, "", "")],
        )

    def test_submit_form_is_not_ready(self):
        self.assert_needs_confirmation(
            "submit the form",
            [(Action.CLICK, "Submit", ""), (Action.STOP, "", "")],
        )

    def test_arabic_send_message_is_not_ready(self):
        self.assert_needs_confirmation(
            "ارسل رسالة لمديري",
            [(Action.CLICK, "زر الإرسال", ""), (Action.STOP, "", "")],
        )

    def test_arabic_send_with_hamza_is_not_ready(self):
        self.assert_needs_confirmation(
            "أرسل الملف للفريق",
            [(Action.CLICK, "زر الإرسال", ""), (Action.STOP, "", "")],
        )

    def test_post_is_not_ready(self):
        self.assert_needs_confirmation(
            "post a tweet",
            [(Action.TYPE, "compose box", "hello"), (Action.STOP, "", "")],
        )

    def test_message_verb_is_not_ready(self):
        self.assert_needs_confirmation(
            "message the support team",
            [(Action.CLICK, "Message button", ""), (Action.STOP, "", "")],
        )

    def test_order_and_checkout_are_not_ready(self):
        self.assert_needs_confirmation(
            "place an order",
            [(Action.CLICK, "Checkout", ""), (Action.STOP, "", "")],
        )

    def test_subscribe_is_not_ready(self):
        self.assert_needs_confirmation(
            "subscribe to the newsletter",
            [(Action.CLICK, "Subscribe", ""), (Action.STOP, "", "")],
        )

    def test_sensitive_step_is_classified_even_when_goal_is_benign(self):
        # The goal has no keyword, so only the per-step check can catch "Submit".
        plan = _plan(
            "open the page and read it",
            [
                (Action.NAVIGATE, "browser address bar", "https://example.com"),
                (Action.CLICK, "Submit", ""),
                (Action.STOP, "", ""),
            ],
        )
        self.assertEqual(plan.status, PlanStatus.REQUIRES_CONFIRMATION)
        self.assertEqual(plan.steps[0].risk, RiskLevel.LOW)
        self.assertFalse(plan.steps[0].requires_confirmation)
        self.assertTrue(plan.steps[1].requires_confirmation)


class BenignPolicyRegressionTests(unittest.TestCase):
    def test_plain_search_stays_ready(self):
        plan = _plan(
            "search for agent architectures",
            [(Action.SEARCH_WEB, "browser search field", "agent architectures"), (Action.STOP, "", "")],
        )
        self.assertEqual(plan.status, PlanStatus.READY)
        self.assertFalse(plan.requires_confirmation)
        self.assertEqual(plan.steps[0].risk, RiskLevel.LOW)

    def test_plain_navigation_stays_ready(self):
        plan = _plan(
            "open the start page",
            [(Action.NAVIGATE, "browser address bar", "https://example.com"), (Action.OBSERVE, "", "")],
        )
        self.assertEqual(plan.status, PlanStatus.READY)


class ShellBlockingStillWorksTests(unittest.TestCase):
    def test_powershell_open_is_blocked(self):
        plan = _plan("open terminal", [(Action.OPEN_APP, "PowerShell", ""), (Action.STOP, "", "")])
        self.assertEqual(plan.status, PlanStatus.BLOCKED)

    def test_rm_rf_type_is_blocked(self):
        plan = _plan("type command", [(Action.TYPE, "terminal", "rm -rf /"), (Action.STOP, "", "")])
        self.assertEqual(plan.status, PlanStatus.BLOCKED)


if __name__ == "__main__":
    unittest.main()
