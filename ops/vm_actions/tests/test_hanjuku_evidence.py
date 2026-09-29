"""Offline stdlib tests: synthetic pixels/state only; no VM/ROM/SSH/real secrets."""
from __future__ import annotations

import ast
import copy
from contextlib import redirect_stdout, redirect_stderr
import fcntl
import hashlib
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import zlib

MODULE = Path(__file__).resolve().parents[1] / "hanjuku_evidence.py"
spec = importlib.util.spec_from_file_location("hanjuku_evidence_test_target", MODULE)
e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e)
receive_spec = importlib.util.spec_from_file_location("hanjuku_receiver_test_target", MODULE.with_name("receive_hanjuku_evidence.py"))
r = importlib.util.module_from_spec(receive_spec)
receive_spec.loader.exec_module(r)
RID = "g7-1234abcd"


def png():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    pixels = b"\x22\x44\x66" * (256 * 224)
    raw = b"".join(b"\0" + pixels[y*768:(y+1)*768] for y in range(224))
    encoded = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 256, 224, 8, 2, 0, 0, 0))
               + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    return encoded, hashlib.sha256(pixels).hexdigest()


def run_state(**kw):
    return {"schema": 1, "game": "hanjuku-hero", "runtime_id": RID, "generation": 7,
            "lease_id": "lease-synthetic", "frame_sha256": png()[1],
            "terminal_reason": "game_over", "name_entered": True, "gameplay_seen": True,
            "phase": "title", "title_count": 3, "title_since": 10,
            "observed_monotonic": 12, "observed_at": 12345, "bot_version": "fixture-v1", **kw}


def canonical(**kw):
    return {"schema_version": 2, "phase": "idle", "active": None, "candidate": None,
            "previous": None, "retiring": [], "operation": None, "request_id": None,
            "revision": 9, "next_generation": 9, **kw}


INVALID_TERMINAL = [
    {"terminal_reason": None}, {"terminal_reason": "manual_stop"}, {"title_count": 2},
    {"title_count": True}, {"observed_monotonic": 11.9}, {"observed_at": float("nan")},
    {"frame_sha256": "bad"}, {"phase": "field"}, {"gameplay_seen": False},
    {"name_entered": False}, {"generation": 8}, {"generation": True},
    {"lease_id": None}, {"lease_id": ""}, {"game": "sorengame"}, {"schema": True},
    {"actions_sent": -1}, {"terminal_reason": "screen_stalled", "unchanged_seconds": 299},
]


class EvidenceFixture:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / "state"
        self.run = self.state / "runtimes" / RID
        (self.run / "hanjuku_frames").mkdir(parents=True)
        (self.state / "locks").mkdir()
        (self.state / "locks" / "game-switch.lock").touch()
        (self.state / "game_switch.json").write_bytes(e._dump(canonical()))
        (self.run / "hanjuku_run.json").write_bytes(e._dump(run_state()))
        image, digest = png()
        for name in ("frame-000.png", "decision-000.png"):
            (self.run / "hanjuku_frames" / name).write_bytes(image)
        (self.run / "hanjuku_events.previous.jsonl").write_bytes(e._dump({"event": "action_plan", "decision_id": "d1"}))
        (self.run / "hanjuku_events.jsonl").write_bytes(e._dump({
            "event": "input_sent", "decision_id": "d1", "frame_sha256": digest, "bot_version": "fixture-v2"}))
        (self.run / "hanjuku_decisions.jsonl").write_bytes(e._dump({
            "event": "decision", "decision_id": "d1", "decision": "battle_melee", "frame_sha256": digest}))

    def pack(self):
        return e.build_archive(*e.snapshot(self.state, RID))

    def inspect(self):
        archive = zipfile.ZipFile(io.BytesIO(self.pack()))
        self.addCleanup(archive.close)
        return archive, json.loads(archive.read("manifest.json"))

class EvidenceTests(EvidenceFixture, unittest.TestCase):
    def test_manifest_and_sha_domains_and_no_runtime_writes(self):
        before = {p: p.read_bytes() for p in self.state.rglob("*") if p.is_file()}
        archive, manifest = self.inspect()
        self.assertEqual(manifest["identity"]["runtime_id"], RID)
        self.assertFalse(manifest["history_complete"])
        self.assertEqual(manifest["bot_versions"], ["fixture-v1", "fixture-v2"])
        self.assertEqual(manifest["missing_frame_sha256"], [])
        name = "hanjuku_frames/frame-000.png"
        self.assertEqual(manifest["files"][name]["rgb_sha256"], png()[1])
        self.assertNotEqual(manifest["files"][name]["source_file_sha256"], png()[1])
        for name, meta in manifest["files"].items():
            self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), meta["export_file_sha256"])
        self.assertTrue(all(p.read_bytes() == data for p, data in before.items()))
        self.assertIn(b"action_plan", archive.read("hanjuku_events.previous.jsonl"))
        self.assertIn(b"input_sent", archive.read("hanjuku_events.jsonl"))

    def test_terminal_rejections(self):
        for values in INVALID_TERMINAL:
            with self.subTest(values=values), self.assertRaises(e.EvidenceError):
                e.terminal_identity(run_state(**values), RID)

    def test_stall_accepted(self):
        self.assertTrue(e.terminal_identity(run_state(terminal_reason="screen_stalled", unchanged_seconds=300), RID))

    def test_native_terminal_parity(self):
        # Execute the actual native pure load/terminal functions, not a copied
        # fake validator. AST selection avoids importing the gameplay/bot stack.
        source = MODULE.parents[2] / "src/docich/hanjuku_run.py"
        tree = ast.parse(source.read_text())
        names = {"RUN_FILE", "TERMINAL_REASONS", "STALL_SECONDS"}
        nodes = [n for n in tree.body if
                 (isinstance(n, ast.FunctionDef) and n.name in {"load", "terminal"}) or
                 (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in n.targets))]
        self.assertEqual(len(nodes), 5)
        native = {"Path": Path, "math": math, "AdapterError": ValueError,
                  "read_record": lambda path: json.loads(path.read_bytes())}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), native)
        identity = e.terminal_identity(run_state(), RID)
        for index, state in enumerate([run_state(), run_state(terminal_reason="screen_stalled", unchanged_seconds=300),
                      *(run_state(**v) for v in INVALID_TERMINAL)]):
            (self.run / "hanjuku_run.json").write_text(json.dumps(state))
            try:
                native_ok = native["terminal"](self.run, identity) is not None
            except (ValueError, TypeError):
                native_ok = False
            try:
                exported = bool(e.terminal_identity(state, RID))
            except e.EvidenceError:
                exported = False
            with self.subTest(state=state):
                # Export is intentionally stricter for legacy schema/no lease.
                self.assertFalse(exported and not native_ok)
                if index < 2:
                    self.assertTrue(exported and native_ok)

    def test_bad_runtime_selector(self):
        for value in ("latest", "../g7-1234abcd", "g7-1234abcd/../x", "g07-1234abcd", "g7-1234abcg"):
            with self.subTest(value=value), self.assertRaisesRegex(e.EvidenceError, "invalid_runtime_id"):
                e.snapshot(self.state, value)

    def test_active_or_unverified_canonical(self):
        for value in [canonical(phase="ready", active={"runtime_id": RID, "generation": 7}),
                      canonical(retiring=[{"runtime_id": RID, "generation": 7}]),
                      canonical(phase="starting"), {}, canonical(operation="stop"),
                      canonical(candidate={"runtime_id": RID}), canonical(retiring=None)]:
            (self.state / "game_switch.json").write_bytes(e._dump(value))
            with self.subTest(value=value), self.assertRaises(e.EvidenceError):
                self.pack()

    def test_other_active_generation(self):
        (self.state / "game_switch.json").write_bytes(e._dump(canonical(
            phase="ready", active={"runtime_id": "g8-1234abcd", "generation": 8})))
        self.assertTrue(self.pack())

    def test_busy_lock_does_not_wait(self):
        with (self.state / "locks" / "game-switch.lock").open() as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(e.EvidenceError, "switch_busy"):
                self.pack()

    def test_linked_files_rejected(self):
        for name in ("hanjuku_run.json", "hanjuku_events.jsonl", "hanjuku_frames/frame-000.png"):
            path = self.run / name
            original = path.read_bytes()
            outside = self.root / "outside"
            outside.write_bytes(original)
            for kind in ("symbolic", "hard"):
                path.unlink()
                path.symlink_to(outside) if kind == "symbolic" else os.link(outside, path)
                with self.subTest(name=name, kind=kind), self.assertRaises((e.EvidenceError, OSError)):
                    self.pack()
            path.unlink()
            path.write_bytes(original)

    def test_symlinked_directory_rejected(self):
        frames = self.run / "hanjuku_frames"
        frames.rename(self.run / "original")
        frames.symlink_to(self.run / "original", target_is_directory=True)
        with self.assertRaises(OSError):
            self.pack()

    def test_fifo_rejected_without_blocking(self):
        path = self.run / "hanjuku_events.jsonl"
        path.unlink()
        os.mkfifo(path)
        with self.assertRaisesRegex(e.EvidenceError, "unsafe_file"):
            self.pack()

    def test_oversized_source(self):
        with (self.run / "hanjuku_events.jsonl").open("wb") as stream:
            stream.truncate(e.MAX_FILE + 1)
        with self.assertRaisesRegex(e.EvidenceError, "file_too_large"):
            self.pack()

    def test_source_change(self):
        original, count = e._read, 0
        def read(parent, name, limit, **kw):
            nonlocal count
            raw = original(parent, name, limit, **kw)
            if name == "hanjuku_events.jsonl":
                count += 1
                if count == 2:
                    return raw + b"\n"
            return raw
        with patch.object(e, "_read", side_effect=read), self.assertRaisesRegex(e.EvidenceError, "source_changed"):
            self.pack()

    def test_budget_and_frame_count(self):
        with patch.object(e, "MAX_TOTAL", 1), self.assertRaisesRegex(e.EvidenceError, "snapshot_budget_exceeded"):
            self.pack()
        with patch.object(e, "MAX_FRAMES", 1), self.assertRaisesRegex(e.EvidenceError, "frame_count_limit"):
            self.pack()
        with patch.object(e, "MAX_RECORDS", 1), self.assertRaisesRegex(e.EvidenceError, "record_count_limit"):
            self.pack()

    def test_sensitive_and_unknown_files_excluded(self):
        for name in (".env", "save.srm", "game.sfc", "hanjuku_narration.jsonl", "hanjuku_commentary.jsonl"):
            (self.run / name).write_text("DO_NOT_EXPORT")
        with (self.run / "hanjuku_decisions.jsonl").open("ab") as stream:
            stream.write(e._dump({"event": "decision", "nested": {"api_key": "DO_NOT_EXPORT", "prompt": "DO_NOT_EXPORT"}}))
        archive, _ = self.inspect()
        self.assertNotIn(b"DO_NOT_EXPORT", b"".join(archive.read(n) for n in archive.namelist()))

    def test_missing_frames_rotated_logs_and_malformed_tail(self):
        for p in (self.run / "hanjuku_frames").iterdir():
            p.unlink()
        (self.run / "hanjuku_events.previous.jsonl").unlink()
        with (self.run / "hanjuku_decisions.jsonl").open("ab") as stream:
            stream.write(b'{"partial":')
        archive, manifest = self.inspect()
        self.assertIn("hanjuku_events.previous.jsonl", manifest["missing"])
        self.assertEqual(manifest["missing_frame_sha256"], [png()[1]])
        meta = manifest["files"]["hanjuku_decisions.jsonl"]
        self.assertEqual(meta["invalid_lines"], [2])
        self.assertFalse(meta["trailing_newline"])
        self.assertEqual(len(archive.read("hanjuku_decisions.jsonl").splitlines()), 2)

    def test_cross_lease_rejects_entire_archive(self):
        (self.run / "hanjuku_events.jsonl").write_bytes(e._dump({"event": "input_sent", "lease_id": "other"}))
        with self.assertRaisesRegex(e.EvidenceError, "record_identity_mismatch"):
            self.pack()

    def test_duplicate_nonfinite_json_rejected(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1e9999}'):
            with self.subTest(raw=raw), self.assertRaises(e.EvidenceError):
                e._json(raw)

    def test_bad_png(self):
        raw, _ = png()
        for changed in (raw[:-1], raw + b"hidden", raw[:40] + b"bad" + raw[43:]):
            with self.subTest(), self.assertRaises(e.EvidenceError):
                e._png(changed)

    def test_private_output_no_overwrite(self):
        directory = self.root / "private"
        directory.mkdir(mode=0o700)
        out = directory / "evidence.cms"
        e.write_private(out, b"encrypted")
        self.assertEqual(out.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            e.write_private(out, b"overwrite")
        self.assertEqual(out.read_bytes(), b"encrypted")
        directory.chmod(0o755)
        with self.assertRaisesRegex(e.EvidenceError, "output_directory_not_private"):
            e.write_private(directory / "other", b"encrypted")

    def test_receiver_checks_manifest_hashes(self):
        clear = self.pack()
        self.assertEqual(r.verify_archive(clear)["identity"]["runtime_id"], RID)
        original = zipfile.ZipFile(io.BytesIO(clear))
        for mode in ("path", "hash", "duplicate", "symlink"):
            with self.subTest(mode=mode):
                output = io.BytesIO()
                with zipfile.ZipFile(output, "w") as archive:
                    for entry in original.infolist():
                        data = original.read(entry.filename)
                        if mode == "hash" and entry.filename == "hanjuku_events.jsonl":
                            data += b"\n"
                        archive.writestr(copy.copy(entry), data)
                    if mode in ("path", "symlink"):
                        entry = zipfile.ZipInfo("../outside" if mode == "path" else "hanjuku_frames/frame-999.png")
                        entry.external_attr = (0o100600 if mode == "path" else 0o120777) << 16
                        entry.compress_type = zipfile.ZIP_DEFLATED
                        archive.writestr(entry, b"payload")
                    if mode == "duplicate":
                        import warnings
                        with warnings.catch_warnings():
                            warnings.simplefilter("ignore", UserWarning)
                            archive.writestr("manifest.json", original.read("manifest.json"))
                with self.assertRaises(r.evidence.EvidenceError):
                    r.verify_archive(output.getvalue())
        original.close()

    def test_cli_errors_are_fixed_codes(self):
        path = self.root / "DO_NOT_PRINT"
        path.write_bytes(b"-----BEGIN PRIVATE KEY-----\nDO_NOT_PRINT\n")
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = e.main(["--state-dir", str(self.state), "--runtime-id", RID,
                             "--recipient", str(path), "--output", str(self.root / "out")])
        self.assertEqual(result, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "evidence export rejected: invalid_recipient\n")


class CryptoTests(EvidenceFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keys = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.keys.cleanup)
        cls.key = Path(cls.keys.name) / "private.pem"
        cls.cert = Path(cls.keys.name) / "recipient.pem"
        result = subprocess.run(["/usr/bin/openssl", "req", "-x509", "-newkey", "rsa:3072", "-nodes",
                                 "-keyout", str(cls.key), "-out", str(cls.cert), "-days", "1",
                                 "-subj", "/CN=synthetic-evidence-test"], capture_output=True, timeout=30)
        if result.returncode:
            raise RuntimeError("OpenSSL test certificate generation failed")

    def test_real_crypto_roundtrip_and_tamper(self):
        clear = self.pack()
        encrypted = e.encrypt(clear, self.cert.read_bytes())
        self.assertNotIn(b"hanjuku_run.json", encrypted)
        args = ["cms", "-decrypt", "-binary", "-inform", "DER", "-recip", str(self.cert), "-inkey", str(self.key)]
        self.assertEqual(e._openssl(args, encrypted), clear)
        corrupted = bytearray(encrypted)
        corrupted[-1] ^= 1
        with self.assertRaisesRegex(e.EvidenceError, "crypto_failed"):
            e._openssl(args, bytes(corrupted))

    def test_recipient_cli_verifies_before_writing(self):
        directory = self.root / "receive"
        directory.mkdir(mode=0o700)
        ciphertext = directory / "input.cms"
        ciphertext.write_bytes(e.encrypt(self.pack(), self.cert.read_bytes()))
        out = directory / "result.zip"
        stdout, stderr = io.StringIO(), io.StringIO()
        argv = ["--input", str(ciphertext), "--runtime-id", RID, "--key", str(self.key), "--recipient", str(self.cert), "--output", str(out)]
        with redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(r.main(argv), 0)
        self.assertEqual(r.verify_archive(out.read_bytes())["identity"]["runtime_id"], RID)
        self.assertEqual(out.stat().st_mode & 0o777, 0o600)
        out.unlink()
        corrupted = bytearray(ciphertext.read_bytes())
        corrupted[-1] ^= 1
        ciphertext.write_bytes(corrupted)
        with redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(r.main(argv), 1)
        self.assertFalse(out.exists())
        self.assertNotIn("lease-synthetic", stderr.getvalue())

    def test_cli_only_ciphertext(self):
        outdir = self.root / "export"
        outdir.mkdir(mode=0o700)
        out = outdir / "evidence.cms"
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = e.main(["--state-dir", str(self.state), "--runtime-id", RID,
                             "--recipient", str(self.cert), "--output", str(out)])
        self.assertEqual(result, 0)
        self.assertEqual(stdout.getvalue(), "encrypted evidence ready\n")
        self.assertEqual(stderr.getvalue(), "")
        self.assertNotIn(b"hanjuku", out.read_bytes())
        self.assertFalse(list(self.state.rglob("*.cms")))


if __name__ == "__main__":
    unittest.main()
