"""Offline guards for container configuration; no Docker daemon is needed."""

import json
import os
import re
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from cela_sh import Settings

ROOT = Path(__file__).resolve().parents[1]


def requirements(path):
    lines = path.read_text().splitlines()
    result = set()
    for line in lines:
        line = line.strip()
        if line.startswith("-r "):
            result.update(requirements(path.parent / line[3:]))
        elif line and not line.startswith("#"):
            result.add(line)
    return result


class ContainerConfigTests(unittest.TestCase):
    def setUp(self):
        self.compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
        self.dockerfile = (ROOT / "Dockerfile").read_text()

    def test_requirement_files_match_installable_package_metadata(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
        base = set(project["dependencies"])
        self.assertEqual(base, requirements(ROOT / "requirements.txt"))
        server = base | set(project["optional-dependencies"]["server"])
        self.assertEqual(server, requirements(ROOT / "requirements-server.txt"))
        self.assertEqual(base | set(project["optional-dependencies"]["dev"]), requirements(ROOT / "requirements-dev.txt"))
        self.assertIn("sqlite-vec==0.1.9", base)

    def test_image_is_multistage_installed_nonroot_and_healthchecked(self):
        self.assertEqual(len(re.findall(r"^FROM ", self.dockerfile, flags=re.MULTILINE)), 2)
        self.assertIn(" AS builder", self.dockerfile)
        self.assertIn(" AS runtime", self.dockerfile)
        self.assertIn("pip wheel", self.dockerfile)
        self.assertIn("--prefix=/install", self.dockerfile)
        self.assertIn("COPY --from=builder /install /usr/local", self.dockerfile)
        self.assertIn("USER 10001:10001", self.dockerfile)
        self.assertIn("HEALTHCHECK", self.dockerfile)
        self.assertNotIn("COPY . .", self.dockerfile)
        command = json.loads(next(line[4:] for line in self.dockerfile.splitlines() if line.startswith("CMD ")))
        self.assertEqual(command[:2], ["uvicorn", "cela_sh.server:app"])
        self.assertEqual(command[command.index("--host") + 1], "0.0.0.0")
        self.assertTrue((ROOT / "src/cela_sh/server.py").is_file())

    def test_build_context_is_whitelisted_not_a_copy_of_secrets_or_worker(self):
        rules = (ROOT / ".dockerignore").read_text().splitlines()
        self.assertIn("*", rules)
        allowed = {rule for rule in rules if rule.startswith("!")}
        self.assertEqual(allowed, {"!Dockerfile", "!.dockerignore", "!pyproject.toml", "!README.md", "!LICENSE", "!src/", "!src/**"})
        self.assertNotIn("!.env", allowed)
        self.assertNotIn("!.git/", allowed)
        self.assertNotIn("!ccelia2027/", allowed)

    def test_all_published_service_ports_stay_on_host_loopback(self):
        for name, service in self.compose["services"].items():
            for port in service.get("ports", []):
                with self.subTest(service=name, port=port):
                    self.assertTrue(port.startswith("127.0.0.1:"))

    def test_api_has_persistent_storage_and_waits_for_healthy_ollama(self):
        api = self.compose["services"]["cela-sh-api"]
        self.assertEqual(api["profiles"], ["api"])
        self.assertEqual(api["build"]["target"], "runtime")
        self.assertIn("cela_data:/app/data", api["volumes"])
        self.assertIn("cela_data", self.compose["volumes"])
        self.assertEqual(api["environment"]["CELA_MEMORY_BACKEND"], "sqlite-vec")
        self.assertEqual(api["environment"]["CELA_SQLITE_VEC_PATH"], "/app/data/cela_memory.db")
        self.assertEqual(api["depends_on"]["ollama"]["condition"], "service_healthy")
        self.assertEqual(self.compose["services"]["ollama"]["healthcheck"]["test"], ["CMD", "ollama", "list"])
        self.assertNotIn(":latest", self.compose["services"]["ollama"]["image"])

    def test_compose_uses_the_actual_cela_environment_names(self):
        env = self.compose["services"]["cela-sh-api"]["environment"]
        self.assertTrue(all(key.startswith("CELA_") for key in env))
        resolved = {key: re.sub(r"\$\{[A-Z_]+:-([^}]+)\}", r"\1", value) for key, value in env.items()}
        with patch.dict(os.environ, resolved, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.ollama_url, "http://ollama:11434")
        self.assertTrue(settings.allow_ollama_container_host)
        self.assertEqual(settings.memory_backend, "sqlite-vec")
        self.assertEqual(settings.sqlite_vec_path, "/app/data/cela_memory.db")

    def test_api_hardening_and_original_optional_qdrant_are_preserved(self):
        api = self.compose["services"]["cela-sh-api"]
        self.assertTrue(api["read_only"])
        self.assertIn("ALL", api["cap_drop"])
        self.assertIn("no-new-privileges:true", api["security_opt"])
        self.assertTrue(api["init"])
        self.assertEqual(self.compose["services"]["qdrant"]["profiles"], ["memory"])
        self.assertIn("qdrant_data", self.compose["volumes"])
        self.assertIn("ollama_data", self.compose["volumes"])

    def test_shell_service_commands_use_profiles_and_never_delete_volumes(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "docker"
            log = Path(directory) / "calls.log"
            binary.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$DOCKER_TEST_LOG"\n')
            binary.chmod(0o755)
            env = {**os.environ, "PATH": directory + os.pathsep + os.environ.get("PATH", ""), "DOCKER_TEST_LOG": str(log)}
            for command in ("services-up-api", "services-stop"):
                result = subprocess.run(["bash", str(ROOT / "cela.sh"), command], cwd=ROOT, env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(log.read_text().splitlines(), [
                "compose --profile api up -d --build cela-sh-api",
                "compose --profile api --profile memory stop",
            ])
            self.assertNotIn("down -v", log.read_text())


if __name__ == "__main__":
    unittest.main()
