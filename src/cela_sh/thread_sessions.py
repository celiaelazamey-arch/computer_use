"""Thread-local HTTP sessions behind one stable handle.

``requests.Session`` is documented as not thread-safe, while the planner, the
embedding provider, and the Qdrant adapter are shared by every request
thread. Rather than serializing all requests behind one process-wide lock (the
old ``backend_lock`` in ``server.py``), each thread gets its own pooled
``requests.Session`` and the object below stays the single stable handle that
callers and tests already know: ``trust_env``, ``headers``, ``post``,
``request``, and ``close`` all behave as before.

Threads are long-lived and bounded (the ASGI thread pool), so the number of
underlying sessions is bounded too. :meth:`ThreadLocalSession.close` closes
every live underlying session, which is what the server lifespan calls at
shutdown.
"""

import threading

import requests


class ThreadLocalSession:
    """A per-thread pool of ``requests.Session`` objects behind one handle."""

    def __init__(self, *, trust_env: bool = False) -> None:
        self._local = threading.local()
        self._lock = threading.Lock()
        self._live: dict[int, requests.Session] = {}
        self._trust_env = bool(trust_env)
        self._headers: dict[str, str] = {}
        self._closed = False

    @property
    def trust_env(self) -> bool:
        return self._trust_env

    @trust_env.setter
    def trust_env(self, value: bool) -> None:
        self._trust_env = bool(value)
        # Keep already-created per-thread sessions in sync.
        with self._lock:
            for session in self._live.values():
                session.trust_env = self._trust_env

    @property
    def headers(self) -> dict[str, str]:
        """Base headers applied to every per-thread session at creation."""
        return self._headers

    def _session_for_thread(self) -> requests.Session:
        if self._closed:
            raise RuntimeError("ThreadLocalSession is closed")
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.trust_env = self._trust_env
            if self._headers:
                session.headers.update(self._headers)
            self._local.session = session
            with self._lock:
                self._live[threading.get_ident()] = session
        return session

    def request(self, method: str, url: str, **kwargs: object) -> requests.Response:
        return self._session_for_thread().request(method, url, **kwargs)

    def post(self, url: str, **kwargs: object) -> requests.Response:
        return self._session_for_thread().post(url, **kwargs)

    def get(self, url: str, **kwargs: object) -> requests.Response:
        return self._session_for_thread().get(url, **kwargs)

    def close(self) -> None:
        with self._lock:
            sessions = list(self._live.values())
            self._live.clear()
            self._closed = True
        for session in sessions:
            session.close()
