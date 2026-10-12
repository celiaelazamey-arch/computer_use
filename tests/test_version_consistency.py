"""Offline guard: the version string must not drift between its six locations.

`CHANGELOG.md` promises a single current version. Nothing else enforced it, and
the inventory it listed was incomplete: `src/cela_sh/server.py` passes `version=`
to FastAPI, which is published to callers through `/openapi.json` and `/docs`,
and `SECURITY.md` names the supported version. Bumping only the four originally
documented files therefore shipped a public OpenAPI contract advertising a stale
version while the package metadata claimed a new one.

This test reads all six, plus the newest `CHANGELOG.md` heading, and fails on any
difference. It needs no network, no Docker, and no Ollama.
"""

import json
import re
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SEMVER = r"(\d+\.\d+\.\d+)"


def _search(pattern: str, text: str, label: str) -> str:
    match = re.search(pattern, text, re.MULTILINE)
    if match is None:
        raise AssertionError(f"could not find a version in {label}; pattern was {pattern!r}")
    return match.group(1)


class VersionConsistencyTests(unittest.TestCase):
    def setUp(self):
        with (ROOT / "pyproject.toml").open("rb") as handle:
            self.expected = tomllib.load(handle)["project"]["version"]

    def assertVersion(self, actual: str, label: str) -> None:
        self.assertEqual(
            actual,
            self.expected,
            f"{label} says {actual} but pyproject.toml says {self.expected}",
        )

    def test_package_json_matches(self):
        data = json.loads((ROOT / "ccelia2027" / "package.json").read_text(encoding="utf-8"))
        self.assertVersion(data["version"], "ccelia2027/package.json")

    def test_documentation_headers_match(self):
        for name in ("docs/architecture.md", "docs/protocol.md"):
            with self.subTest(document=name):
                text = (ROOT / name).read_text(encoding="utf-8")
                self.assertVersion(_search(rf"\*\*الإصدار:\*\*\s*{SEMVER}", text, name), name)

    def test_security_supported_versions_matches(self):
        text = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
        found = _search(rf"^\|\s*{SEMVER}\s*\(`main`\)\s*\|", text, "SECURITY.md")
        self.assertVersion(found, "SECURITY.md supported-versions table")

    def test_fastapi_app_version_matches_in_source(self):
        text = (ROOT / "src" / "cela_sh" / "server.py").read_text(encoding="utf-8")
        found = _search(rf'^\s*version="{SEMVER}",\s*$', text, "src/cela_sh/server.py")
        self.assertVersion(found, "src/cela_sh/server.py FastAPI version=")

    def test_changelogs_newest_heading_matches(self):
        text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        found = _search(rf"^## {SEMVER}\s*$", text, "CHANGELOG.md")
        self.assertVersion(found, "newest CHANGELOG.md version heading")

    def test_the_version_reaches_callers_through_openapi(self):
        """The source literal is not enough: assert what a client actually reads.

        Deliberately not a ban on an `## Unreleased` heading: the next cycle has
        to be able to open one. `test_changelogs_newest_heading_matches` searches
        for the first *semver* heading, so an Unreleased section above the
        released one does not hide a mismatch.
        """
        from cela_sh.server import create_app

        self.assertEqual(create_app().version, self.expected)


if __name__ == "__main__":
    unittest.main()
