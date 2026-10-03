#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from docich.config import load_global
from docich.status import collect_status
from docich.twica_state import fresh, legacy_clients, state_directory, status

_spec = importlib.util.spec_from_file_location(
    "docich_twica_common_ops", ROOT / "ops/vm_actions/twica_common.py"
)
if _spec is None or _spec.loader is None:
    raise SystemExit(59)
_ops = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ops)


class ActivationBlocked(RuntimeError):
    pass


EXIT = {
    "renderer_not_ready": 41,
    "legacy_guards_not_ready": 42,
    "stream_intentionally_stopped": 43,
    "stream_supervisor_unverified": 44,
    "stream_input_not_ready_after_restart": 45,
    "handoff_timeout": 46,
    "post_activation_unhealthy": 47,
    "shared_only_evidence_unavailable": 48,
    "prepare_required": 49,
    "operation_failed": 50,
    "unexpected_install_root": 50,
    "unit_drift": 51,
}


def _runtime_proves_non_sorengame(snapshot: dict) -> bool:
    canonical = snapshot.get("canonical")
    actual = snapshot.get("actual")
    mirror = snapshot.get("mirror")
    if not isinstance(canonical, dict) or canonical.get("present") is not True:
        return False
    if canonical.get("corrupt") is True or canonical.get("phase") != "ready":
        return False
    active = canonical.get("active")
    if not isinstance(active, dict):
        return False
    game = active.get("game")
    if not isinstance(game, str) or not game or game == "sorengame":
        return False
    if snapshot.get("cleanup_pending") is not False:
        return False
    if not isinstance(mirror, dict) or mirror.get("matches_canonical") is not True:
        return False
    if not isinstance(actual, dict):
        return False
    running = actual.get("active")
    if not isinstance(running, dict) or running.get("game") != game:
        return False

    probes = []
    window = running.get("game_window")
    if isinstance(window, dict) and window.get("exists") is True:
        probes.append(window)
    session = running.get("adapter_session")
    if isinstance(session, dict) and session.get("applicable") is not False and session.get("exists") is True:
        probes.append(session)
    return any(p.get("ownership") == "matched" and p.get("panes") == "alive" for p in probes)


def _select_shared_only(clients: list[dict], runtime_snapshot: dict) -> bool:
    if any(not fresh(client) for client in clients):
        raise ActivationBlocked("legacy_guards_not_ready")
    roles = [client.get("role") for client in clients]
    if len(roles) != len(set(roles)):
        raise ActivationBlocked("legacy_guards_not_ready")
    if set(roles) == {"game", "shared"}:
        return False
    if roles == ["shared"] or set(roles) == {"shared"}:
        if _runtime_proves_non_sorengame(runtime_snapshot):
            return True
        raise ActivationBlocked("shared_only_evidence_unavailable")
    raise ActivationBlocked("legacy_guards_not_ready")


def _healthy(data: dict) -> bool:
    return (
        data.get("owner") == "common"
        and data.get("pipeline_ready") is True
        and data.get("renderer_alive") is True
        and data.get("renderer_state") == "active"
        and data.get("frame_state") == "fresh"
        and data.get("legacy_subscribers") == 0
        and data.get("legacy_healthy") is True
    )


def main() -> int:
    try:
        _ops.install()
        directory = state_directory()
        g = load_global(ROOT, ROOT / "config/docich.soren-live.toml")
        shared_only = _select_shared_only(legacy_clients(directory), collect_status(g))
        _ops.arm_stream(True, shared_only=shared_only)
        _ops.transfer(
            directory,
            "common",
            idle_confirmed=True,
            shared_only=shared_only,
            legacy_role="game",
        )
        return 0 if _healthy(status(directory)) else EXIT["post_activation_unhealthy"]
    except ActivationBlocked as exc:
        return EXIT.get(str(exc), 59)
    except _ops.NotReady as exc:
        return EXIT.get(str(exc), 59)
    except Exception:
        return 59


if __name__ == "__main__":
    raise SystemExit(main())
