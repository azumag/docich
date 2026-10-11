import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import unittest
import zlib

MODULE_PATH = Path(__file__).resolve().parents[1] / "soren91_private_frame_handoff.py"
SPEC = importlib.util.spec_from_file_location("soren91_private_frame_handoff", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)

SESSION = "2d51f1b2-32f3-4c3c-91eb-48573d36e52f"


def chunk(kind: bytes, data: bytes) -> bytes:
    body = kind + data
    return len(data).to_bytes(4, "big") + body + (zlib.crc32(body) & 0xffffffff).to_bytes(4, "big")


def png(width=960, height=540) -> bytes:
    ihdr = (width.to_bytes(4, "big") + height.to_bytes(4, "big")
            + bytes([8, 2, 0, 0, 0]))
    raw = (bytes(width * 3 + 1) * height)
    return (MODULE.PNG_SIGNATURE + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


def metadata(image_path: Path, reason: str, session_id=SESSION) -> dict:
    file_mtime = round(image_path.stat().st_mtime_ns / 1_000_000)
    return {
        "schema": 1,
        "game": 7,
        "turn": 12,
        "sessionId": session_id,
        "reason": reason,
        "imageFormat": "png",
        "boardConfidence": 0.47,
        "currentPieceConfidence": 0.31,
        "image": {"width": 960, "height": 540},
        "fileMtimeMs": file_mtime,
        "recordedAtMs": file_mtime,
        "capture": {
            "capturedAtMs": 1234.5,
            "captureMs": 8.25,
            "geometry": {
                "x": 0, "y": 0, "width": 960, "height": 540,
                "scrollX": 0, "scrollY": 0, "dpr": 1,
                "viewportWidth": 960, "viewportHeight": 540, "viewportScale": 1,
            },
        },
    }


class PrivateFrameHandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="soren91-private-frame-")
        self.root = Path(self.tmp.name)
        self.runtime = self.root / "runtime"
        self.run_dir = self.runtime / "tmp/rejected_frame_diagnostics" / f"run_{SESSION}"
        self.run_dir.mkdir(parents=True, mode=0o700)
        (self.runtime / "tmp").chmod(0o755)  # Runtime tmp need not be private.
        (self.runtime / "tmp/rejected_frame_diagnostics").chmod(0o700)

    def tearDown(self):
        self.tmp.cleanup()

    def add_frame(self, index=0, reason="unknown-current", *, file_name="image.png",
                  sidecar_name="metadata.json", session_id=SESSION, content=None):
        pair = self.run_dir / f"frame_{index:02d}"
        pair.mkdir(mode=0o700)
        image_path = pair / file_name
        image = png() if content is None else content
        image_path.write_bytes(image)
        image_path.chmod(0o600)
        stamp = image_path.stat().st_mtime_ns
        data = metadata(image_path, reason, session_id)
        sidecar = pair / sidecar_name
        sidecar.write_text(json.dumps(data, separators=(",", ":")) + "\n")
        sidecar.chmod(0o600)
        return image, data

    def test_export_and_private_receive_validate_exact_pair_bytes_and_provenance(self):
        image, sidecar = self.add_frame()
        buffer = io.BytesIO()
        MODULE.export_run(self.runtime.resolve(), SESSION, buffer)
        payload = buffer.getvalue()
        verified = MODULE.verify_bundle(payload, SESSION)
        self.assertEqual(verified[f"run_{SESSION}/frame_00/image.png"], image)
        self.assertEqual(json.loads(verified[f"run_{SESSION}/frame_00/metadata.json"]), sidecar)
        self.assertEqual(json.loads(verified["manifest.json"])["files"][0]["imageSha256"],
                         hashlib.sha256(image).hexdigest())
        private_parent = self.root / "private"
        private_parent.mkdir(mode=0o700)
        output = private_parent / "capture"
        result = MODULE.receive_bundle(payload, SESSION, output)
        self.assertEqual(result["status"], "verified")
        session_output = output / f"run_{SESSION}"
        self.assertEqual((session_output / "frame_00" / "image.png").read_bytes(), image)
        self.assertEqual(json.loads((session_output / "frame_00" / "metadata.json").read_text()), sidecar)
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((session_output / "frame_00" / "image.png").stat().st_mode), 0o600)

    def test_strict_uuid_no_follow_pair_completeness_and_private_file_contract(self):
        with self.assertRaisesRegex(MODULE.HandoffError, "invalid_session_id"):
            MODULE.read_selected_run(self.runtime.resolve(), "../" + SESSION)
        self.add_frame(sidecar_name="other.json")
        with self.assertRaisesRegex(MODULE.HandoffError, "incomplete_frame_pair"):
            MODULE.read_selected_run(self.runtime.resolve(), SESSION)

    def test_wrong_session_metadata_duplicate_reason_and_bad_geometry_fail_closed(self):
        self.add_frame(session_id="b09027a0-2d46-4c88-90fb-3c145db9c935")
        with self.assertRaisesRegex(MODULE.HandoffError, "invalid_frame_metadata"):
            MODULE.read_selected_run(self.runtime.resolve(), SESSION)
        shutil.rmtree(self.run_dir / "frame_00")
        self.add_frame(reason="unknown-current")
        self.add_frame(index=1, reason="unknown-current")
        with self.assertRaisesRegex(MODULE.HandoffError, "duplicate_reason"):
            MODULE.read_selected_run(self.runtime.resolve(), SESSION)

    def test_tampered_bundle_digest_and_existing_output_are_rejected(self):
        image, _ = self.add_frame()
        buffer = io.BytesIO()
        MODULE.export_run(self.runtime.resolve(), SESSION, buffer)
        payload = buffer.getvalue()
        image_offset = payload.find(image)
        self.assertGreaterEqual(image_offset, 0)
        damaged = payload[:image_offset + 12] + bytes([payload[image_offset + 12] ^ 1]) + payload[image_offset + 13:]
        with self.assertRaises(MODULE.HandoffError):
            MODULE.verify_bundle(damaged, SESSION)
        private_parent = self.root / "private"
        private_parent.mkdir(mode=0o700)
        output = private_parent / "capture"
        output.mkdir(mode=0o700)
        with self.assertRaisesRegex(MODULE.HandoffError, "output_must_be_new"):
            MODULE.receive_bundle(payload, SESSION, output)

    def test_one_shot_command_carries_only_fixed_duration_and_profile(self):
        command = MODULE.manual_command(Path("/fixed/docich"))
        self.assertEqual(command[-5:], ["start", "--duration-minutes", "5",
                                        "--capture-profile", "rejected_png_v1"])
        self.assertNotIn("SOREN91_CAPTURE_FORMAT", command)
        self.assertNotIn("SOREN91_REJECT_FRAME_DIAGNOSTICS", command)


if __name__ == "__main__":
    unittest.main()
