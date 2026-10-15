#!/usr/bin/env python3
"""Select the docich comment classifier's Jev route (#882), owner-only.

Manages route assignments and, on explicit Clef provisioning, its dedicated
account/token in soren's .env. Clef credentials are retained on a switch back
to direct, so the next Clef switch needs no secret re-entry. It never touches
``COMMENT_CLASSIFIER_BACKEND``, ``COMMENT_CLASSIFIER_JEV_*`` or
``TYPESAFE_API_KEY``: whether Jev is called at all, and its timeout/threshold
and direct-route credential, stay owned by ``configure_comment_classifier_jev.py``
(#678). This script only decides which reviewed route(s) those calls use.

``--route clef`` selects Clef only (not an automatic fallback chain). Initial
account/token provisioning takes the pair from this process's dedicated
environment pair, or -- with ``--reuse-existing-cloudflare-credentials`` -- from
this VM's own reviewed Cloudflare pair (see ``CLEF_REUSE_SOURCES``), which keeps
the secret off the wire entirely. Subsequent switches reuse the existing chat
worker's pair. Both Jev and Clef credentials are preserved and checked on the
replacement chat worker.

The fixed gateway operations are:

- ``configure_jev_route_direct`` / ``configure_jev_route_vercel`` /
  ``configure_jev_route_clef`` / ``disable_jev_route`` for this script's routes,
- ``configure_jev_route_clef_reuse`` for the reuse provisioning above (empty
  stdin: it never accepts or needs a secret).

The existing fixed gateway operations map to fixed values:

- ``configure_jev_route_direct`` (``--route direct``): ``DOCICH_JEV_ROUTE=direct`,
  direct only, Vercel secret removed.
- ``configure_jev_route_vercel`` (``--route vercel``): ``DOCICH_JEV_ROUTE=direct,vercel``
  plus the Vercel secret -- direct stays primary and Vercel is the automatic
  fallback the classifier uses after a fast direct failure or while direct
  is cooling down (owner decision; see ``docich.comment_classifier.jev``).

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
import re
from pathlib import Path
import shlex
import stat
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
CLEF_KEYS = ("DOCICH_JEV_CLOUDFLARE_ACCOUNT_ID", "DOCICH_JEV_CLOUDFLARE_API_TOKEN")
ROUTES = ("direct", "vercel", "clef")
# --route -> the exact DOCICH_JEV_ROUTE value written and then verified live.
ROUTE_VALUES = {"direct": "direct", "vercel": "direct,vercel", "clef": "clef"}
# Reviewed sources for reusing this VM's own, already-issued Cloudflare pair as
# the Clef pair (#2301). Each entry is ONE provider's account/token pair, so a
# token is never combined with another source's account. Both are Workers AI
# capable: the first is the direct Workers AI chat provider's own pair, the
# second the reviewed Web Search pair. Adding a source is a reviewed change.
CLEF_REUSE_SOURCES = (
    ("DOCICH_CHAT_CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN"),
    ("DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_ACCOUNT_ID", "DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_API_TOKEN"),
)
CLEF_ACCOUNT_RE = re.compile(r"[0-9a-f]{32}\Z")


def _read_existing_secret(env, name: str) -> str:
    """Resolve NAME, or its reviewed NAME_FILE indirection, without printing it.

    Mirrors ``docich.discord_chat.read_secret``: the value and its file form are
    mutually exclusive, the file must be a regular non-world-accessible file
    opened without following a symlink, and the result must be a short,
    printable ASCII token. Any deviation is a fixed, secret-free failure.
    """
    value = env.get(name, "")
    filename = env.get(name + "_FILE", "")
    value = value.strip() if isinstance(value, str) else ""
    filename = filename.strip() if isinstance(filename, str) else ""
    if value and filename:
        raise ConfigureError("clef_source_credentials_conflicting")
    if not filename:
        return value
    try:
        descriptor = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise ConfigureError("clef_source_credentials_unreadable") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o007:
            raise ConfigureError("clef_source_credentials_unreadable")
        raw = os.read(descriptor, 4097)
    except OSError as exc:
        raise ConfigureError("clef_source_credentials_unreadable") from exc
    finally:
        os.close(descriptor)
    if len(raw) > 4096:
        raise ConfigureError("clef_source_credentials_unreadable")
    try:
        text = raw.decode("ascii").removesuffix("\n").removesuffix("\r")
    except UnicodeError as exc:
        raise ConfigureError("clef_source_credentials_unreadable") from exc
    if not text or any(not 33 <= ord(char) <= 126 for char in text):
        raise ConfigureError("clef_source_credentials_unreadable")
    return text


def clef_credentials_from_existing(env) -> tuple[str, str]:
    """Return the first usable reviewed Cloudflare pair from this VM's env.

    Only the pair that is actually configured is returned, and only to the
    caller; neither half is ever printed or written anywhere but soren's .env
    and its protected rollback backup. Nothing is inferred: an account that is
    absent or not the reviewed 32-lowercase-hex shape, or a token that is
    missing or unusable, makes that source ineligible rather than guessed.
    """
    for account_name, token_name in CLEF_REUSE_SOURCES:
        account = env.get(account_name, "")
        account = account.strip() if isinstance(account, str) else ""
        if account and CLEF_ACCOUNT_RE.fullmatch(account) is None:
            raise ConfigureError("clef_source_account_invalid")
        if not account:
            continue
        token = _read_existing_secret(env, token_name)
        if not token:
            continue
        return clef_settings(dict(zip(CLEF_KEYS, (account, token))))
    raise ConfigureError("clef_source_credentials_missing")


def validate_vercel_key(value: str) -> None:
    if not value or len(value) > 4096 or not value.isascii():
        raise ConfigureError("invalid_vercel_api_key")
    if any(ord(char) < 33 or ord(char) > 126 for char in value):
        raise ConfigureError("invalid_vercel_api_key")


def _rewrite_route_env(env_file: Path, managed_lines: list[str], *,
                       managed_keys=MANAGED_KEYS) -> Path:
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
        if match and match.group(1) in managed_keys + RETIRED_KEYS:
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
    return _rewrite_route_env(env_file, [f"DOCICH_JEV_ROUTE={ROUTE_VALUES['direct']}\n"])


def configure_vercel_env(env_file: Path, vercel_api_key: str) -> Path:
    """Keep direct primary and add Vercel as the automatic fallback."""
    validate_vercel_key(vercel_api_key)
    return _rewrite_route_env(env_file, [
        f"DOCICH_JEV_ROUTE={ROUTE_VALUES['vercel']}\n",
        # .env is sourced by the worker; quote the secret as a shell value so
        # printable punctuation cannot become shell syntax.
        f"DOCICH_JEV_VERCEL_API_KEY={shlex.quote(vercel_api_key)}\n",
    ])


def clef_settings(env) -> tuple[str, str]:
    """Validate a single account/token pair without exposing either value."""
    account, token = (env.get(key, "") for key in CLEF_KEYS)
    if type(account) is not str or re.fullmatch(r"[0-9a-f]{32}", account) is None:
        raise ConfigureError("invalid_clef_account_id")
    if (type(token) is not str or not 1 <= len(token) <= 4096
            or any(not 33 <= ord(char) < 127 for char in token)):
        raise ConfigureError("invalid_clef_api_token")
    return account, token


def configure_clef_env(env_file: Path, account: str, token: str) -> Path:
    """Select Clef, leaving the existing TYPESAFE_API_KEY bytes untouched."""
    clef_settings(dict(zip(CLEF_KEYS, (account, token))))
    return _rewrite_route_env(env_file, [
        "DOCICH_JEV_ROUTE=clef\n",
        f"{CLEF_KEYS[0]}={shlex.quote(account)}\n",
        f"{CLEF_KEYS[1]}={shlex.quote(token)}\n",
    ], managed_keys=MANAGED_KEYS + CLEF_KEYS)


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


def verify_jev_route(route: str | None, *, retained_credentials=None):
    """route=None means reset to the default route with no vercel secret."""
    def verify(runtime_env: dict[str, str]) -> None:
        for key, expected in (retained_credentials or {}).items():
            if runtime_env.get(key) != expected:
                raise ConfigureError("chat_worker_retained_credential_mismatch")
        if route is None:
            if runtime_env.get("DOCICH_JEV_ROUTE"):
                raise ConfigureError("chat_worker_docich_route_still_set")
            if runtime_env.get("DOCICH_JEV_VERCEL_API_KEY"):
                raise ConfigureError("chat_worker_vercel_api_key_still_present")
            _require_default_direct_route_ready(runtime_env)
            return
        if runtime_env.get("DOCICH_JEV_ROUTE") != ROUTE_VALUES[route]:
            raise ConfigureError("chat_worker_docich_route_mismatch")
        if route == "clef":
            clef_settings(runtime_env)
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
    parser.add_argument("--reuse-existing-cloudflare-credentials", action="store_true",
                        help="--route clef only: provision the Clef pair from this VM's own "
                             "reviewed Cloudflare pair instead of from this process's environment")
    args = parser.parse_args()

    if args.reuse_existing_cloudflare_credentials and args.route != "clef":
        raise ConfigureError("clef_reuse_requires_clef_route")
    env_file = args.soren_root / ".env"
    current = _classifier._process_env(
        _classifier._read_worker_pid(args.soren_root / _classifier.WORKER_PID_FILE) or -1
    )
    retained = {key: current[key] for key in (*CLEF_KEYS, "TYPESAFE_API_KEY")
                if current.get(key)}
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
    elif args.route == "clef":
        # A reversible migration must retain the already-working Jev route.
        if not current.get("TYPESAFE_API_KEY"):
            raise ConfigureError("clef_switch_requires_existing_typesafe_api_key")
        if args.reuse_existing_cloudflare_credentials:
            # First-time provisioning from this VM's own, already-issued pair.
            # The live worker's environ is the already-shell-resolved source, so
            # no .env parsing and no secret on the wire or on a command line.
            account, token = clef_credentials_from_existing(current)
        else:
            # Never combine a token from one source with an account from another.
            source = os.environ if any(key in os.environ for key in CLEF_KEYS) else current
            account, token = clef_settings(source)
        backup = configure_clef_env(env_file, account, token)
        retained.update(zip(CLEF_KEYS, (account, token)))
        route = "clef"
    else:
        vercel_api_key = os.environ.get("DOCICH_JEV_VERCEL_API_KEY", "")
        backup = configure_vercel_env(env_file, vercel_api_key)
        route = "vercel"

    old_pid, new_pid = restart_chat_worker_verified(
        args.soren_root, verify=verify_jev_route(route, retained_credentials=retained))
    print(json.dumps({
        "status": "default_route" if route is None else "configured",
        "route": ROUTE_VALUES[route] if route else "direct",
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
