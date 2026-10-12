"""Shared Ollama URL policy for the planner and embedding provider."""

import ipaddress
from urllib.parse import urlsplit


def validate_ollama_url(base_url: str, *, allow_container_host: bool = False) -> None:
    """Keep loopback as the default; optionally trust exactly Docker's 'ollama'.

    This is not permission to contact arbitrary LAN, public, or container hosts.
    Only enable the exception on a trusted Docker network. Redirects and proxy
    environment variables remain disabled separately in both HTTP clients.
    """
    if not isinstance(allow_container_host, bool):
        raise ValueError("allow_container_host must be a boolean")
    if not isinstance(base_url, str) or not base_url or any(ord(char) <= 32 for char in base_url):
        raise ValueError("Ollama must use a local HTTP base URL")
    try:
        parsed = urlsplit(base_url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Ollama must use a valid local HTTP base URL") from exc
    if (
        parsed.scheme != "http"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or (port is not None and port < 1)
    ):
        raise ValueError("Ollama must use a local HTTP base URL without credentials")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("Ollama base URL must not contain a path, query, or fragment")
    try:
        local_host = parsed.hostname == "localhost" or ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        local_host = False
    if not local_host and not (allow_container_host and parsed.hostname == "ollama"):
        raise ValueError("Ollama is restricted to loopback hosts (or the explicitly enabled 'ollama' service)")
