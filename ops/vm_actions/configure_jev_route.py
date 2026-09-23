#!/usr/bin/env python3
"""Select the docich comment classifier's Jev route (#882), owner-only.

Manages ONLY ``DOCICH_JEV_ROUTE`` / ``DOCICH_JEV_VERCEL_API_KEY`` in soren's
.env -- the route keys ``docich.comment_classifier`` reads. It never touches
``COMMENT_CLASSIFIER_BACKEND``, ``COMMENT_CLASSIFIER_JEV_*`` or
``TYPESAFE_API_KEY``: whether Jev is called at all, and its timeout/threshold
and direct-route credential, stay owned by ``configure_comment_classifier_jev.py``
(#678). This script only decides which reviewed route those calls use.

``--disable`` (the ``disable_jev_route`` operation) removes both keys, so the
classifier returns to its default ``direct`` route and the Vercel secret
leaves the runtime env. It does not turn Jev off; ``disable_jev`` does that.

The retired ``DOCICH_SEMANTIC_BACKEND`` switch (read only by the removed
soviet_now adapter) is never written; any stale assignment of it is dropped
whenever this script rewrites .env.

A Vercel API key is supplied only through this process's own environment by
the reviewed control plane, exactly like ``configure_comment_classifier_jev.py``'s
existing ``TYPESAFE_API_KEY`` contract. This script never prints or stores a
secret outside soren's .env file and its protected rollback backup.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import types


def _load_sibling(name: str):
    path = Path(__file__).with_name(f"{name}.py")
    if path.is_file():
        spec = importlib.util.spec_from_file_location(name, str(path))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    # The owner-only gateway executes this reviewed script through ``python -``
    # so __file__ is <stdin>. In that path, load the sibling from the reviewed
    # Git object at HEAD rather than from the mutable worktree. The gateway has
    # already verified that production HEAD is the requested protected-main SHA.
    repo_path = Path("ops") / "vm_actions" / f"{name}.py"
    try:
        source = subprocess.check_output(
            ["git", "-c", "core.hooksPath=/dev/null", "show", f"HEAD:{repo_path.as_posix()}"],
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("reviewed sibling missing") from exc
    module = types.ModuleType(name)
    module.__file__ = str(repo_path)
    exec(compile(source, str(repo_path), "exec"), module.__dict__)
    return module


_classifier = _load_sibling("configure_comment_classifier_jev")
ConfigureError = _classifier.ConfigureError
_backup_env = _classifier._backup_env
_regular_file = _classifier._regular_file
_write_atomic = _classifier._write_atomic
ASSIGNMENT = _classifier.ASSIGNMENT
restart_chat_worker_verified = _classifier.restart_chat_worker_verified

MANAGED_KEYS = ("DOCICH_JEV_ROUTE", "DOCICH_JEV_VERCEL_API_KEY")
RETIRED_KEYS = ("DOCICH_SEMANTIC_BACKEND",)
ROUTES = ("direct", "vercel")


def validate_vercel_key(value: str) -> None:
    if not value or len(value) > 4096 or not value.isascii():
        raise ConfigureError("invalid_vercel_api_key")
    if any(ord(char) < 33 or ord(char) > 126 for char in value):
        raise ConfigureError("invalid_vercel_api_key")


def _rewrite_route_env(env_file: Path, managed_lines: list[str]) -> Path:
    """Atomically replace only this script's own managed assignments.

    Reuses configure_comment_classifier_jev's backup/atomic-write primitives
    with this script's own, disjoint MANAGED_KEYS -- never the #678 keys --
    and drops any stale RETIRED_KEYS assignment.
    """
    info = _regular_file(env_file, "env")
    try:
        original = env_file.read_bytes()
        text = original.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ConfigureError("env_unreadable") from exc

    kept = []
    for line in text.splitlines(keepends=True):
        match = ASSIGNMENT.match(line)
        if match and match.group(1) in MANAGED_KEYS + RETIRED_KEYS:
            continue
        kept.append(line)
    if kept and not kept[-1].endswith(("\n", "\r")):
        kept.append("\n")
    kept.extend(managed_lines)
    new_data = "".join(kept).encode("utf-8")

    backup = _backup_env(env_file, original, info)
    _write_atomic(env_file, new_data, info)
    return backup


def configure_direct_env(env_file: Path) -> Path:
    """Pin the classifier to the direct route.

    Does not write TYPESAFE_API_KEY: that credential is #678's own, and the
    caller (main()) must already have confirmed it is configured.
    """
    return _rewrite_route_env(env_file, ["DOCICH_JEV_ROUTE=direct\n"])


def configure_vercel_env(env_file: Path, vercel_api_key: str) -> Path:
    """Switch the classifier to the vercel route."""
    validate_vercel_key(vercel_api_key)
    return _rewrite_route_env(env_file, [
        "DOCICH_JEV_ROUTE=vercel\n",
        # .env is sourced by the worker; quote the secret as a shell value so
        # printable punctuation cannot become shell syntax.
        f"DOCICH_JEV_VERCEL_API_KEY={shlex.quote(vercel_api_key)}\n",
    ])


def disable_env(env_file: Path) -> Path:
    """Return to the default direct route and remove the vercel secret.

    COMMENT_CLASSIFIER_BACKEND/TYPESAFE_API_KEY are untouched: whether Jev is
    called at all stays #678's own contract (``disable_jev``).
    """
    return _rewrite_route_env(env_file, [])


def _require_default_direct_route_ready(runtime_env: dict[str, str]) -> None:
    """Fail closed if resetting the route would silently disable live Jev.

    With no DOCICH_JEV_ROUTE the classifier defaults to ``direct``. If the
    purpose-level Jev gate is active, that route requires #678's existing
    TYPESAFE_API_KEY. Check this before mutating .env and again on the
    replacement worker so ``disable_jev_route`` cannot turn a working Vercel
    configuration into heuristic fallback while reporting success.
    """
    if runtime_env.get("COMMENT_CLASSIFIER_BACKEND") == "jev" and not runtime_env.get("TYPESAFE_API_KEY"):
        raise ConfigureError("direct_route_requires_existing_typesafe_api_key")


def verify_jev_route(route: str | None):
    """route=None means reset to the default route with no vercel secret."""
    def verify(runtime_env: dict[str, str]) -> None:
        if route is None:
            if runtime_env.get("DOCICH_JEV_ROUTE"):
                raise ConfigureError("chat_worker_docich_route_still_set")
            if runtime_env.get("DOCICH_JEV_VERCEL_API_KEY"):
                raise ConfigureError("chat_worker_vercel_api_key_still_present")
            _require_default_direct_route_ready(runtime_env)
            return
        if runtime_env.get("DOCICH_JEV_ROUTE") != route:
            raise ConfigureError("chat_worker_docich_route_mismatch")
        if route == "vercel" and not runtime_env.get("DOCICH_JEV_VERCEL_API_KEY"):
            raise ConfigureError("chat_worker_vercel_api_key_missing")
        if route == "direct" and not runtime_env.get("TYPESAFE_API_KEY"):
            # This script never writes TYPESAFE_API_KEY; if the replacement
            # worker does not have it, the direct route cannot function,
            # even though it is #678's key, not this script's own.
            raise ConfigureError("chat_worker_api_key_missing_for_direct_route")
    return verify


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--soren-root", type=Path, default=Path("/home/ubuntu/soren"))
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--route", choices=ROUTES)
    group.add_argument("--disable", action="store_true")
    args = parser.parse_args()

    env_file = args.soren_root / ".env"
    current = _classifier._process_env(
        _classifier._read_worker_pid(args.soren_root / _classifier.WORKER_PID_FILE) or -1
    )
    if args.disable:
        # Reset means default-direct, not "turn Jev off". If Jev is currently
        # active, prove the direct credential exists before removing the
        # working Vercel route/secret from .env.
        _require_default_direct_route_ready(current)
        backup = disable_env(env_file)
        route = None
    elif args.route == "direct":
        if not current.get("TYPESAFE_API_KEY"):
            # Fail closed before writing anything: direct route needs #678's
            # own credential already configured and live on the current
            # worker, not merely present somewhere in the .env file.
            raise ConfigureError("direct_route_requires_existing_typesafe_api_key")
        backup = configure_direct_env(env_file)
        route = "direct"
    else:
        vercel_api_key = os.environ.get("DOCICH_JEV_VERCEL_API_KEY", "")
        backup = configure_vercel_env(env_file, vercel_api_key)
        route = "vercel"

    old_pid, new_pid = restart_chat_worker_verified(
        args.soren_root, verify=verify_jev_route(route))
    print(json.dumps({
        "status": "default_route" if route is None else "configured",
        "route": route or "direct",
        "old_pid": old_pid,
        "new_pid": new_pid,
        "backup": str(backup),
    }, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ConfigureError as exc:
        print(f"configuration failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
