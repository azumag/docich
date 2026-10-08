"""Corpus loading and production-data projection for offline eval (#1308 PR-1).

The public suite (``evals/comment/v1``) holds only synthetic fixtures. Private
production-derived cases live under
``$DOCICH_STATE_DIR/eval/corpora/<suite>/cases.jsonl`` and are projected through
:func:`sanitize_text` before a runner sees them, so no raw username, URL, path
or secret can leak from an eval corpus into a log or a committed report.

Only an explicit call loads a private corpus; nothing here exports it back to
Git. Images are never carried: a private case may set ``image_attached`` but
its bytes are not stored by this module.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from . import contracts
from .contracts import ContractError

MAX_CASES = 5000
STATE_ENV = "DOCICH_STATE_DIR"
SALT_ENV = "DOCICH_EVAL_SALT"


def read_jsonl(path) -> list:
    """Parse a JSONL file strictly: one JSON value per non-blank line."""
    rows = []
    with Path(path).open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ContractError(f"invalid_json_line:{number}") from exc
            if len(rows) > MAX_CASES:
                raise ContractError("too_many_cases")
    return rows


def write_jsonl(path, rows) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    body = "".join(contracts.canonical(row) + "\n" for row in rows)
    Path(path).write_text(body, encoding="utf-8")


def tokenize_user(user: str, *, salt: str = "") -> str:
    """Irreversible username token; the projection never keeps the raw value."""
    if not isinstance(user, str) or not user.strip():
        return "tok-anonymous"
    raw = (salt + "\x00" + contracts.normalize_comment(user)).encode("utf-8")
    return "tok-" + hashlib.sha256(raw).hexdigest()[:12]


def sanitize_text(text: str, *, salt: str = "") -> str:
    """Allowlist projection: URLs, handles, paths and secrets become markers."""
    if not isinstance(text, str):
        raise ContractError("invalid_text")
    text = contracts._URL_RE.sub("[url]", text)
    text = contracts._USER_AT_RE.sub("[user]", text)
    text = contracts._USER_PREFIX_RE.sub("[user]:", text)
    for name, pattern in contracts.SECRET_PATTERNS:
        text = pattern.sub(f"[{name}]", text)
    return contracts.normalize_comment(text)


def project_private_case(raw: dict, *, salt: str) -> dict:
    """Anonymise one production-derived case, then validate it as a case.

    ``raw`` uses the eval case shape plus optional ``input.user``; the user is
    replaced by a salted token and the body by :func:`sanitize_text`. The
    original values are dropped, not stored alongside the projection.
    """
    if not isinstance(raw, dict):
        raise ContractError("invalid_case")
    source = dict(raw.get("input") or {})
    user, source["user"] = source.get("user", ""), None
    source["comment"] = sanitize_text(source.get("comment", ""), salt=salt)
    projected = {
        "case_id": raw.get("case_id"),
        "group_id": raw.get("group_id"),
        "input": source,
        "expected": dict(raw.get("expected") or {}),
        "tags": list(raw.get("tags") or []),
    }
    if user:
        tags = list(projected["tags"])
        if "user_token" not in tags:
            tags.append("user_token:" + tokenize_user(user, salt=salt))
        projected["tags"] = tags
    case = contracts.validate_case(projected)
    case["input"].pop("user", None)
    return case


def load_public_cases(directory) -> dict:
    """Load ``manifest.json`` + public/critical fixtures from a suite directory.

    Returns ``{"manifest":..., "cases":[...], "critical_ids": frozenset}``.
    Every fixture is validated and gated by :func:`contracts.assert_public_safe`
    so a production-derived body cannot be committed by mistake.
    """
    directory = Path(directory)
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise ContractError("missing_manifest")
    manifest = contracts.validate_manifest(json.loads(manifest_path.read_text(encoding="utf-8")))
    cases, critical_ids = [], set()
    for key, flag in (("public_cases", False), ("critical_cases", True)):
        name = manifest[key]
        path = directory / name
        if not path.is_file():
            raise ContractError(f"missing_{key}")
        for raw in read_jsonl(path):
            case = contracts.validate_case(raw)
            contracts.assert_public_safe(case)
            if case["case_id"] in critical_ids or case["case_id"] in {c["case_id"] for c in cases}:
                raise ContractError("duplicate_case_id:" + case["case_id"])
            cases.append(case)
            if flag:
                critical_ids.add(case["case_id"])
    manifest = {**manifest, "case_count": len(cases),
                "corpus_digest": contracts.digest(cases)}
    return {"manifest": manifest, "cases": cases, "critical_ids": frozenset(critical_ids)}


def private_corpus_dir(suite: str, *, env=None) -> Path:
    env = os.environ if env is None else env
    state = env.get(STATE_ENV)
    if not state:
        raise ContractError("missing_state_dir")
    return Path(state) / "eval" / "corpora" / suite


def load_private_cases(suite: str, *, env=None, salt=None) -> list:
    """Load and project a private corpus; requires both state dir and salt."""
    env = os.environ if env is None else env
    salt = salt if salt is not None else env.get(SALT_ENV)
    if not salt:
        raise ContractError("missing_eval_salt")
    path = private_corpus_dir(suite, env=env) / "cases.jsonl"
    if not path.is_file():
        raise ContractError("missing_private_corpus")
    return [project_private_case(raw, salt=salt) for raw in read_jsonl(path)]
