"""Owner-only fixed control of one stalled Hanjuku prediction round (#2216).

The canonical recovery for a prediction round that stopped on an unverifiable
record used to be the WebUI `POST /api/predictions/action` route only. That
route drives *Soren's* `twitch_predictions.sh` and `tmp/state/current_prediction.json`,
so it never touches the half-cooked hero's durable ledger
(`<state_dir>/hanjuku_predictions.json`) and cannot unblock a round owned by
`hanjuku_predictions`. This module is the reviewed, fixed-operation entry that
can.

Only two fixed operations exist, and neither accepts a free-form argument:

``cancel``
    Cancel the round that the ledger currently owns and clear the local
    ownership, refunding the channel points on Twitch. Used when the round
    provably cannot settle (the #2213 class) and the corner must not stay
    blocked.

``sync``
    Re-derive the local ownership from the authoritative remote state. A round
    the remote already finished (``RESOLVED`` / ``CANCELED``) clears the local
    ownership; a live remote round is re-adopted with its real ids; a remote
    that no longer has the round at all clears the stale local ownership.

Both operations reconcile *local ownership against the remote*, so they can
only ever end or re-adopt the round that is already recorded. They never
create a new round, never change a target/middle/outcome selection, never pick
a winner, and never touch another corner's wager.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

from . import hanjuku_predictions as predictions
from .config import load_global
from .game_switch import atomic_write_json
from .hanjuku_prediction_api import APIError

OPERATIONS = ("cancel", "sync")
#: The reviewed production profile; the gateway env does not set DOCICH_CONFIG.
LIVE_CONFIG = "config/docich.soren-live.toml"

#: Fixed, secret-safe outcome codes. Nothing else crosses the operator boundary.
EXIT_OK = 0
EXIT_REFUSED = 65
EXIT_OPERATION_FAILED = 66
EXIT_UNREADABLE = 75



class ControlRefused(RuntimeError):
    """A fixed reason code; never a ledger body, token or remote payload."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _soren_root(g):
    # Imported lazily so this reviewed operator can be exercised without a
    # full Soren checkout in unit tests that inject their own client.
    from .trading.soren_output import resolve_soren_root

    return resolve_soren_root(g)


def _client(g, client_factory=None):
    """Return a usable Helix client or refuse with a fixed code."""
    factory = client_factory or predictions.configured_client
    client, unavailable = factory(g)
    if unavailable:
        raise ControlRefused(f"unavailable_{unavailable}")
    if client is None:
        raise ControlRefused("unavailable_configuration")
    if client_factory is None and not _soren_root(g).is_dir():
        # The reviewed real path must resolve an actual Soren root, otherwise
        # we could act against an unrelated directory.
        raise ControlRefused("soren_root_missing")
    return client


def _seed(g):
    """Prediction seed, or a fixed refusal code when the config is unreadable."""
    try:
        return predictions.config(g)[1]
    except Exception:
        raise ControlRefused("configuration_unavailable") from None


def _owned_row(state):
    """The live row this ledger owns, or refuse.

    A refused operation must never fabricate a round to act on: only a row the
    ledger already recorded with a remote id can be canceled or synced.
    """
    row = state.get("round")
    if not isinstance(row, dict) or not row:
        raise ControlRefused("no_round")
    if row.get("status") in predictions.DONE:
        raise ControlRefused("round_already_terminal")
    if not isinstance(row.get("id"), str) or not row["id"]:
        # An INTENT that never reached Twitch has no remote object to control.
        raise ControlRefused("round_not_remote")
    return row


def _remote_row(client, row):
    """The single remote row for this id, or ``None`` when GET returns nothing.

    A row that carries this id but differs in title/outcomes is an ownership
    *mismatch*, not an absence. Filtering it out would silently drop local
    ownership for a prediction that still exists in another shape, so a
    mismatch fails closed instead.
    """
    try:
        rows = client.list(row.get("id"))
    except APIError as exc:
        raise ControlRefused(f"remote_{exc.code}") from None
    candidates = [r for r in rows if isinstance(r, dict) and r.get("id") == row.get("id")]
    if not candidates:
        return None
    if len(candidates) > 1 or not predictions._matches(candidates[0], row):
        raise ControlRefused("remote_mismatch")
    return candidates[0]


def cancel(g, *, client_factory=None, now=None):
    """Cancel the owned round remotely and leave this attempt terminal.

    The round is never erased: a DONE row belonging to the same run is what
    stops `_advance` from opening a second wager in that run, and it is also
    what lets the ordinary tick re-observe the `CANCELED` remote row and retry
    the participation record exactly once (#933). A later, different runtime
    still creates normally.
    """
    root = Path(g.state_dir)
    now = time.time() if now is None else now
    client = _client(g, client_factory)
    with predictions.locked(root) as acquired:
        if not acquired:
            raise ControlRefused("busy")
        seed = _seed(g)
        try:
            state = predictions._load_recoverable(root, seed)
        except Exception:
            raise ControlRefused("ledger_unreadable") from None
        row = _owned_row(state)
        remote = _remote_row(client, row)
        # A DONE row stops `_advance` from opening a second wager in this run.
        # While the participation record is still pending the row must instead
        # stay unresolved, so the next tick re-observes CANCELED and records the
        # same id exactly once (an unresolved round also blocks a new wager).
        terminal = True
        if remote is None:
            # GET really returned nothing for this id. Do not PATCH an id we
            # cannot verify; terminalize the attempt locally instead.
            outcome = "remote_absent"
        elif remote.get("status") in predictions.DONE:
            # Twitch rejects CANCELED on a finished prediction. Do not turn a
            # recoverable mismatch into a failed PATCH: `sync` is the operation
            # that records the remote's terminal state without changing it.
            raise ControlRefused(f"remote_{remote['status'].lower()}_use_sync")
        else:
            # Bounded, verified evidence BEFORE the irreversible cancel, exactly
            # like the ordinary tick: the snapshot survives a failed
            # participation write so it can be retried once after restart.
            if (predictions._no_contest(remote) and remote.get("status") == "LOCKED"):
                try:
                    predictions._save_cancel_snapshot(root, row, remote)
                except APIError:
                    raise ControlRefused("evidence_unavailable") from None
            try:
                response = client.end(row["id"], "CANCELED")
            except APIError as exc:
                raise ControlRefused(f"remote_{exc.code}") from None
            if (len(response) != 1 or not predictions._matches(response[0], row)
                    or response[0].get("status") != "CANCELED"):
                raise ControlRefused("remote_unverified")
            canceled = response[0]
            outcome = "canceled"
            if predictions._no_contest(canceled):
                try:
                    predictions._record_canceled(root, now, client, sources=(canceled,))
                    predictions._cancel_snapshot_path(root, row).unlink(missing_ok=True)
                except (APIError, OSError):
                    # Keep the snapshot and leave the round unresolved: the
                    # next tick re-observes CANCELED and records this id once.
                    # Report the pending write rather than claiming completion.
                    outcome = "canceled_record_pending"
                    terminal = False
        if terminal:
            # Terminal tombstone for this attempt, never `round=None`.
            row["status"] = "CANCELED"
            state.update(mode="canceled", error=None, next_poll_at=0)
        else:
            # Keep the round unresolved: the next tick re-observes CANCELED and
            # records this id once, without opening a second wager.
            state.update(mode="settling", error=None, next_poll_at=0)
        atomic_write_json(root / predictions.FILE, state)
        predictions._update_health(root, {"mode": state.get("mode"), "error": None}, now)
    return {"operation": "cancel", "outcome": outcome, "prediction_id": row["id"]}


def sync(g, *, client_factory=None, now=None):
    """Re-derive local ownership from the authoritative remote state.

    ``sync`` never changes a selection: it only adopts the real remote ids for
    the round the ledger already recorded, or clears ownership the remote no
    longer justifies.
    """
    root = Path(g.state_dir)
    now = time.time() if now is None else now
    client = _client(g, client_factory)
    with predictions.locked(root) as acquired:
        if not acquired:
            raise ControlRefused("busy")
        seed = _seed(g)
        try:
            state = predictions._load_recoverable(root, seed)
        except Exception:
            raise ControlRefused("ledger_unreadable") from None
        row = _owned_row(state)
        remote = _remote_row(client, row)
        if remote is None:
            # Terminalize this attempt locally; never erase the row, or the
            # same run could open a second wager on the next tick.
            row["status"] = "CANCELED"
            state.update(mode="canceled", error=None, next_poll_at=0)
            outcome = "remote_absent"
        elif remote.get("status") in predictions.DONE:
            # The remote already ended this round. Record the outcome the
            # normal tick would have recorded, then clear local ownership.
            if remote["status"] == "RESOLVED":
                try:
                    predictions._record_settled(root, remote, now, client)
                except APIError as exc:
                    if exc.code != "invalid_response":
                        raise ControlRefused(f"ledger_{exc.code}") from None
            else:
                snapshot = predictions._load_cancel_snapshot(root, row)
                if snapshot is not None or predictions._no_contest(remote):
                    try:
                        predictions._record_canceled(root, now, client,
                                                     sources=(remote, snapshot))
                    except APIError as exc:
                        if exc.code != "invalid_response":
                            raise ControlRefused(f"ledger_{exc.code}") from None
                    predictions._cancel_snapshot_path(root, row).unlink(missing_ok=True)
            # Terminal tombstone: `_advance` refuses a second wager in a run
            # whose round is already DONE, and a later runtime still creates.
            row["status"] = remote["status"]
            state.update(mode=remote["status"].lower(), error=None, next_poll_at=0)
            outcome = remote["status"].lower()
        else:
            # Re-adopt the live round with the authoritative ids/status only.
            # Target, middle and the outcome titles stay exactly as recorded.
            adopted = remote.get("outcome_ids")
            if adopted is None and isinstance(remote.get("outcomes"), list):
                adopted = [o.get("id") for o in remote["outcomes"]]
            expected = len(row["outcome_titles"])
            if (not isinstance(adopted, list) or len(adopted) != expected
                    or len(set(adopted)) != expected
                    or not all(isinstance(i, str) and 1 <= len(i) <= 128 for i in adopted)):
                raise ControlRefused("remote_ownership_invalid")
            row.update(id=remote["id"], outcome_ids=adopted, status=remote["status"])
            state.update(mode="active", error=None, next_poll_at=0)
            outcome = "adopted"
        atomic_write_json(root / predictions.FILE, state)
        predictions._update_health(root, {"mode": state.get("mode"), "error": None}, now)
    return {"operation": "sync", "outcome": outcome, "prediction_id": row.get("id")}


def run(g, operation, *, client_factory=None, now=None):
    if operation not in OPERATIONS:
        raise ControlRefused("unsupported_operation")
    return (cancel if operation == "cancel" else sync)(
        g, client_factory=client_factory, now=now)


def _build_parser():
    parser = argparse.ArgumentParser(prog="docich hanjuku-prediction-control")
    parser.add_argument("--config", metavar="PATH")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("cancel", help="所有する予想 round をキャンセルして返金する")
    sub.add_parser("sync", help="所有する予想 round を remote の権威状態へ同期する")
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    # The gateway runs fixed scripts in a limited environment that does not
    # carry DOCICH_CONFIG, so an unspecified config would resolve to
    # config/docich.toml (state_dir=run) instead of the live profile
    # (run-soren-live). Always bind the reviewed production profile.
    root = _repo_root()
    config = Path(args.config) if args.config else root / LIVE_CONFIG
    if not config.is_file():
        print("docich: prediction control live configuration missing", file=sys.stderr)
        return EXIT_UNREADABLE
    try:
        g = load_global(root, config)
    except Exception:
        print("docich: prediction control configuration unavailable", file=sys.stderr)
        return EXIT_UNREADABLE
    try:
        result = run(g, args.command)
    except ControlRefused as exc:
        print(f"docich: prediction control refused ({exc.code})", file=sys.stderr)
        return EXIT_REFUSED
    except Exception:
        print("docich: prediction control failed", file=sys.stderr)
        return EXIT_OPERATION_FAILED
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return EXIT_OK


def _repo_root():
    return Path(__file__).resolve().parents[2]


if __name__ == "__main__":
    raise SystemExit(main())
