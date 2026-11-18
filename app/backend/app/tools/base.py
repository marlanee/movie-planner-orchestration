import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from time import sleep
from typing import Any, Literal

import requests

BAD_ARGUMENT = "BAD_ARGUMENT"
INVALID_KEY = "INVALID_KEY"
NOT_FOUND = "NOT_FOUND"
NO_DATA = "NO_DATA"
UPSTREAM_ERROR = "UPSTREAM_ERROR"
NO_RUNTIME = "NO_RUNTIME"
NO_RESULT = "NO_RESULT"

ToolKind = Literal["external", "local"]
ToolResult = dict[str, Any]


def ok(data: Any) -> ToolResult:
    return {"ok": True, "data": data}


def fail(code: str, message: str) -> ToolResult:
    return {"ok": False, "error": {"code": code, "message": message}}


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    func: Callable[..., ToolResult]
    kind: ToolKind = "external"
    source: str = ""
    output: dict = field(default_factory=dict)

    def run(self, /, **kwargs: Any) -> ToolResult:
        try:
            missing = set(self.parameters.get("required", ())) - kwargs.keys()
            unknown = kwargs.keys() - self.parameters.get("properties", {}).keys()
            if missing or unknown:
                return fail(BAD_ARGUMENT, "필수 입력이 빠졌거나 알 수 없는 입력이 있습니다.")
            signature = inspect.signature(self.func)
            try:
                signature.bind(**kwargs)
            except TypeError:
                return fail(BAD_ARGUMENT, "입력값이 규약과 다릅니다.")
            return self.func(**kwargs)
        except Exception:
            return fail(UPSTREAM_ERROR, "Tool 실행 중 오류입니다.")


class UpstreamError(Exception):
    """외부 API 요청 또는 JSON 해석 실패."""


def http_get_json(url, *, params=None, headers=None, timeout=5) -> tuple[int, Any]:
    try:
        try:
            response = requests.get(url, params=params, headers=headers, timeout=timeout)
        except (requests.Timeout, requests.ConnectionError):
            sleep(0.3)
            response = requests.get(url, params=params, headers=headers, timeout=timeout)
        return response.status_code, response.json()
    except (requests.RequestException, ValueError):
        raise UpstreamError("외부 API 응답을 읽지 못했습니다.") from None


def as_object(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def object_list(value: Any) -> list[dict]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def as_text(value: Any) -> str | None:
    return (value.strip() or None) if isinstance(value, str) else None


def positive_int(value: Any) -> int | None:
    if type(value) is not int and not isinstance(value, str):
        return None
    try:
        number = int(value)
    except ValueError:
        return None
    return number if number > 0 else None
