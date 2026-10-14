"""Stable hot-load trampoline and generation publication for Hanjuku (#1469).

The Hanjuku bot runs as a fresh subprocess on every observation
(``CommandBrain`` -> ``brains/hanjuku/bot.py``), so a canonical deploy is read
by the very next decision.  The agent loop and the retro corner are long-lived
Python processes instead: they imported ``hanjuku_run``/``hanjuku_bot`` once and
keep executing that module object until they are restarted, and neither
``importlib.reload`` nor a plain file distribution updates an already running
``RetroCornerManager._wait_hanjuku`` frame.  A deploy that changes the terminal
logic (the assessment-required suppression of the 300 s ``screen_stalled``
terminal) can therefore leave the *bot* on the new code and the two *observers*
on the old one inside the same live run.

This module is the stable trampoline both observers pass through on every
observation.  It

* resolves the deployed Hanjuku logic generation from the logic sources,
* re-uses the process' canonical modules while they are still that generation,
* otherwise fresh-loads a *versioned copy* of the whole logic set into a
  generation-scoped module namespace, so an already running frame never sees a
  half-reloaded module and no process restart is required,
* publishes, durably and per runtime, the logic generation each observer
  adopted (:func:`publish`), and
* exposes the fail-closed gate :func:`generation_gate`: a MIXED generation must
  not take the new-policy-dependent stall teardown.

Nothing here changes runtime identity, the input gate, RetroArch, save state or
the distribution/audio processes: the trampoline only decides which copy of the
per-observation Hanjuku logic runs, and records that decision.

The generation is a SHA-256 over the source bytes of :data:`LOGIC_MODULES`, so
it changes exactly when that terminal/policy boundary changes.  The Hanjuku
policy itself (``hanjuku_policy``) is *not* part of the set: it is executed in
the per-observation bot subprocess, which is always fresh.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import os
import sys
import time
import types
from pathlib import Path
from typing import Mapping

#: The modules whose source defines the Hanjuku terminal/policy boundary that a
#: long-lived observer executes.  Dependency order: later entries import the
#: earlier ones.
LOGIC_MODULES = ('hanjuku_bot', 'hanjuku_run')

#: Runtime identity a publication and its reader must agree on.
IDENTITY_KEYS = ('game', 'runtime_id', 'generation', 'lease_id')

#: The two observers that must agree before the run is torn down.
ROLES = ('agent', 'corner')

RUN_GAME = 'hanjuku-hero'
RECORD_SCHEMA = 1
RECORD_NAME = 'hanjuku_generation_{role}.json'
RECORD_LIMIT = 4096

#: ``screen_stalled`` is the terminal the new Hanjuku policy suppresses while a
#: discharge assessment is pending, so it is the terminal whose teardown must
#: not be taken on an unproven (mixed) code generation.  ``game_over`` and the
#: existing ``input_stalled`` bound are policy-independent and stay ungated.
NEW_POLICY_TERMINAL_REASONS = frozenset({'screen_stalled'})

#: Re-publish the same generation at most this often.  The gate compares
#: generations, so a process that keeps running the same code stays valid.
REFRESH_SECONDS = 30.0

_MISSING = object()


def logic_path(name: str) -> Path:
    """Source path of one logic module (same package directory as this module)."""
    return Path(__file__).resolve().with_name(f'{name}.py')


def logic_sources() -> dict:
    return {name: logic_path(name) for name in LOGIC_MODULES}


def digest_sources(sources: Mapping) -> str:
    """Stable digest over ``{name: path}``; order-independent, path-independent."""
    digest = hashlib.sha256()
    for name in sorted(sources):
        digest.update(str(name).encode('utf-8'))
        digest.update(b'\0')
        digest.update(Path(sources[name]).read_bytes())
        digest.update(b'\0')
    return digest.hexdigest()


def deployed_generation() -> str:
    """Generation of the Hanjuku logic currently deployed on disk."""
    return digest_sources(logic_sources())


def logic_generation() -> str:
    """Public alias used by the logic modules to record their own generation."""
    return deployed_generation()


class LogicHandle:
    """One generation of the Hanjuku logic, as loaded in this process."""

    __slots__ = ('generation', 'modules')

    def __init__(self, generation: str, modules: Mapping):
        self.generation = generation
        self.modules = dict(modules)


_HANDLE: LogicHandle | None = None


def _canonical(generation: str) -> LogicHandle | None:
    """The process' own modules, when they really are ``generation``.

    Each logic module records the generation it loaded as ``LOGIC_GENERATION``,
    so this comparison is independent of when this module was imported.
    """
    modules = {}
    for name in LOGIC_MODULES:
        try:
            module = importlib.import_module(f'docich.{name}')
        except ImportError:
            return None
        if getattr(module, 'LOGIC_GENERATION', None) != generation:
            return None
        modules[name] = module
    return LogicHandle(generation, modules)


def _blank_module(full: str, path: Path):
    module = types.ModuleType(full)
    module.__file__ = str(path)
    module.__package__ = full.rpartition('.')[0]
    module.__loader__ = None
    module.__spec__ = importlib.util.spec_from_file_location(full, path)
    return module


def _fresh(generation: str, sources: Mapping | None = None) -> LogicHandle:
    """Load a versioned, private copy of the whole logic set.

    Each module is installed into ``sys.modules`` under its real dotted name
    *while* it executes so its cross-imports bind to the fresh copies, and the
    previous entries are restored afterwards.  Callers keep using the returned
    handle, never ``sys.modules``, so the swap is invisible outside this call.
    The sources are compiled here instead of through the standard loader: a
    redeployed file with the same size in the same second must not be served
    from a stale ``__pycache__`` entry.
    """
    sources = logic_sources() if sources is None else dict(sources)
    generation = digest_sources(sources)
    modules: dict = {}
    saved: dict = {}
    try:
        for name in LOGIC_MODULES:
            full = f'docich.{name}'
            path = Path(sources[name])
            code = compile(path.read_bytes(), str(path), 'exec')
            module = _blank_module(full, path)
            if full not in saved:
                saved[full] = sys.modules.get(full, _MISSING)
            sys.modules[full] = module
            try:
                exec(code, module.__dict__)
            except BaseException:
                previous = saved[full]
                if previous is _MISSING:
                    sys.modules.pop(full, None)
                else:
                    sys.modules[full] = previous
                raise
            modules[name] = module
    finally:
        for full, previous in saved.items():
            if previous is _MISSING:
                sys.modules.pop(full, None)
            else:
                sys.modules[full] = previous
    return LogicHandle(generation, modules)


def handle() -> LogicHandle:
    """The logic this process will execute for the next observation."""
    global _HANDLE
    deployed = deployed_generation()
    current = _HANDLE
    if current is not None and current.generation == deployed:
        return current
    canonical = _canonical(deployed)
    _HANDLE = canonical if canonical is not None else _fresh(deployed)
    return _HANDLE


def adopted_generation() -> str:
    """Generation of the logic this process actually loaded."""
    return handle().generation


def module(name: str):
    """The current-generation module object (``hanjuku_run`` / ``hanjuku_bot``)."""
    return handle().modules[name]


def _logic(attribute: str, *args, **kwargs):
    return getattr(module('hanjuku_run'), attribute)(*args, **kwargs)


# --- trampoline entry points ------------------------------------------------

def enabled(game):
    return _logic('enabled', game)


def runtime_identity(spec):
    return _logic('runtime_identity', spec)


def observe(*args, **kwargs):
    return _logic('observe', *args, **kwargs)


def terminal(*args, **kwargs):
    return _logic('terminal', *args, **kwargs)


def load(*args, **kwargs):
    return _logic('load', *args, **kwargs)


def action_sent(*args, **kwargs):
    return _logic('action_sent', *args, **kwargs)


def event(*args, **kwargs):
    return _logic('event', *args, **kwargs)


# --- durable publication ----------------------------------------------------

def _adapter_error():
    from .adapters.base import AdapterError

    return AdapterError


def _is_digest(value) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(character in '0123456789abcdef' for character in value))


def clean_identity(identity) -> dict:
    AdapterError = _adapter_error()
    keys = set(identity) if isinstance(identity, Mapping) else set()
    if not isinstance(identity, Mapping) or keys - set(IDENTITY_KEYS):
        raise AdapterError('Hanjuku logic generation identity is malformed')
    clean = {key: identity.get(key) for key in IDENTITY_KEYS}
    if (clean['game'] != RUN_GAME
            or not isinstance(clean['runtime_id'], str) or not clean['runtime_id']
            or type(clean['generation']) is not int or clean['generation'] < 1
            or not isinstance(clean['lease_id'], str) or not clean['lease_id']):
        raise AdapterError('Hanjuku logic generation identity is malformed')
    return clean


def record_path(runtime_dir, role: str) -> Path:
    return Path(runtime_dir) / RECORD_NAME.format(role=role)


_REFRESH: dict = {}


def publish(runtime_dir, identity, role: str, *, now=None, refresh: bool = False):
    """Durably record the logic generation this observer adopted.

    Returns the written record, or ``None`` when an identical, still fresh
    record was already published by this process.
    """
    AdapterError = _adapter_error()
    if role not in ROLES:
        raise AdapterError(f'unknown Hanjuku observer role: {role!r}')
    identity = clean_identity(identity)
    path = record_path(runtime_dir, role)
    generation = adopted_generation()
    at = time.time() if now is None else now
    key = (str(path), role, identity['generation'])
    cache = _REFRESH
    if not refresh:
        previous = cache.get(key)
        if previous is not None and previous[0] == generation and at - previous[1] < REFRESH_SECONDS:
            return None
    if path.is_symlink():
        raise AdapterError('Hanjuku logic generation record may not be a symlink')
    from .game_switch import atomic_write_json

    record = {'schema': RECORD_SCHEMA, **identity, 'role': role,
              'logic_generation': generation,
              'bot_version': getattr(module('hanjuku_bot'), 'BOT_VERSION', None),
              'at': at, 'pid': os.getpid()}
    atomic_write_json(path, record)
    cache[key] = (generation, at)
    return record


def publish_observation(obs, role: str, *, now=None):
    """Publish the observer's generation from a trampolined observation."""
    meta = getattr(obs, 'meta', None)
    if not isinstance(meta, dict):
        return None
    runtime_dir = meta.get('runtime_dir')
    state = meta.get('hanjuku')
    if not isinstance(runtime_dir, str) or not runtime_dir or not isinstance(state, dict):
        return None
    identity = {key: state.get(key) for key in IDENTITY_KEYS}
    return publish(Path(runtime_dir), identity, role, now=now)


def adoption(runtime_dir, identity, role: str):
    """The published record for ``role``, bound to this runtime identity."""
    AdapterError = _adapter_error()
    if role not in ROLES:
        raise AdapterError(f'unknown Hanjuku observer role: {role!r}')
    identity = clean_identity(identity)
    from .retroarch_boundary import read_record

    record = read_record(record_path(runtime_dir, role), limit=RECORD_LIMIT)
    if not record:
        return None
    if (record.get('schema') != RECORD_SCHEMA or record.get('role') != role
            or not _is_digest(record.get('logic_generation'))):
        raise AdapterError('Hanjuku logic generation record is unreadable')
    if any(record.get(key) != value for key, value in identity.items()):
        raise AdapterError('Hanjuku logic generation record identity mismatch')
    return record


def consistency(runtime_dir, identity) -> dict:
    """Whether the two observers of this runtime share one logic generation.

    ``consistent`` - both published the same generation (the boundary holds).
    ``mixed``      - both published, but different generations (fail closed).
    ``unestablished`` - at least one side never published: the hot-load
    boundary is not in use for this runtime, so the pre-existing semantics
    stand unchanged.
    """
    values = {role: (adoption(runtime_dir, identity, role) or {}).get('logic_generation')
              for role in ROLES}
    if values['agent'] is None or values['corner'] is None:
        state = 'unestablished'
    elif values['agent'] == values['corner']:
        state = 'consistent'
    else:
        state = 'mixed'
    return {'schema': RECORD_SCHEMA, 'state': state, 'agent': values['agent'],
            'corner': values['corner'], 'deployed': deployed_generation()}


def generation_gate(runtime_dir, identity, reason):
    """Return ``(teardown_allowed, status)`` for a Hanjuku terminal reason.

    A mixed generation must not take the new-policy-dependent stall teardown:
    the observers do not agree on the code that decided it, and tearing the run
    down is irreversible while holding it is not.  An unestablished boundary
    leaves the pre-existing behaviour untouched.
    """
    status = consistency(runtime_dir, identity)
    if reason in NEW_POLICY_TERMINAL_REASONS and status['state'] == 'mixed':
        return False, status
    return True, status
