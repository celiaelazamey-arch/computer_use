"""Opt-in end-to-end smoke test for real local Ollama plan generation."""

import sys

from cela_sh.planner.client import OllamaPlanner, PlannerError
from cela_sh.settings import ConfigurationError, Settings


PROBE_GOAL = "ابحث عن مقال عربي موجز يشرح نهج البرمجيات المحلية أولاً."


def main() -> int:
    try:
        settings = Settings.from_env()
        planner = OllamaPlanner(
            model=settings.planner_model,
            timeout=(settings.connect_timeout, settings.read_timeout),
            base_url=settings.ollama_url,
        )
        plan = planner.generate_plan(PROBE_GOAL)
    except (ConfigurationError, PlannerError, ValueError) as exc:
        print(f"FAIL local Ollama plan probe: {exc}", file=sys.stderr)
        return 1

    if not plan.dry_run or plan.execution_enabled or not plan.steps:
        print("FAIL local Ollama returned a plan outside the planning-only contract.", file=sys.stderr)
        return 1

    print("PASS local Ollama generated a schema-valid, planning-only plan", file=sys.stderr)
    sys.stdout.write(plan.model_dump_json(indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
