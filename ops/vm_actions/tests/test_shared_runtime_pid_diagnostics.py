from __future__ import annotations

import time
import json
import copy
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COLLECTOR = ROOT / "ops" / "vm_actions" / "collect_diagnostics.py"


def load_collector():
    spec = importlib.util.spec_from_file_location("collect_diagnostics_shared_pid", str(COLLECTOR))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SharedRuntimePidTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_collector()
        self.tmp = tempfile.TemporaryDirectory(prefix="sharedpid-")
        self.addCleanup(self.tmp.cleanup)
        self.proc = Path(self.tmp.name).resolve() / "proc"
        self.proc.mkdir()
        self.soren = Path(self.tmp.name).resolve() / 'soren'
        state = self.soren / 'tmp/state/direct_stream'
        state.mkdir(parents=True)
        (state/'status.json').write_text(json.dumps({'running': True, 'pid': 103, 'ffmpeg_pid': 101}))
        (state.parent/'audio_worker.pid').write_text('102\n')
        boot = self.proc / "sys/kernel/random"
        boot.mkdir(parents=True)
        (boot / "boot_id").write_text("12345678-1234-1234-1234-123456789abc\n")
        # fake /proc/stat with btime
        (self.proc / "stat").write_text("cpu 0 0 0 0 0 0 0 0 0 0\nbtime 1000\ncpu 0\n", encoding="utf-8")

    def _make_proc(self, pid, uid, comm, cmdline, start_ticks, parent=1):
        base = self.proc / str(pid)
        base.mkdir()
        (base / "comm").write_text(comm + "\n", encoding="utf-8")
        (base / "cmdline").write_bytes(("\x00".join(cmdline) + "\x00").encode() if cmdline else b"")
        # /proc/<pid>/stat: comm in parens then fields; starttime at index 19
        stat_fields = ["S", str(parent), "1", "1", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "1", "0", str(start_ticks)]
        (base / "stat").write_text(f"{pid} ({comm}) " + " ".join(stat_fields) + "\n", encoding="utf-8")
        exe = '/usr/bin/' + ('python3.12' if comm == 'python3' else comm)
        (base/'exe').symlink_to(exe)
        (base/'cwd').symlink_to(self.soren)
        return base

    def producers(self):
        uid = os.getuid()
        self._make_proc(101, uid, 'ffmpeg', ['ffmpeg', '-f', 'x11grab', '-progress', 'pipe:1', '-secret', 'TOKEN=abc'], 1000, parent=103)
        self._make_proc(102, uid, 'bash', ['/bin/bash', './workers/audio_worker.sh'], 2000)
        self._make_proc(103, uid, 'python3', ['python3', str(self.soren/'lib/direct_stream.py'), 'run'], 3000)

    def collect(self):
        return self.mod._collect_shared_runtime_pids(soren=self.soren, proc_root=self.proc,
                                                     docich_root=Path(self.tmp.name).resolve()/'docich')

    def test_collect_exact_producers_and_redact(self):
        self.producers()
        sec = self.collect()
        self.assertTrue(sec['scan_complete'])
        for role, pid in [('encoder', 101), ('audio', 102), ('shared_stream', 103)]:
            self.assertEqual(sec['roles'][role]['pids'], [pid])
        self.assertEqual(sec['roles']['encoder']['entries'][0]['start_ticks'], 1000)
        text = json.dumps(sec)
        for private in ('TOKEN', 'secret', 'cmdline', 'argv', str(self.soren)):
            self.assertNotIn(private, text)
        self.assertTrue(self.mod.compare_shared_runtime_pids(sec, sec)['match'])

    def test_source_refusal_reports_fixed_source_and_reason_only(self):
        self.producers()
        state = self.soren / 'tmp/state'
        stream = state / 'direct_stream/status.json'
        audio = state / 'audio_worker.pid'
        original = stream.read_bytes()
        cases = [
            ('stream', 'directory_writable', lambda: state.chmod(0o770), lambda: state.chmod(0o700)),
            ('audio', 'missing', audio.unlink, lambda: audio.write_text('102')),
            ('audio', 'invalid_pid', lambda: audio.write_text('PRIVATE-secret'), lambda: audio.write_text('102')),
            ('stream', 'invalid_json', lambda: stream.write_text('PRIVATE-secret'), lambda: stream.write_bytes(original)),
            ('stream', 'not_running', lambda: stream.write_text('{}'), lambda: stream.write_bytes(original)),
        ]
        for source, reason, change, restore in cases:
            with self.subTest(source=source, reason=reason):
                try:
                    change()
                    sec = self.collect()
                    self.assertFalse(sec['scan_complete'])
                    self.assertEqual(sec['failure_stage'], 'producer_sources')
                    self.assertEqual(sec['failure_source'], source)
                    self.assertEqual(sec['failure_reason'], reason)
                    self.assertNotIn('PRIVATE', json.dumps(sec))
                    self.assertNotIn(str(self.soren), json.dumps(sec))
                finally:
                    restore()
        sec = self.collect()
        self.assertTrue(sec['scan_complete'])
        self.assertFalse(any(k.startswith('failure_') for k in sec))

    def test_source_recheck_preserves_phase_and_fixed_refusal(self):
        from unittest.mock import patch
        self.producers()
        original = self.mod._shared_runtime_sources
        calls = 0
        def source(root):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise self.mod._SharedRuntimeSourceError('audio', 'missing')
            return original(root)
        with patch.object(self.mod, '_shared_runtime_sources', side_effect=source):
            sec = self.collect()
        self.assertEqual(sec['failure_stage'], 'source_recheck')
        self.assertEqual(sec['failure_source'], 'audio')
        self.assertEqual(sec['failure_reason'], 'missing')
        self.assertFalse(self.mod.compare_shared_runtime_pids(sec, sec)['match'])
        unsafe = self.mod._SharedRuntimeSourceError('PRIVATE-source', 'PRIVATE-reason')
        self.assertEqual((unsafe.source, unsafe.reason), ('unknown', 'unknown'))

    def test_unrelated_processes_never_enter_fixed_sets(self):
        self.producers()
        self._make_proc(104, os.getuid(), 'python3', ['python3', 'test_pulseaudio_helpers.py', 'direct_stream', 'shared'], 4000)
        self._make_proc(105, os.getuid(), 'pulseaudio', ['pulseaudio'], 4001)
        from unittest.mock import patch
        with patch.object(self.mod.os, 'listdir', side_effect=AssertionError('no process enumeration')):
            sec = self.collect()
        self.assertTrue(sec['scan_complete'])
        self.assertEqual(sec['roles']['audio']['pids'], [102])

    def test_wrong_command_or_dead_recorded_pid_is_partial(self):
        self.producers()
        command = self.proc/'102/cmdline'
        for raw in (b'bash\0fake_audio_worker.sh\0', b'python3\0test_pulseaudio_helpers.py\0'):
            command.write_bytes(raw)
            self.assertFalse(self.collect()['scan_complete'])
        command.unlink()
        self.assertFalse(self.collect()['scan_complete'])

    def test_missing_stopped_and_stale_source_fail_closed(self):
        self.producers()
        record = self.soren/'tmp/state/direct_stream/status.json'
        for value in ({}, {'running': False, 'pid': 103, 'ffmpeg_pid': 101},
                      {'running': True, 'pid': 999, 'ffmpeg_pid': 101},
                      {'running': True, 'pid': True, 'ffmpeg_pid': 101}):
            record.write_text(json.dumps(value))
            self.assertFalse(self.collect()['scan_complete'])
        record.unlink()
        self.assertFalse(self.collect()['scan_complete'])

    def test_state_or_process_symlink_rejected(self):
        self.producers()
        for source in (self.soren/'tmp/state/audio_worker.pid', self.proc/'102/stat'):
            moved = source.with_suffix('.moved')
            source.rename(moved); source.symlink_to(moved)
            self.assertFalse(self.collect()['scan_complete'])
            source.unlink(); moved.rename(source)

    def test_source_handover_and_process_reuse_during_scan_fail_closed(self):
        self.producers()
        from unittest.mock import patch
        original = self.mod._shared_runtime_sources
        calls = 0
        def changing(root):
            nonlocal calls
            calls += 1
            value = original(root)
            if calls == 2: value['audio'] = 999
            return value
        with patch.object(self.mod, '_shared_runtime_sources', side_effect=changing):
            self.assertFalse(self.collect()['scan_complete'])
        process = self.mod._shared_runtime_process
        def reused(*args):
            result = process(*args)
            if args[-1] == 102:
                path = self.proc/'102/stat'
                path.write_text(path.read_text().replace('2000', '2001'))
            return result
        with patch.object(self.mod, '_shared_runtime_process', side_effect=reused):
            self.assertFalse(self.collect()['scan_complete'])

    def pipeline(self):
        self.producers()
        adapter = self.proc/'101'
        (adapter/'cmdline').write_bytes(b'python3\0-m\0docich.twica_ffmpeg\0-f\0x11grab\0-progress\0pipe:1\0')
        (adapter/'exe').unlink(); (adapter/'exe').symlink_to('/usr/bin/python3.12')
        self._make_proc(104, os.getuid(), 'python3',
                        ['python3', '-m', 'docich.twica_encoder', '3', '4', '5', 'ffmpeg'], 4000, parent=101)
        self._make_proc(105, os.getuid(), 'ffmpeg',
                        ['ffmpeg', '-f', 'x11grab', '-progress', 'pipe:1', 'PRIVATE'], 5000, parent=104)
        root = Path(self.tmp.name).resolve()/'docich/run/twica-common'
        root.mkdir(parents=True)
        record = {'schema': 1, 'pid': 101,
                  'identity': '12345678-1234-1234-1234-123456789abc:1000',
                  'monotonic_ns': time.monotonic_ns(), 'ready': True, 'encoder_pid': 105}
        file = root/'pipeline.json'; file.write_text(json.dumps(record))
        return file, record

    def test_common_chain_projects_native_and_shared_owners(self):
        self.pipeline()
        out = self.collect()
        self.assertTrue(out['scan_complete'])
        self.assertEqual(out['roles']['encoder']['pids'], [105])
        self.assertEqual(out['roles']['shared_stream']['pids'], [101, 103, 104])
        self.assertNotIn('PRIVATE', json.dumps(out))
        self.assertTrue(self.mod.compare_shared_runtime_pids(out, out)['match'])

    def test_heartbeat_updated_during_open_uses_read_completion_time(self):
        file, record = self.pipeline()
        from unittest.mock import patch
        original = self.mod._stream_title_open_fixed
        clock = [1000]
        def opened(root, parts):
            if parts[-1] == 'pipeline.json':
                clock[0] += 1
                file.write_text(json.dumps({**record, 'monotonic_ns': clock[0]}))
            return original(root, parts)
        with patch.object(self.mod, '_stream_title_open_fixed', side_effect=opened), \
                patch.object(self.mod.time, 'monotonic_ns', side_effect=lambda: clock[0]):
            self.assertTrue(self.collect()['scan_complete'])

    def test_future_and_expired_heartbeat_are_partial(self):
        file, record = self.pipeline()
        from unittest.mock import patch
        with patch.object(self.mod.time, 'monotonic_ns', return_value=4_000_000_000):
            for stamp in (4_000_000_001, 1_000_000_000):
                file.write_text(json.dumps({**record, 'monotonic_ns': stamp}))
                self.assertFalse(self.collect()['scan_complete'])

    def test_wrong_parent_or_pipeline_evidence_is_partial(self):
        file, record = self.pipeline()
        for change in ({'schema': True}, {'pid': 999}, {'identity': 'wrong'},
                       {'monotonic_ns': 0}, {'encoder_pid': True}, {'ready': False}):
            file.write_text(json.dumps({**record, **change}))
            self.assertFalse(self.collect()['scan_complete'])
        file.write_text(json.dumps(record))
        for pid, parent in ((101, 103), (104, 101), (105, 104)):
            path = self.proc/str(pid)/'stat'; original = path.read_text()
            path.write_text(original.replace(f') S {parent} ', ') S 999 '))
            self.assertFalse(self.collect()['scan_complete'])
            path.write_text(original)
        target = file.with_name('saved.json'); file.rename(target); file.symlink_to(target)
        self.assertFalse(self.collect()['scan_complete'])
        file.unlink()
        self.assertFalse(self.collect()['scan_complete'])

    def test_native_with_unrelated_parent_is_not_a_producer(self):
        self.producers()
        path = self.proc/'101/stat'
        path.write_text(path.read_text().replace(') S 103 ', ') S 999 '))
        self.assertFalse(self.collect()['scan_complete'])

    def test_pipeline_encoder_changes_between_reads_are_partial(self):
        self.pipeline()
        from unittest.mock import patch
        original = self.mod._shared_runtime_pipeline
        calls = 0
        def changed(*args):
            nonlocal calls
            calls += 1
            value = original(*args)
            return value if calls == 1 else 999
        with patch.object(self.mod, '_shared_runtime_pipeline', side_effect=changed):
            self.assertFalse(self.collect()['scan_complete'])

    def test_failure_stage_is_fixed_and_does_not_expose_exception(self):
        from unittest.mock import patch
        self.producers()
        for helper, stage in (('_shared_runtime_sources', 'producer_sources'),
                              ('_shared_runtime_process', 'stream_process')):
            with patch.object(self.mod, helper, side_effect=ValueError('PRIVATE /secret/path')):
                result = self.collect()
            self.assertFalse(result['scan_complete'])
            self.assertEqual(result['failure_stage'], stage)
            self.assertNotIn('PRIVATE', json.dumps(result))
            self.assertFalse(self.mod.compare_shared_runtime_pids(result, result)['match'])
            self.assertTrue(all(not row['entries'] for row in result['roles'].values()))

    def test_second_chain_exception_stays_a_recheck_failure(self):
        from unittest.mock import patch
        self.producers()
        original = self.mod._shared_runtime_process
        for fail_call in (4, 5, 6):
            calls = 0
            def fail_on_recheck(*args):
                nonlocal calls
                calls += 1
                if calls == fail_call:
                    raise OSError('PRIVATE /secret/path')
                return original(*args)
            with self.subTest(fail_call=fail_call), patch.object(
                    self.mod, '_shared_runtime_process', side_effect=fail_on_recheck):
                result = self.collect()
                self.assertEqual(result['failure_stage'], 'identity_recheck')
                self.assertFalse(result['scan_complete'])
                self.assertFalse(self.mod.compare_shared_runtime_pids(result, result)['match'])
                self.assertNotIn('PRIVATE', json.dumps(result))
                self.assertTrue(all(not row['entries'] for row in result['roles'].values()))

    def test_pipeline_and_audio_failures_identify_stage(self):
        file, record = self.pipeline()
        file.write_text(json.dumps({**record, 'ready': False}))
        self.assertEqual(self.collect()['failure_stage'], 'pipeline_receipt')
        (self.proc/'102/cmdline').write_bytes(b'bash\0wrong.sh\0')
        self.assertEqual(self.collect()['failure_stage'], 'audio_process')

    def receipt(self):
        return {'schema_version': 2, 'scan_complete': True,
                'boot_id': '12345678-1234-1234-1234-123456789abc',
                'roles': {role: {'count': 1, 'pids': [10+i],
                    'entries': [{'pid': 10+i, 'start_ticks': 100, 'started_at': 1001}]}
                    for i, role in enumerate(self.mod.SHARED_RUNTIME_ROLES)}}

    def test_identical_nonempty_and_full_envelope_match(self):
        before = self.receipt()
        self.assertTrue(self.mod.compare_shared_runtime_pids(before, copy.deepcopy(before))['match'])
        self.assertTrue(self.mod.compare_shared_runtime_pids(
            {'shared_runtime_pids': before}, {'shared_runtime_pids': before})['match'])

    def test_role_entries_and_pid_projection_order_are_irrelevant(self):
        before = self.receipt()
        role = before['roles']['encoder']
        role['entries'].append({'pid': 99, 'start_ticks': 102, 'started_at': 1002})
        role.update(count=2, pids=[11, 99])
        after = copy.deepcopy(before)
        after['roles']['encoder']['entries'].reverse()
        self.assertTrue(self.mod.compare_shared_runtime_pids(before, after)['match'])
        after['roles']['encoder']['pids'].reverse()
        self.assertTrue(self.mod.compare_shared_runtime_pids(before, after)['match'])

    def test_different_boot_pid_or_start_identity_fails(self):
        for change in ('boot', 'pid', 'ticks', 'epoch'):
            with self.subTest(change=change):
                before = self.receipt(); after = copy.deepcopy(before)
                entry = after['roles']['encoder']['entries'][0]
                if change == 'boot': after['boot_id'] = '22345678-1234-1234-1234-123456789abc'
                elif change == 'pid':
                    entry['pid'] = 99; after['roles']['encoder']['pids'] = [99]
                elif change == 'ticks': entry['start_ticks'] += 1
                else: entry['started_at'] += 1
                self.assertFalse(self.mod.compare_shared_runtime_pids(before, after)['match'])

    def test_empty_and_inconsistent_or_incompatible_receipts_fail_closed(self):
        changes = [lambda s: s.update(schema_version=1), lambda s: s.update(schema_version=True),
            lambda s: s.update(scan_complete=False), lambda s: s.pop('boot_id'),
            lambda s: s.update(boot_id='SECRET'), lambda s: s.update(extra=1),
            lambda s: s['roles'].pop('audio'), lambda s: s['roles'].update(extra={}),
            lambda s: s['roles']['audio'].update(count=0, pids=[], entries=[]),
            lambda s: s['roles']['audio'].update(count=2),
            lambda s: s['roles']['audio'].update(pids=[99]),
            lambda s: s['roles']['audio']['entries'][0].update(pid=True),
            lambda s: s['roles']['audio']['entries'][0].update(start_ticks=-1),
            lambda s: s['roles']['audio']['entries'][0].update(started_at=True),
            lambda s: s['roles']['audio']['entries'][0].update(start_ticks=2**64),
            lambda s: s['roles']['audio']['entries'][0].update(secret='PRIVATE'),
            lambda s: s['roles'].update(audio=copy.deepcopy(s['roles']['encoder'])),
            lambda s: s.update(roles={r: {'count': 0, 'pids': [], 'entries': []}
                                     for r in self.mod.SHARED_RUNTIME_ROLES})]
        for index, change in enumerate(changes):
            with self.subTest(index=index):
                value = self.receipt(); change(value)
                self.assertFalse(self.mod.compare_shared_runtime_pids(value, value)['match'])
        self.assertFalse(self.mod.compare_shared_runtime_pids({}, {})['match'])
        self.assertFalse(self.mod.compare_shared_runtime_pids({'status': 'output_omitted'}, {})['match'])

    def test_missing_boot_identity_is_partial(self):
        (self.proc / 'sys/kernel/random/boot_id').unlink()
        self.assertFalse(self.collect()['scan_complete'])



if __name__ == '__main__':
    unittest.main()
