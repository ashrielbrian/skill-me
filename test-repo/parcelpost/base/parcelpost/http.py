import logging
import re
from dataclasses import dataclass, field
from typing import Callable

from .errors import AppError

log = logging.getLogger(__name__)


@dataclass
class Request:
    method: str
    path: str
    body: dict = field(default_factory=dict)
    query: dict = field(default_factory=dict)
    headers: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)


@dataclass
class Response:
    status: int
    body: dict


_routes: list[tuple[str, re.Pattern, Callable[[Request], Response]]] = []


def route(method: str, pattern: str):
    regex = re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", pattern) + "$")

    def register(fn):
        _routes.append((method, regex, fn))
        return fn

    return register


def json_response(body: dict, status: int = 200) -> Response:
    return Response(status=status, body=body)


def dispatch(request: Request) -> Response:
    for method, regex, fn in _routes:
        match = regex.match(request.path)
        if method != request.method or match is None:
            continue
        request.params = match.groupdict()
        try:
            return fn(request)
        except AppError as exc:
            return json_response(exc.to_body(), exc.status)
        except Exception:
            # The message of an unexpected exception can carry SQL or upstream
            # payloads, so the client gets a fixed string and the log gets the rest.
            log.exception("unhandled error in %s %s", request.method, request.path)
            return json_response({"error": {"code": "internal", "message": "internal error"}}, 500)
    return json_response({"error": {"code": "not_found", "message": "no such route"}}, 404)
