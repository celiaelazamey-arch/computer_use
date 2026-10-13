"""Offline tests for the per-thread session pool (thread_sessions)."""

import threading
import unittest
from unittest.mock import Mock, patch

from cela_sh.thread_sessions import ThreadLocalSession


class ThreadLocalSessionTests(unittest.TestCase):
    def test_each_thread_gets_its_own_pooled_session(self):
        session = ThreadLocalSession()
        session.headers.update({"x-api-key": "not-a-real-key"})
        self.assertFalse(session.trust_env)

        main_id = id(session._session_for_thread())
        seen = {}

        def other_thread() -> None:
            seen["id"] = id(session._session_for_thread())
            seen["again"] = id(session._session_for_thread())
            inner = session._session_for_thread()
            seen["trust_env"] = inner.trust_env
            seen["header"] = inner.headers.get("x-api-key")

        thread = threading.Thread(target=other_thread)
        thread.start()
        thread.join()

        self.assertNotEqual(seen["id"], main_id, "threads must not share one requests.Session")
        self.assertEqual(seen["again"], seen["id"], "a thread reuses its own session")
        self.assertFalse(seen["trust_env"])
        self.assertEqual(seen["header"], "not-a-real-key")

    def test_close_reaches_every_underlying_session(self):
        session = ThreadLocalSession()
        main_session = session._session_for_thread()
        other_session: dict[str, object] = {}

        def other_thread() -> None:
            other_session["session"] = session._session_for_thread()

        thread = threading.Thread(target=other_thread)
        thread.start()
        thread.join()

        with patch.object(main_session, "close", autospec=True) as main_close, patch.object(
            other_session["session"], "close", autospec=True
        ) as other_close:
            session.close()

        main_close.assert_called_once()
        other_close.assert_called_once()
        with self.assertRaises(RuntimeError):
            session._session_for_thread()

    def test_trust_env_updates_apply_to_live_sessions(self):
        session = ThreadLocalSession()
        live = session._session_for_thread()
        session.trust_env = True
        self.assertTrue(live.trust_env)
        session.trust_env = False
        self.assertFalse(live.trust_env)
        fresh = session._session_for_thread()
        self.assertFalse(fresh.trust_env)

    def test_the_session_handle_is_the_patch_seam_for_tests(self):
        # This is exactly how the HTTP contract tests fake Ollama/Qdrant:
        # patching the handle's post() must shadow the real transport.
        session = ThreadLocalSession()
        mock_response = Mock(status_code=200)
        with patch.object(session, "post", return_value=mock_response) as mocked:
            self.assertIs(session.post("http://127.0.0.1:11434/api/generate"), mock_response)
            mocked.assert_called_once()


if __name__ == "__main__":
    unittest.main()
