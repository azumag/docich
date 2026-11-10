"""N04a: News PAPER point-in-time replay foundation.

Manifest loading + validation, injected logical clock, PIT input view,
A/B baseline interface, leakage fixtures. No real time/network/LLM.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

MODE = "paper"
LIVE_ELIGIBLE = False

MAX_HEADER_BYTES = 65536
MAX_CHUNK_BYTES = 1024 * 1024
MAX_CHUNKS = 256
MAX_RECORDS_PER_CHUNK = 2000
MAX_TOTAL_RECORDS = 512000
MAX_RECORD_BYTES = 512
MAX_DEPTH = 16
MAX_FILENAME_LEN = 128


class ReplayError(Exception):
    pass


@dataclass
class Chunk:
    seq: int
    filename: str
    sha256: str
    record_count: int
    records: list = field(default_factory=list)


@dataclass
class ManifestHeader:
    schema_version: str
    run_id: str
    scenario_id: str
    versions: dict
    clock: dict
    arm: str
    chunks: list


@dataclass
class Manifest:
    header: ManifestHeader
    chunks: list[Chunk] = field(default_factory=list)

    @property
    def ingest_order(self):
        out = []
        for c in self.chunks:
            for r in c.records:
                out.append((r.get("db"), r.get("observation_id")))
        return out


def _depth(obj, d=0):
    if d > MAX_DEPTH:
        return d
    if isinstance(obj, dict):
        return max([_depth(v, d + 1) for v in obj.values()] + [d])
    if isinstance(obj, list):
        return max([_depth(v, d + 1) for v in obj] + [d])
    return d


def load_manifest(data: dict, chunk_loader: Callable[[str], bytes] | None = None) -> Manifest:
    # header size check
    header_bytes = json.dumps(data.get("header", {})).encode()
    if len(header_bytes) > MAX_HEADER_BYTES:
        raise ReplayError("header too large")
    h = data.get("header")
    if not isinstance(h, dict):
        raise ReplayError("missing header")
    for k in ("schema_version", "run_id", "scenario_id", "versions", "clock", "arm", "chunks"):
        if k not in h:
            raise ReplayError(f"missing header field {k}")
    chunks_meta = h["chunks"]
    if not (1 <= len(chunks_meta) <= MAX_CHUNKS):
        raise ReplayError("chunks count out of range")
    # chunks array meta
    seqs = []
    filenames = set()
    for cm in chunks_meta:
        if not isinstance(cm, dict):
            raise ReplayError("bad chunk meta")
        if not (1 <= cm.get("seq", 0) <= MAX_CHUNKS):
            raise ReplayError("bad seq")
        seqs.append(cm["seq"])
        fn = cm.get("filename")
        if not isinstance(fn, str) or len(fn) > MAX_FILENAME_LEN or len(fn) == 0:
            raise ReplayError("bad filename")
        if fn in filenames:
            raise ReplayError("duplicate filename")
        filenames.add(fn)
        sha = cm.get("sha256")
        if not (isinstance(sha, str) and len(sha) == 64 and all(c in "0123456789abcdef" for c in sha.lower())):
            raise ReplayError("bad sha256")
        rc = cm.get("record_count")
        if not (isinstance(rc, int) and 1 <= rc <= MAX_RECORDS_PER_CHUNK):
            raise ReplayError("bad record_count")
    if seqs != sorted(set(seqs)) or len(set(seqs)) != len(seqs):
        raise ReplayError("seq not strictly increasing unique")
    # load chunks
    chunks = []
    total = 0
    seen_io = set()
    prev_seq_db: dict[str, int] = {}
    for cm in sorted(chunks_meta, key=lambda x: x["seq"]):
        raw = chunk_loader(cm["filename"]) if chunk_loader else None
        if raw is None:
            recs = cm.get("records", [])
            raw_bytes = json.dumps(recs).encode()
        else:
            if len(raw) > MAX_CHUNK_BYTES or len(raw) == 0:
                raise ReplayError("chunk size")
            recs = json.loads(raw)
            if not isinstance(recs, list):
                raise ReplayError("chunk not list")
        if not (1 <= len(recs) <= MAX_RECORDS_PER_CHUNK):
            raise ReplayError("chunk record count")
        if len(recs) != cm["record_count"]:
            raise ReplayError("record_count mismatch")
        # sha check
        if raw is not None:
            if hashlib.sha256(raw).hexdigest() != cm["sha256"].lower():
                raise ReplayError("sha mismatch")
        # record size/depth
        for r in recs:
            rb = json.dumps(r).encode()
            if len(rb) > MAX_RECORD_BYTES:
                raise ReplayError("record too large")
            if _depth(r) > MAX_DEPTH:
                raise ReplayError("depth too deep")
            # ingest order unique
            key = (r.get("db"), r.get("observation_id"))
            if key in seen_io:
                raise ReplayError("duplicate ingest_order")
            seen_io.add(key)
            db = r.get("db")
            seq = r.get("ingest_sequence")
            if db in prev_seq_db and not (seq > prev_seq_db[db]):
                raise ReplayError("ingest_sequence not increasing")
            prev_seq_db[db] = seq
        total += len(recs)
        if total > MAX_TOTAL_RECORDS:
            raise ReplayError("total records exceeded")
        chunks.append(Chunk(seq=cm["seq"], filename=cm["filename"], sha256=cm["sha256"], record_count=cm["record_count"], records=recs))
    # strict increasing seq across
    seqs_sorted = [c.seq for c in chunks]
    if seqs_sorted != sorted(seqs_sorted) or len(set(seqs_sorted)) != len(seqs_sorted):
        raise ReplayError("seq order")
    header = ManifestHeader(
        schema_version=h["schema_version"],
        run_id=h["run_id"],
        scenario_id=h["scenario_id"],
        versions=h["versions"],
        clock=h["clock"],
        arm=h["arm"],
        chunks=chunks_meta,
    )
    return Manifest(header=header, chunks=chunks)


class InjectedClock:
    def __init__(self, fence_at):
        self.fence_at = fence_at
        self._now = fence_at

    def now(self):
        return self._now

    def advance(self, t):
        if t < self._now:
            raise ReplayError("clock cannot go back")
        self._now = t

    def fence(self, t):
        return t <= self.fence_at


class PitInputView:
    def __init__(self, clock: InjectedClock):
        self.clock = clock

    def visible(self, obs, cutoff=None):
        cutoff = cutoff if cutoff is not None else self.clock.fence_at
        # obs must have timestamp/available_at
        ts = None
        for k in ("available_at", "timestamp", "observed_at", "bar_close"):
            if k in obs:
                ts = obs[k]
                break
        if ts is None:
            return True
        return ts <= cutoff

    def filter(self, items, cutoff=None):
        return [x for x in items if self.visible(x, cutoff)]


# Baseline interface
ARM_A = "A"
ARM_B = "B"


@dataclass
class BaselineConfig:
    initial_capital: Any = None
    universe: Any = None
    warmup: Any = None
    window: Any = None
    risk: Any = None
    fee: Any = None
    slippage: Any = None
    latency: Any = None


class BaselineArm:
    def __init__(self, name, config: BaselineConfig):
        self.name = name
        self.config = config

    def price_only(self):
        return self.name == ARM_A

    def apply_news_filter(self, signal):
        return self.name == ARM_B


def make_baseline_a(config):
    return BaselineArm(ARM_A, config)


def make_baseline_b(config):
    return BaselineArm(ARM_B, config)


# Leakage fixtures
def leakage_post_cutoff_doc(clock_fence):
    return {"available_at": clock_fence + 1, "type": "post_cutoff"}


def leakage_unconfirmed_bar():
    return {"confirmed": False, "type": "unconfirmed_bar"}


def leakage_correction():
    return {"corrected": True, "type": "correction"}


def leakage_universe_change():
    return {"universe_changed": True, "type": "universe_change"}


def leakage_forming_bar():
    return {"forming": True, "type": "forming_bar"}


LEAKAGE_FIXTURES = {
    "post_cutoff": leakage_post_cutoff_doc,
    "unconfirmed_bar": leakage_unconfirmed_bar,
    "correction": leakage_correction,
    "universe_change": leakage_universe_change,
    "forming_bar": leakage_forming_bar,
}

# aliases
ManifestLoader = load_manifest
load_manifest_file = None


def load_manifest_from_file(path, chunk_loader=None):
    with open(path) as f:
        data = json.load(f)
    return load_manifest(data, chunk_loader=chunk_loader)


__all__ = [
    "MODE", "LIVE_ELIGIBLE", "ReplayError", "Chunk", "Manifest", "ManifestHeader",
    "load_manifest", "InjectedClock", "PitInputView", "ARM_A", "ARM_B",
    "BaselineConfig", "BaselineArm", "make_baseline_a", "make_baseline_b",
    "LEAKAGE_FIXTURES",
]
