import re

from .models import Action, Plan, PlanStatus, PlannerInput, RawPlan, RiskLevel


# Keyword classifier, not a complete one (see docs/threat-model-cela.md, F1).
# Consequential verbs (send, submit, post, publish, upload, email, reply, share,
# message, order, checkout, subscribe) are matched with their common inflections,
# so "send", "sending", "sent", "submitted", "published" all require confirmation.
# Arabic terms have no word boundary: a substring match only over-triggers, which is
# the safe direction for a dry-run classifier.
SENSITIVE_TERMS = re.compile(
    r"\b(delete|remove|erase|format|wipe|purchase|buy|pay|transfer|password|credential|"
    r"secret|api key|access token|send money|install|uninstall|shutdown|restart|"
    r"(?:send|submit|post|publish|upload|email|e-mail|reply|share|message|order|subscribe)"
    r"(?:s|es|ed|ing)?|replies|replying|replied|sharing|checkout|subscription)\b|"
    r"(حذف|احذف|امسح|مسح|تهيئة|فرمت|شراء|اشتر|ادفع|دفع|تحويل|كلمة المرور|كلمة السر|"
    r"بيانات الدخول|مفتاح api|رمز وصول|إيقاف التشغيل|إعادة التشغيل|"
    r"أرسل|ارسل|إرسال|ارسال|قدّم|قدم|انشر|ارفع|شارك|راسل|مراسلة|اشترك|اشتراك)",
    re.IGNORECASE,
)
SHELL_APP_TERMS = re.compile(r"\b(powershell|cmd\.exe|command prompt|terminal|bash|zsh|shell)\b", re.IGNORECASE)
SHELL_COMMAND_TERMS = re.compile(
    r"\b(sudo\s+|rm\s+-rf\b|del\s+/[sqf]\b|format\s+[a-z]:|powershell\s+-|cmd\s+/c\b)",
    re.IGNORECASE,
)


def apply_policy(raw_plan: RawPlan, request: PlannerInput) -> Plan:
    planned_steps = []
    for step in raw_plan.steps:
        step_data = step.model_dump()
        if step.action is Action.SEARCH_WEB:
            step_data["target"] = "browser search field"
            step_data["value"] = step.value.strip() or step.target.strip()
        step_text = " ".join((step_data["target"], step_data["value"], step.reason))
        blocked = (
            (step.action is Action.OPEN_APP and SHELL_APP_TERMS.search(step_data["target"]) is not None)
            or (step.action is Action.TYPE and SHELL_COMMAND_TERMS.search(step_data["value"]) is not None)
        )
        needs_confirmation = bool(SENSITIVE_TERMS.search(request.goal) or SENSITIVE_TERMS.search(step_text))

        if blocked:
            risk = RiskLevel.BLOCKED
            reasons = ["This step targets a shell or contains an OS command, which this planner forbids."]
        elif needs_confirmation:
            risk = RiskLevel.REQUIRES_CONFIRMATION
            reasons = ["A deterministic policy matched a sensitive operation or data category."]
        else:
            risk = RiskLevel.LOW
            reasons = []

        planned_steps.append(
            {
                **step_data,
                "risk": risk,
                "requires_confirmation": needs_confirmation or blocked,
                "policy_reasons": reasons,
            }
        )

    if any(step["risk"] is RiskLevel.BLOCKED for step in planned_steps):
        status = PlanStatus.BLOCKED
    elif any(step["requires_confirmation"] for step in planned_steps):
        status = PlanStatus.REQUIRES_CONFIRMATION
    else:
        status = PlanStatus.READY

    return Plan(
        analysis=raw_plan.analysis,
        steps=planned_steps,
        status=status,
        requires_confirmation=status is not PlanStatus.READY,
    )
