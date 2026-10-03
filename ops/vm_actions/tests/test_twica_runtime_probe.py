import importlib.util
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location('twica_probe', ROOT / 'ops/vm_actions/probe_twica_runtime.py')
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class TwicaRuntimeProbeTests(unittest.TestCase):
    def packet(self, alpha, captured=100, magic=b'TWICARG1'):
        return probe.HEADER.pack(magic, 6, 2, captured) + b''.join(bytes((255, 255, 255, a)) for a in alpha)

    def test_transparent_is_not_display_success(self):
        self.assertEqual(probe.classify_frame(self.packet([0] * 12), 6, 2, 101), 20)

    def test_white_and_semtransparent_pixels_survive(self):
        for alpha in (255, 128, 1):
            with self.subTest(alpha=alpha):
                self.assertEqual(probe.classify_frame(self.packet([0, alpha] + [0] * 10), 6, 2, 101), 0)

    def test_already_right_aligned_pixels_are_clipped(self):
        self.assertEqual(probe.classify_frame(self.packet([0] * 5 + [255] + [0] * 6), 6, 2, 101), 21)

    def test_full_background_has_own_category(self):
        self.assertEqual(probe.classify_frame(self.packet([255] * 12), 6, 2, 101), 22)

    def test_ttl_and_future_fail_closed(self):
        for now in (99, 1_000_000_100):
            self.assertEqual(probe.classify_frame(self.packet([255] * 12), 6, 2, now), 41)

    def test_bad_frame_geometry_and_magic(self):
        packet = self.packet([0] * 12)
        for payload, width, height in ((packet, True, 2), (packet, 7, 2), (packet[:-1], 6, 2),
                                        (self.packet([0] * 12, magic=b'WRONGMAG'), 6, 2)):
            self.assertEqual(probe.classify_frame(payload, width, height, 101), 42)

    def test_gate_requires_current_generation_and_liveness(self):
        owner = {'schema': 1, 'generation': 'a' * 32, 'owner': 'common'}
        pipeline = {'ready': True}
        renderer = {'state': 'active', 'generation': 'a' * 32}
        with patch.object(probe, 'fresh', return_value=True):
            self.assertIsNone(probe.gate(owner, pipeline, renderer, 100))
            self.assertEqual(probe.gate({}, pipeline, renderer, 100), 30)
            self.assertEqual(probe.gate(dict(owner, owner='legacy'), pipeline, renderer, 100), 31)
            self.assertEqual(probe.gate(dict(owner, owner=[]), pipeline, renderer, 100), 32)
            self.assertEqual(probe.gate(owner, {}, renderer, 100), 33)
            self.assertEqual(probe.gate(owner, pipeline, dict(renderer, state='standby'), 100), 35)
            self.assertEqual(probe.gate(owner, pipeline, dict(renderer, generation='b' * 32), 100), 36)
        with patch.object(probe, 'fresh', side_effect=[True, False]):
            self.assertEqual(probe.gate(owner, pipeline, renderer, 100), 34)

    def test_files_reject_links_permissions_and_oversize(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            path = root / 'state'
            path.write_text('{}'); path.chmod(0o600)
            self.assertEqual(probe.read_file(path, 2), b'{}')
            with self.assertRaises(ValueError):
                probe.read_file(path, 1)
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                probe.read_file(path, 10)
            self.assertEqual(probe.read_file(path, 10, private=False), b'{}')
            alias = root / 'alias'; alias.symlink_to(path)
            with self.assertRaises(OSError):
                probe.read_file(alias, 10)
            fifo = root / 'fifo'; os.mkfifo(fifo, 0o600)
            with self.assertRaises(ValueError):
                probe.read_file(fifo, 10)

    def test_native_requires_current_runner_and_compositor(self):
        pipeline = {'pid': 12, 'encoder_pid': 14}
        runner = {'pid': 10}
        graph = '[0:v:0][2:v:0]overlay=x=round(main_w/3):y=0:format=auto[twica_out]'
        def processes(pid):
            return {10: {'cwd': probe.SOREN, 'args': ['python3', 'lib/direct_stream.py']},
                    14: {'args': ['ffmpeg', '-filter_complex', graph, '-map', '[twica_out]']}}.get(pid, {})
        with patch.object(probe, 'descendant', return_value=True), patch.object(probe, 'process', side_effect=processes):
            self.assertIsNone(probe.native_gate(pipeline, runner))
            self.assertEqual(probe.native_gate(pipeline, {'pid': 20}), 38)
            self.assertEqual(probe.native_gate(dict(pipeline, encoder_pid=15), runner), 39)
        with patch.object(probe, 'descendant', return_value=False):
            self.assertEqual(probe.native_gate(pipeline, runner), 37)

    def test_no_parent_traversal_on_malformed_pids(self):
        for pid in (None, True, [], 1, '10'):
            self.assertFalse(probe.descendant(pid, 10))


if __name__ == '__main__':
    unittest.main()
