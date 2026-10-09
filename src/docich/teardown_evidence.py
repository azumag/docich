"""Per-runtime teardown evidence for the game-switch event log (Issue #1936).

A ``cleaned`` event used to be emitted with an empty ``detail``: the
coordinator reported that teardown succeeded without recording *what* it tore
down or *how* it proved the runtime was gone.  Issue #1105 could therefore not
be closed — the log said ``cleaned`` while an orphaned moon-buggy kept burning
CPU under the tmux server.

This module carries the evidence a teardown has to produce and renders it as a
short, redaction-safe ``detail`` line.  The rendering follows the existing
``_sanitize_log_detail`` policy in :mod:`docich.game_switch`:

* no full command lines, no URLs, no credential ``key=value`` pairs,
* no long opaque tokens (a PID list is not one, a 32+ hex blob is),
* every list is capped, so a runaway teardown cannot grow the log (the
  production log is ~25k lines / 10MB).

The recorder is a mutable object so the thread the coordinator runs an adapter
step in can write into the recorder the coordinator owns: a worker thread
inherits a *copy* of the caller's context, so both hold the same object and its
mutations are visible to the coordinator.  Recording is opt-in — with no active
recorder the teardown paths behave exactly as before.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from .log_redaction import redact_log_detail

# Cap on the number of entries rendered for any list-valued evidence field.  A
# teardown that would exceed this is itself a fault worth surfacing, so the
# renderer keeps a truncation marker instead of the tail.
MAX_EVIDENCE_ENTRIES = 12

# The event log's ``detail`` field is truncated to 240 characters on write
# (game_switch._safe_detail), and _teardown_detail prefixes the runtime
# identity.  This budget leaves room for that prefix, so the sanitizer cuts
# nothing. Diagnostic fields have priority over the optional ownership lists.
MAX_EVIDENCE_DETAIL_CHARS = 210

MAX_REMARK_CHARS = 120


@dataclass(frozen=True)
class PaneScopeEvidence:
    """The scope captured from one pane leader, and what it took to stop it.

    ``term_sent`` / ``kill_sent`` are the PIDs that actually received each
    signal; ``remaining`` is what was still alive when the bounded stop gave
    up.  A pane that died on TERM alone has an empty ``kill_sent``.
    """

    pane_pid: int
    pgid: int | None = None
    cgroup: str | None = None
    term_sent: tuple[int, ...] = ()
    kill_sent: tuple[int, ...] = ()
    remaining: tuple[int, ...] = ()

    def as_line(self) -> str:
        parts = [f"pid={self.pane_pid}"]
        if self.pgid is not None:
            parts.append(f"pgid={self.pgid}")
        cgroup = _cgroup_leaf(self.cgroup)
        if cgroup:
            parts.append(f"cg={cgroup}")
        if self.term_sent:
            parts.append(f"term={_pids(self.term_sent)}")
        if self.kill_sent:
            parts.append(f"kill={_pids(self.kill_sent)}")
        if self.remaining:
            parts.append(f"rem={_pids(self.remaining)}")
        return " ".join(parts)


@dataclass
class TeardownEvidence:
    """Everything one runtime teardown observed and concluded.

    ``signals`` records the escalation actually used rather than the one
    intended: a teardown that only needed TERM reads ``term``, one that had to
    escalate reads ``term,kill``.  ``confirmed`` is True only once the runtime
    was *observed* gone — a cleanup that returns without that is not evidence.
    """

    # Observed target identity: the tmux ids of what was actually stopped.
    # ``@N`` / ``$N`` ids are stable across a re-exec, so they survive the
    # teardown they describe (a window name alone does not).
    game_window_id: str | None = None
    agent_window_id: str | None = None
    session_id: str | None = None
    targets: tuple[str, ...] = ()

    # The ownership tags proven before anything was signalled.  Without this
    # the PIDs below prove nothing about *which* runtime they belonged to.
    ownership: tuple[str, ...] = ()

    pane_scopes: tuple[PaneScopeEvidence, ...] = ()

    # Escaped processes reclaimed by the post-condition sweep (Issue #1105).
    reclaimed: tuple[int, ...] = ()
    reclaim_term_sent: tuple[int, ...] = ()
    reclaim_kill_sent: tuple[int, ...] = ()

    # Escalation summary, in order: subset of ("term", "kill").
    signals: tuple[str, ...] = ()

    # What survived and why (empty means nothing survived).
    remaining: tuple[int, ...] = ()
    remaining_reason: str = ""

    # Liveness confirmation, plus how it was established (or why it was not).
    confirmed: bool = False
    probe: str = ""

    error_code: str = ""
    remarks: tuple[str, ...] = ()

    def add_remark(self, remark: str) -> None:
        """Record one bounded remark (an adapter detail or a sweep note)."""

        text = _bounded_text(remark, MAX_REMARK_CHARS)
        if text and len(self.remarks) < MAX_EVIDENCE_ENTRIES:
            self.remarks = (*self.remarks, text)

    @property
    def successful(self) -> bool:
        return self.confirmed and not self.remaining

    def render(self) -> str:
        """Render the evidence as one bounded log line.

        Unset fields are omitted, so a teardown nobody instrumented produces
        the same short line the log carried before evidence existed.
        """

        # Even with every diagnostic present, these caps plus names and
        # separators total at most 210 chars. Budget each complete, redacted
        # field; never cut the assembled line through a later diagnostic.
        segments = [f"confirmed={'yes' if self.confirmed else 'no'}"]
        if self.remaining:
            segments.append("remaining=" + _pids(self.remaining, max_chars=40))
        for name, value in (
            ("reason", self.remaining_reason),
            ("probe", self.probe),
            ("error_code", self.error_code),
        ):
            if value:
                segments.append(f"{name}={_bounded_text(value, 32)}")
        signals = [signal for signal in ("term", "kill") if signal in self.signals]
        if signals:
            segments.append("signals=" + ",".join(signals))

        def add(
            name: str, value: str, *, items: Iterable[str] | None = None,
            pids: Iterable[int] | None = None,
        ) -> None:
            available = MAX_EVIDENCE_DETAIL_CHARS - len("; ".join(segments)) - len(name) - 3
            if available <= 0:
                return
            if items is not None:
                value = _items(items, max_chars=available)
            elif pids is not None and len(value) > available:
                # Keep whole PIDs and a count marker; the signal summary above
                # still records any escalation whose per-PID detail is omitted.
                suffix = "(…)"
                value = _pids(pids, max_chars=available - len(suffix))
                value = value + suffix if value else ""
            else:
                value = _bounded_text(value, available)
            if not value:
                return
            segments.append(f"{name}={value}")

        if self.reclaimed:
            add(
                "reclaimed", _pids(self.reclaimed) + _escalation(
                    self.reclaim_term_sent, self.reclaim_kill_sent),
                pids=self.reclaimed,
            )
        if self.pane_scopes:
            add("panes", "", items=(p.as_line() for p in self.pane_scopes))
        windows = _ids(
            ("game", self.game_window_id),
            ("agent", self.agent_window_id),
            ("session", self.session_id),
        )
        if windows:
            add("windows", windows)
        if self.ownership:
            add("roles", _roles(self.ownership))
        if self.remarks:
            add("remarks", " | ".join(
                _bounded_text(remark, MAX_REMARK_CHARS) for remark in self.remarks
            ))
        return "; ".join(segments)


# --- the recorder a teardown writes into ---------------------------------
#
# The coordinator runs every adapter step in its own worker thread, and a
# thread does not inherit the parent's contextvar values, so a contextvar
# cannot carry the recorder across ``_call_adapter``.  The teardown layer
# therefore binds the recorder explicitly onto the *object* the worker thread
# and the caller both hold: the adapter instance.


def bind_recorder(adapter: object, evidence: TeardownEvidence) -> None:
    """Attach ``evidence`` to ``adapter`` as the recorder its teardown fills.

    Every :class:`~docich.tmux.Tmux` the adapter holds is told about its owner,
    so a teardown that stops windows, a session and an eval session all report
    into the same recorder.
    """

    setattr(adapter, "_docich_teardown_evidence", evidence)
    for attribute in vars(adapter).values() if hasattr(adapter, "__dict__") else ():
        bind = getattr(attribute, "bind_teardown_owner", None)
        if callable(bind):
            bind(adapter)


def adapter_recorder(adapter: object) -> TeardownEvidence | None:
    """The recorder bound to ``adapter``, or ``None`` when not recording."""

    value = getattr(adapter, "_docich_teardown_evidence", None)
    return value if isinstance(value, TeardownEvidence) else None


# --- rendering helpers ---------------------------------------------------


def _clean(text: object) -> str:
    """Collapse whitespace so one evidence field stays a single log line."""

    return " ".join(str(text or "").split())


def _bounded_text(text: object, max_chars: int) -> str:
    """Redact the complete value, then flag any character-budget omission."""

    value = redact_log_detail(_clean(text))
    return value if len(value) <= max_chars else value[:max_chars - 1] + "…"


def _cgroup_leaf(cgroup: object) -> str:
    """Keep the meaningful scope unit of a cgroup path.

    ``0::/user.slice/user-1000.slice/tmux-spawn-1234.scope`` -> ``tmux-spawn-1234``;
    a path with no usable leaf is dropped rather than dumped in full.  The raw
    ``/proc/<pid>/cgroup`` text ends with a newline, which must not end up in
    the log line.
    """

    for part in reversed([part for part in str(cgroup or "").split("/") if part.strip()]):
        part = part.strip()
        if part.startswith("tmux-spawn-"):
            return part[:-6] if part.endswith(".scope") else part
    return ""


def _pids(values: Iterable[int], *, max_chars: int | None = None) -> str:
    """Render PIDs, capping the count and flagging truncation."""

    listed = [str(int(value)) for value in values if int(value) > 0]
    shown = listed[:MAX_EVIDENCE_ENTRIES]
    def rendered() -> str:
        return ",".join(shown) + _over(len(listed), len(shown))
    while shown and max_chars is not None and len(rendered()) > max_chars:
        shown.pop()
    return rendered() if max_chars is None or len(rendered()) <= max_chars else ""


def _over(total: int, shown: int) -> str:
    return f",+{total - shown}" if total > shown else ""


def _roles(tags: Iterable[str]) -> str:
    """Condense ownership tags (``gN-hash:gen:role``) to distinct roles.

    The runtime id and generation are already carried by the event's own
    ``runtime_id`` / ``generation`` fields and by the detail prefix, so
    repeating them for every stopped object only crowds out the fields that
    actually differ per teardown.
    """

    seen: list[str] = []
    for tag in tags:
        role = tag.rsplit(":", 1)[-1]
        if role and role not in seen:
            seen.append(role)
    return ",".join(seen)


def _ids(*pairs: tuple[str, str | None]) -> str:
    return ",".join(f"{role}:{value}" for role, value in pairs if value)


def _items(items: Iterable[str], *, max_chars: int | None = None) -> str:
    listed = [redact_log_detail(item) for item in items if item]
    shown = listed[:MAX_EVIDENCE_ENTRIES]
    def rendered() -> str:
        omitted = _over(len(listed), len(shown))
        if not shown:
            omitted = omitted.lstrip(",")
        return "[" + " ".join(shown) + omitted + "]"
    while shown and max_chars is not None and len(rendered()) > max_chars:
        shown.pop()
    return rendered() if max_chars is None or len(rendered()) <= max_chars else ""


def _escalation(term: Iterable[int], kill: Iterable[int]) -> str:
    parts = []
    term_list = [int(value) for value in term if int(value) > 0]
    kill_list = [int(value) for value in kill if int(value) > 0]
    if term_list:
        parts.append(f"term={_pids(term_list)}")
    if kill_list:
        parts.append(f"kill={_pids(kill_list)}")
    return "(" + " ".join(parts) + ")" if parts else ""


def evidence_from_mapping(payload: Mapping[str, object] | None) -> TeardownEvidence:
    """Rebuild a :class:`TeardownEvidence` from a plain mapping.

    Used by callers that carry evidence through a dict channel instead of the
    object itself.  Unknown keys are ignored, so a newer field never breaks an
    older caller.
    """

    evidence = TeardownEvidence()
    if not isinstance(payload, Mapping):
        return evidence

    def ints(key: str) -> tuple[int, ...]:
        raw = payload.get(key)
        if not isinstance(raw, (list, tuple)):
            return ()
        return tuple(int(value) for value in raw if isinstance(value, int))

    def texts(key: str) -> tuple[str, ...]:
        raw = payload.get(key)
        if not isinstance(raw, (list, tuple)):
            return ()
        return tuple(str(value) for value in raw if isinstance(value, str))

    def one_int(key: str) -> int | None:
        value = payload.get(key)
        return int(value) if isinstance(value, int) else None

    def one_str(key: str) -> str | None:
        return _clean(payload.get(key)) or None

    evidence.game_window_id = one_str("game_window_id")
    evidence.agent_window_id = one_str("agent_window_id")
    evidence.session_id = one_str("session_id")
    evidence.targets = texts("targets")
    evidence.ownership = texts("ownership")
    evidence.reclaimed = ints("reclaimed")
    evidence.reclaim_term_sent = ints("reclaim_term_sent")
    evidence.reclaim_kill_sent = ints("reclaim_kill_sent")
    evidence.signals = texts("signals")
    evidence.confirmed = payload.get("confirmed") is True
    evidence.probe = _clean(payload.get("probe"))
    evidence.remaining = ints("remaining")
    evidence.remaining_reason = _clean(payload.get("remaining_reason"))
    evidence.error_code = _clean(payload.get("error_code"))
    evidence.remarks = texts("remarks")
    raw_panes = payload.get("pane_scopes")
    if isinstance(raw_panes, (list, tuple)):
        for entry in raw_panes:
            if isinstance(entry, PaneScopeEvidence):
                evidence.pane_scopes = (*evidence.pane_scopes, entry)
            elif isinstance(entry, Mapping):
                evidence.pane_scopes = (*evidence.pane_scopes, _pane_from_mapping(entry))
    return evidence


def _pane_from_mapping(entry: Mapping[str, object]) -> PaneScopeEvidence:
    def ints(key: str) -> tuple[int, ...]:
        raw = entry.get(key)
        if not isinstance(raw, (list, tuple)):
            return ()
        return tuple(int(value) for value in raw if isinstance(value, int))

    def one_int(key: str) -> int | None:
        value = entry.get(key)
        return int(value) if isinstance(value, int) else None

    return PaneScopeEvidence(
        pane_pid=one_int("pane_pid") or 0,
        pgid=one_int("pgid"),
        cgroup=_clean(entry.get("cgroup")) or None,
        term_sent=ints("term_sent"),
        kill_sent=ints("kill_sent"),
        remaining=ints("remaining"),
    )
