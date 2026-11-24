"""Per-observation contract between a foreground procedure and arbitration.

docich#3193 B: today ``None`` (not mine), ``[]`` (mine, no input) and action
lists are overloaded, so every new hold needs a matching mem key the arbiter
already knows. ``FlowResult`` makes the procedure's claim explicit instead:

- ``owner``         the procedure that took the observation.
- ``disposition``   'input' (send ``actions``), 'hold' (mine, no input),
                    'release' (reservation returned; the foreground may
                    re-drive) or 'commit_pending' (settled by the
                    observation layer, e.g. observe_sortie_transition).
- ``reason``        the procedure's own justification.
- ``retry``         {'used', 'limit'} when the result spent a bounded slot.
- ``permitted_recovery``  'resend' | 'reobserve' | 'reconfirm' | 'none'.

Stage 1 migrates only the departure-request procedure
(``map._departure_request_step``). Its arbiter still receives bare actions,
so this type carries a plain list and stays a pure contract — no behavior
change. Wider migration converts one procedure at a time and diffs old/new
inputs and state transitions.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FlowResult:
    owner: str
    disposition: str          # 'input' | 'hold' | 'release' | 'commit_pending'
    actions: list = field(default_factory=list)
    reason: str = ''
    retry: dict | None = None
    permitted_recovery: str = 'none'
