import os
import unittest
from unittest.mock import patch

from cela_sh.factory import create_planner
from cela_sh.settings import ConfigurationError, Settings


class SettingsTests(unittest.TestCase):
    def test_defaults_are_local_and_memory_is_disabled(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env()

        self.assertEqual(settings.ollama_url, "http://127.0.0.1:11434")
        self.assertEqual(settings.planner_model, "qwen2.5:3b")
        self.assertEqual(settings.memory_backend, "disabled")
        self.assertIsNone(settings.qdrant_api_key)

    def test_factory_does_not_connect_or_create_memory_when_disabled(self):
        settings = Settings()
        with (
            patch("cela_sh.factory.OllamaEmbeddingProvider") as embeddings,
            patch("cela_sh.factory.QdrantMemoryStore") as memory,
            patch("cela_sh.factory.OllamaPlanner") as planner,
        ):
            create_planner(settings)

        embeddings.assert_not_called()
        memory.assert_not_called()
        self.assertIsNone(planner.call_args.kwargs["memory_store"])

    def test_environment_overrides_runtime_adapters_and_timeouts(self):
        env = {
            "CELA_OLLAMA_URL": "http://localhost:11434/",
            "CELA_PLANNER_MODEL": "qwen2.5:7b",
            "CELA_EMBEDDING_MODEL": "nomic-embed-text",
            "CELA_MEMORY_BACKEND": "QDRANT",
            "CELA_MEMORY_LIMIT": "5",
            "CELA_QDRANT_URL": "https://memory.example:6333",
            "CELA_QDRANT_COLLECTION": "sessions",
            "CELA_QDRANT_API_KEY": "local-secret",
            "CELA_CONNECT_TIMEOUT": "2.5",
            "CELA_READ_TIMEOUT": "45",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env()

        self.assertEqual(settings.memory_backend, "qdrant")
        self.assertEqual(settings.memory_limit, 5)
        self.assertEqual(settings.qdrant_api_key, "local-secret")
        self.assertEqual(settings.connect_timeout, 2.5)
        self.assertEqual(settings.read_timeout, 45)

    def test_rejects_invalid_backend_and_nonpositive_timeouts(self):
        for env in (
            {"CELA_MEMORY_BACKEND": "redis"},
            {"CELA_CONNECT_TIMEOUT": "0"},
            {"CELA_READ_TIMEOUT": "inf"},
            {"CELA_MEMORY_LIMIT": "-1"},
            {"CELA_OLLAMA_URL": "   "},
            {"CELA_MEMORY_BACKEND": "qdrant", "CELA_QDRANT_COLLECTION": " "},
        ):
            with self.subTest(env=env), patch.dict(os.environ, env, clear=True):
                with self.assertRaises(ConfigurationError):
                    Settings.from_env()

    def test_factory_injects_optional_qdrant_and_embedding_adapters(self):
        settings = Settings(
            memory_backend="qdrant",
            qdrant_url="http://127.0.0.1:7333",
            qdrant_collection="cela_test",
            memory_limit=4,
        )
        with (
            patch("cela_sh.factory.OllamaEmbeddingProvider") as embeddings,
            patch("cela_sh.factory.QdrantMemoryStore") as memory,
            patch("cela_sh.factory.OllamaPlanner") as planner,
        ):
            created = create_planner(settings)

        self.assertIs(created, planner.return_value)
        embeddings.assert_called_once_with(
            model="nomic-embed-text",
            base_url="http://127.0.0.1:11434",
            timeout=(3.05, 60.0),
        )
        memory.assert_called_once_with(
            collection_name="cela_test",
            qdrant_url="http://127.0.0.1:7333",
            embedding_provider=embeddings.return_value,
            timeout=(3.05, 60.0),
            api_key=None,
        )
        planner.assert_called_once_with(
            model="qwen2.5:3b",
            timeout=(3.05, 60.0),
            memory_store=memory.return_value,
            memory_limit=4,
            base_url="http://127.0.0.1:11434",
        )


if __name__ == "__main__":
    unittest.main()
