import argparse
import json
import sys
from collections.abc import Sequence

from .factory import create_planner
from .planner.client import (
    PlannerConnectionError,
    PlannerError,
    PlannerMemoryError,
    PlannerResponseError,
    PlannerValidationError,
)
from .settings import ConfigurationError, Settings

#: The CLI's stable machine contract: one exit code per error code. Scripts and
#: tests branch on these numbers, so changing one is a breaking change and is
#: caught by the snapshot in ``tests/snapshots/api_surface.json``.
EXIT_CODES: dict[str, int] = {
    "ok": 0,
    "planner_error": 1,
    "cli_usage_error": 2,
    "configuration_error": 2,
    "planner_connection_error": 3,
    "planner_response_error": 4,
    "planner_validation_error": 5,
    "planner_memory_error": 6,
    "interrupted": 130,
}


def write_error(code: str, message: str, usage: str | None = None) -> None:
    error = {"code": code, "message": message}
    if usage:
        error["usage"] = usage
    sys.stderr.write(json.dumps({"error": error}, ensure_ascii=False) + "\n")


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        write_error("cli_usage_error", message, self.format_usage().strip())
        raise SystemExit(EXIT_CODES["cli_usage_error"])


def non_blank(value: str) -> str:
    if not value.strip():
        raise argparse.ArgumentTypeError("must not be blank")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(prog="cela.sh", description="Generate a local, dry-run-only plan with Ollama.")
    parser.add_argument("--goal", required=True, type=non_blank, help="The user's goal")
    parser.add_argument("--context", default="", help="Dummy or manually supplied screen context")
    parser.add_argument("--model", default=None, type=non_blank, help="Override the configured local planner model")
    parser.add_argument("--session-id", default=None, type=non_blank, help="Required when Qdrant memory is enabled")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        settings = Settings.from_env()
        planner = create_planner(settings, model=args.model)
        plan = planner.generate_plan(args.goal, args.context, session_id=args.session_id)
    except (ConfigurationError, ValueError) as exc:
        write_error("configuration_error", str(exc))
        return EXIT_CODES["configuration_error"]
    except PlannerConnectionError as exc:
        write_error("planner_connection_error", str(exc))
        return EXIT_CODES["planner_connection_error"]
    except PlannerResponseError as exc:
        write_error("planner_response_error", str(exc))
        return EXIT_CODES["planner_response_error"]
    except PlannerValidationError as exc:
        write_error("planner_validation_error", str(exc))
        return EXIT_CODES["planner_validation_error"]
    except PlannerMemoryError as exc:
        write_error("planner_memory_error", str(exc))
        return EXIT_CODES["planner_memory_error"]
    except PlannerError as exc:
        write_error("planner_error", str(exc))
        return EXIT_CODES["planner_error"]
    except KeyboardInterrupt:
        write_error("interrupted", "Planning was interrupted.")
        return EXIT_CODES["interrupted"]

    sys.stdout.write(plan.model_dump_json(indent=2) + "\n")
    return EXIT_CODES["ok"]
