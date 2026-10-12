"""The container exception must never turn into a general remote Ollama switch."""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from cela_sh import ConfigurationError, OllamaEmbeddingProvider, OllamaPlanner, Settings, build_memory_store, create_planner
from cela_sh.memory import MemoryStoreError
from cela_sh.planner.client import PlannerResponseError


class OllamaURLTests(unittest.TestCase):
    def test_docker_service_is_rejected_by_default_by_both_clients(self):
        for client in (OllamaPlanner, OllamaEmbeddingProvider):
            with self.subTest(client=client.__name__), self.assertRaises(ValueError):
                client(base_url="http://ollama:11434")

    def test_explicit_opt_in_accepts_exactly_the_ollama_service(self):
        for client in (OllamaPlanner, OllamaEmbeddingProvider):
            with self.subTest(client=client.__name__):
                instance = client(base_url="http://ollama:11434", allow_container_host=True)
                self.addCleanup(instance._session.close)
                self.assertTrue(instance.url.startswith("http://ollama:11434/api/"))
                self.assertFalse(instance._session.trust_env)

    def test_opt_in_does_not_allow_public_private_or_lookalike_hosts(self):
        urls = (
            "http://example.com:11434", "http://10.0.0.5:11434", "http://192.168.1.8:11434",
            "http://169.254.169.254", "http://0.0.0.0:11434", "http://host.docker.internal:11434",
            "http://ollama.example:11434", "http://ollama.:11434", "http://localhost.example:11434",
            "https://ollama:11434", "http://user:pass@ollama:11434", "http://@ollama:11434",
            "http://ollama:11434/path", "http://ollama:11434?target=remote", "http://ollama:11434#fragment",
            "http://ollama:99999", "http://ollama:0", "http://ollama:bad", "http://olla\nma:11434",
        )
        for client in (OllamaPlanner, OllamaEmbeddingProvider):
            for url in urls:
                with self.subTest(client=client.__name__, url=url), self.assertRaises(ValueError):
                    client(base_url=url, allow_container_host=True)

    def test_original_loopback_and_positional_calls_still_work(self):
        planner = OllamaPlanner("qwen2.5:3b", (3.05, 60.0), None, 3, "http://localhost:11434/")
        provider = OllamaEmbeddingProvider("nomic-embed-text", "http://[::1]:11434", (3.05, 60.0))
        self.addCleanup(planner._session.close)
        self.addCleanup(provider._session.close)
        self.assertEqual(planner.url, "http://localhost:11434/api/generate")
        self.assertEqual(provider.url, "http://[::1]:11434/api/embed")

    def test_container_clients_still_reject_redirects(self):
        planner = OllamaPlanner(base_url="http://ollama:11434", allow_container_host=True)
        provider = OllamaEmbeddingProvider(base_url="http://ollama:11434", allow_container_host=True)
        self.addCleanup(planner._session.close)
        self.addCleanup(provider._session.close)
        with patch.object(planner._session, "post", return_value=Mock(status_code=302)) as post:
            with self.assertRaises(PlannerResponseError):
                planner.generate_plan("Read a page")
            self.assertFalse(post.call_args.kwargs["allow_redirects"])
        with patch.object(provider._session, "post", return_value=Mock(status_code=302)) as post:
            with self.assertRaises(MemoryStoreError):
                provider.embed("memory")
            self.assertFalse(post.call_args.kwargs["allow_redirects"])

    def test_non_boolean_client_opt_in_is_rejected(self):
        for client in (OllamaPlanner, OllamaEmbeddingProvider):
            with self.subTest(client=client.__name__), self.assertRaises(ValueError):
                client(allow_container_host="false")


class OllamaContainerSettingsTests(unittest.TestCase):
    def test_flag_defaults_to_false(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(Settings.from_env().allow_ollama_container_host)

    def test_environment_boolean_parsing_is_explicit(self):
        for raw, expected in (("true", True), ("1", True), (" TRUE ", True), ("false", False), ("0", False)):
            with self.subTest(raw=raw), patch.dict(os.environ, {"CELA_ALLOW_OLLAMA_CONTAINER_HOST": raw}, clear=True):
                self.assertEqual(Settings.from_env().allow_ollama_container_host, expected)
        for raw in ("", "maybe", "null"):
            with self.subTest(raw=raw), patch.dict(os.environ, {"CELA_ALLOW_OLLAMA_CONTAINER_HOST": raw}, clear=True):
                with self.assertRaisesRegex(ConfigurationError, "CELA_ALLOW_OLLAMA_CONTAINER_HOST"):
                    Settings.from_env()
        with self.assertRaises(ConfigurationError):
            Settings(allow_ollama_container_host="true")

    def test_factory_passes_opt_in_to_planner_and_embedding_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(
                ollama_url="http://ollama:11434", allow_ollama_container_host=True,
                memory_backend="sqlite-vec", sqlite_vec_path=str(Path(directory) / "memory.db"),
            )
            planner = create_planner(settings)
            self.addCleanup(planner._session.close)
            self.addCleanup(planner.memory_store.embedding_provider._session.close)
            self.assertEqual(planner.url, "http://ollama:11434/api/generate")
            self.assertEqual(planner.memory_store.embedding_provider.url, "http://ollama:11434/api/embed")

    def test_builtin_builder_accepts_structural_settings_without_the_new_field(self):
        config = SimpleNamespace(
            embedding_model="nomic-embed-text", memory_limit=3, ollama_url="http://127.0.0.1:11434",
            qdrant_api_key=None, qdrant_collection="compatibility", qdrant_url="http://127.0.0.1:6333",
        )
        store = build_memory_store("qdrant", config, (3.05, 60.0))
        self.addCleanup(store._session.close)
        self.addCleanup(store.embedding_provider._session.close)
        self.assertEqual(store.embedding_provider.url, "http://127.0.0.1:11434/api/embed")

    def test_factory_with_false_flag_rejects_the_container_url(self):
        with self.assertRaises(ValueError):
            create_planner(Settings(ollama_url="http://ollama:11434"))


if __name__ == "__main__":
    unittest.main()
