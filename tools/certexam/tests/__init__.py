"""Collector tests. Real network access is blocked for the whole package:
creating a socket or resolving a host fails, so a test that tries to call
an API fails instead of reaching the network."""

import http.client  # noqa: F401  (import before patching: ssl subclasses socket)
import socket
import ssl  # noqa: F401


class NetworkBlocked(RuntimeError):
    pass


def _blocked(*args, **kwargs):
    raise NetworkBlocked("network access is blocked in certexam tests")


class _BlockedSocket(socket.socket):
    def __init__(self, *args, **kwargs):
        _blocked()


socket.socket = _BlockedSocket  # type: ignore[misc]
socket.create_connection = _blocked  # type: ignore[assignment]
socket.getaddrinfo = _blocked  # type: ignore[assignment]
