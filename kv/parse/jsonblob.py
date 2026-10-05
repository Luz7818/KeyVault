"""JSON 片段。

两类完全不同的处理，别搞混：

* **GCP 服务账号**（`"type":"service_account"` + `private_key`）→ **一条**记录，
  整个 JSON 一个 blob，kind='json'。绝不能拆成 private_key / client_email 多条 ——
  那会把一个凭据碎片化成能各自轮换、彼此漂移的行。非机密子集（project_id /
  client_email / token_uri）提到 extra 里，于是不解密也能搜索和显示。

* **含 base_url 的配置对象** → 主机名**传播给兄弟候选**。这就是
  `{"base_url":"https://api.deepseek.com/v1","api_key":"sk-..."}` 里的裸 sk-
  变成 deepseek + exact 的机制。

平台判定仍然不在这里发生 —— 本包只把主机名塞进 Candidate.hostnames。
"""

from __future__ import annotations

import json
import re
from typing import Iterator

from kv.model import Candidate, Rejection
from kv.parse.candidate import emit, hosts_in
from kv.parse.placeholders import Context

SERVICE_ACCOUNT_EXTRA = ("type", "project_id", "client_email", "token_uri",
                         "private_key_id", "client_id")

CREDENTIAL_KEYS = re.compile(
    r"(?i)^(?:api[_-]?key|apikey|access[_-]?token|auth[_-]?token|secret[_-]?key|"
    r"client[_-]?secret|app[_-]?secret|private[_-]?key|password|passwd|token|secret|"
    r"refresh[_-]?token|id[_-]?token|session[_-]?token|encryption[_-]?key|"
    r"[a-z0-9_]*(?:_api_key|_apikey|_token|_secret|_password))$"
)

NON_CREDENTIAL_KEYS = frozenset({
    "type", "project_id", "client_email", "token_uri", "private_key_id", "client_id",
    "auth_uri", "auth_provider_x509_cert_url", "client_x509_cert_url",
    "universe_domain", "base_url", "api_base", "endpoint", "url", "host", "model",
    "region", "version", "name", "id", "username", "user",
})

BASE_URL_KEYS = ("base_url", "api_base", "endpoint", "url", "host", "api_url",
                 "token_uri", "auth_uri", "server", "address")

BRACE_START = re.compile(r"[{\[]")


def looks_like_json(text: str) -> bool:
    stripped = text.strip()
    return bool(stripped) and stripped[0] in "{["


def parse_jsonblob(text: str, *, source: str = "json") -> Iterator[Candidate | Rejection]:
    for chunk in _json_chunks(text):
        try:
            loaded = json.loads(chunk)
        except ValueError:
            continue
        if isinstance(loaded, dict):
            yield from _from_dict(loaded, chunk, source)
        elif isinstance(loaded, list):
            for index, item in enumerate(loaded):
                if isinstance(item, dict):
                    yield from _from_dict(item, chunk, source, span_prefix=f"[{index}]")


def _json_chunks(text: str) -> Iterator[str]:
    """整段文本，以及其中花括号/方括号平衡的子串。"""
    stripped = text.strip()
    if stripped:
        yield stripped
    if BRACE_START.search(stripped) and not (stripped.startswith("{") or stripped.startswith("[")):
        for chunk in _balanced_substrings(stripped):
            yield chunk


def _balanced_substrings(text: str) -> Iterator[str]:
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char in "{[":
            if depth == 0:
                start = index
            depth += 1
        elif char in "}]":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    yield text[start : index + 1]
                    start = -1


def _from_dict(obj: dict, chunk: str, source: str,
               span_prefix: str = "") -> Iterator[Candidate | Rejection]:
    if _is_service_account(obj):
        yield _service_account(obj, chunk, source, span_prefix)
        return

    hosts = _hosts_from_siblings(obj)
    span = f"{span_prefix}json object" if span_prefix else "json object"
    context = Context(source=source, line_text=chunk[:200])

    found = 0
    for key, value in obj.items():
        if not isinstance(value, str) or not CREDENTIAL_KEYS.match(key):
            continue
        if key.lower() in NON_CREDENTIAL_KEYS:
            continue
        found += 1
        kind = "pem" if value.startswith("-----BEGIN") else "token"
        yield emit(
            value, kind=kind, key_name=_env_style(key),
            hostnames=hosts, source=source, span=span,
            extra=_extra_from_siblings(obj), context=context,
        )

    if found:
        return

    # 没有明显的凭据键，但整个对象里有 PEM —— 那这个对象本身就是一份凭据。
    if _contains_pem(obj):
        yield emit(
            json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
            kind="json", key_name=None, hostnames=hosts, source=source, span=span,
            extra=_extra_from_siblings(obj), context=context,
        )


def _is_service_account(obj: dict) -> bool:
    return str(obj.get("type", "")).lower() == "service_account" and bool(obj.get("private_key"))


def _service_account(obj: dict, chunk: str, source: str,
                     span_prefix: str) -> Candidate | Rejection:
    canonical = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    hosts = hosts_in(*[str(obj[k]) for k in ("token_uri", "auth_uri", "client_email")
                       if k in obj])
    span = f"{span_prefix}service account" if span_prefix else "service account"
    return emit(
        canonical,
        kind="json",
        key_name="GCP_SERVICE_ACCOUNT",
        hostnames=hosts,
        source=source,
        span=span,
        extra={k: str(obj[k]) for k in SERVICE_ACCOUNT_EXTRA if k in obj},
        context=Context(source=source, line_text=chunk[:200]),
    )


def _hosts_from_siblings(obj: dict) -> tuple[str, ...]:
    values = [str(obj[k]) for k in BASE_URL_KEYS if k in obj]
    values.extend(str(v) for v in obj.values() if isinstance(v, str) and "://" in v)
    return hosts_in(*values)


def _extra_from_siblings(obj: dict) -> dict:
    """把非机密的兄弟字段带出来，于是不解密也能显示「这把 key 配的是哪个 endpoint」。"""
    return {
        key: str(obj[key])[:200]
        for key in BASE_URL_KEYS + ("model", "region", "project_id", "client_email", "type")
        if key in obj and isinstance(obj[key], (str, int, float))
    }


def _contains_pem(obj: dict) -> bool:
    return any(isinstance(v, str) and v.startswith("-----BEGIN") for v in obj.values())


def _env_style(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", key).strip("_").upper()
