import importlib.util
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
COLLECTOR = ROOT / "ops" / "vm_actions" / "collect_diagnostics.py"


def load_collector():
    spec = importlib.util.spec_from_file_location("collect_diagnostics", str(COLLECTOR))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rotation_projection_never_emits_seed_or_request_text(tmp_path):
    module = load_collector()
    (tmp_path / "corner_rotation.json").write_text(json.dumps({
        "status": "waiting", "seed": "DO-NOT-PUBLISH-SEED", "slot": 3,
        "last_seen_at": 100, "next_due_at": 200, "eligible": ["paper", "meriken"],
        "pending": {"request_id": "DO-NOT-PUBLISH-REQUEST", "prompt": "DO-NOT-PUBLISH-BODY"},
    }))
    output = {}
    module._collect_corner_files(tmp_path, output, 100)
    projection = output["corner_rotation"]
    assert projection["status"] == "waiting"
    assert projection["slot"] == 3
    assert projection["pending"] is True
    assert projection["eligible_count"] == 2
    # The configured dispatch policy is projected; the production profile runs
    # the continuous queue with a 24h rolling cooldown (#948 follow-up).
    assert projection["schedule_mode"] == "queue"
    assert projection["cooldown_seconds"] == 86400.0
    assert "DO-NOT-PUBLISH" not in json.dumps(output)


def test_corner_match_target_projection_is_bounded_and_does_not_change_n(tmp_path):
    module = load_collector()
    for value in (1, 3, 100, 0, 101, True, "SECRET-TARGET", None):
        (tmp_path / "retro_corner.json").write_text(json.dumps({
            "game": "nsnake", "target_matches": value, "save": "SECRET-SAVE",
        }))
        (tmp_path / "corner_rotation.json").write_text(json.dumps({
            "eligible": ["nsnake", "paper"], "interval_seconds": 43200,
        }))
        output = {}
        module._collect_corner_files(tmp_path, output, 100)
        expected = value if type(value) is int and 1 <= value <= 100 else None
        assert output["retro_corner"]["target_matches"] == expected
        assert output["corner_rotation"]["eligible_count"] == 2
        assert output["corner_rotation"]["interval_seconds"] == 43200
        assert "SECRET" not in json.dumps(output)


def test_hanjuku_telemetry_is_enum_only_and_never_publishes_frames_or_state():
    module=load_collector()
    state={'game':'hanjuku-hero','end_reason':'screen_stalled','bot_phase':'battle',
           'bot_actions_sent':17,'battles_started':2,'battles_finished':1,
           'screen_unchanged_seconds':300,'frame':'SECRET-FRAME','prompt':'SECRET-PROMPT'}
    output=module._project_corner_state(state)
    assert output['end_reason']=='screen_stalled'
    assert output['bot_phase']=='battle' and output['bot_actions_sent']==17
    assert output['battles_finished']==1
    assert 'SECRET' not in json.dumps(output)
    state.update(end_reason='SECRET-OUTCOME',bot_phase='SECRET-PHASE',bot_actions_sent='SECRET-COUNT',screen_unchanged_seconds=float('nan'))
    output=module._project_corner_state(state)
    assert output['end_reason'] is None and output['bot_phase'] is None
    assert output['bot_actions_sent'] is None and output['screen_unchanged_seconds'] is None


def test_hanjuku_chart_progress_is_allowlisted_counters_only():
    module=load_collector()
    state={'game':'hanjuku-hero','bot_version':'hanjuku-chart-v2','bot_chart':{
        'chapter':1,'chart_step':'1-A2','strategy_variant':'budget_boss_kit_first',
        'orders_launched':4,'orders_failed':0,'captured':2,'wins':3,'losses':1,'unclassified':1,
        'cards_used':2,'generals_lost':1,'gold':137,'month':'1-5','name_entered':True,
        'reason':'SECRET-REASON','text':'SECRET-TEXT'}}
    chart=module._project_corner_state(state)['bot_chart']
    assert chart['chart_step']=='1-A2' and chart['captured']==2 and chart['gold']==137
    assert chart['strategy_variant']=='budget_boss_kit_first' and chart['name_entered'] is True
    output=module._project_corner_state(state)
    assert output['bot_version']=='hanjuku-chart-v2'
    assert 'SECRET' not in json.dumps(output)
    state['bot_chart'].update(chart_step='SECRET step',month='SECRET',strategy_variant='SECRET-X',
                              wins=True,gold='12')
    state['bot_version']='SECRET'
    output=module._project_corner_state(state)
    chart=output['bot_chart']
    assert chart['chart_step'] is None and chart['month'] is None and chart['strategy_variant'] is None
    assert chart['wins'] is None and chart['gold'] is None and output['bot_version'] is None
    assert module._project_corner_state({'bot_chart':'SECRET'})['bot_chart'] is None


def test_hanjuku_audio_and_narration_evidence_is_bounded():
    module=load_collector()
    state={'game':'hanjuku-hero','narration':{'enqueued':3,'skipped':9,'delivery_failed':0,'text':'SECRET'},
           'game_audio':{'status':'applied','target_percent':80,'streams':[
               {'sink':'soren_null','volume_percent':[80,80],'mute':False,'secret':'SECRET'}]}}
    out=module._project_corner_state(state)
    assert out['narration']=={'enqueued':3,'delivery_failed':0,'skipped':9}
    assert out['game_audio']=={'status':'applied','target_percent':80,'streams':[
        {'sink':'soren_null','volume_percent':[80,80],'mute':False}]}
    assert 'SECRET' not in json.dumps(out)
    state['game_audio']={'status':'SECRET','streams':[{'sink':'bad sink;rm','volume_percent':['x'],'mute':'no'}]}
    audio=module._project_corner_state(state)['game_audio']
    assert audio['status'] is None and audio['streams']==[{'sink':None,'volume_percent':[],'mute':None}]


def _synthetic_environ(pairs):
    return b'\x00'.join([f'{k}={v}'.encode() for k, v in pairs.items()] + [b''])


def test_semantic_decision_absent_worker_reports_present_false():
    module = load_collector()
    assert module._collect_semantic_decision({'details': {}}) == {'present': False, 'readable': False}
    dead = {'details': {'chat_worker': {'pid': 4242, 'alive': False}}}
    assert module._collect_semantic_decision(dead) == {'present': False, 'readable': False}


def test_semantic_decision_unreadable_environ_reports_present_true_readable_false():
    module = load_collector()
    workers = {'details': {'chat_worker': {'pid': 4242, 'alive': True}}}
    with mock.patch.object(module.Path, 'read_bytes', side_effect=FileNotFoundError):
        assert module._collect_semantic_decision(workers) == {'present': True, 'readable': False}


def test_semantic_decision_reads_only_the_fixed_allowlist_and_hides_credential_values():
    module = load_collector()
    workers = {'details': {'chat_worker': {'pid': 4242, 'alive': True}}}
    environ = _synthetic_environ({
        'PATH': '/usr/bin',
        'DOCICH_SEMANTIC_BACKEND': 'retired-and-never-read',
        'DOCICH_JEV_ROUTE': 'vercel',
        'DOCICH_JEV_VERCEL_API_KEY': 'SYNTHETIC_VERCEL_SECRET',
        'COMMENT_CLASSIFIER_BACKEND': 'jev',
        'UNRELATED_OTHER_SECRET': 'SHOULD_NEVER_APPEAR',
    })
    with mock.patch.object(module.Path, 'read_bytes', return_value=environ):
        result = module._collect_semantic_decision(workers)
    assert result == {'present': True, 'readable': True, 'comment_classifier_backend': 'jev',
                      'backend': 'jev', 'route': 'vercel',
                      'requested_model': 'typesafe-ai/jev', 'credential': 'present',
                      'fallback_route': None, 'fallback_credential': 'not_applicable'}
    assert 'SYNTHETIC_VERCEL_SECRET' not in json.dumps(result)
    assert 'SHOULD_NEVER_APPEAR' not in json.dumps(result)
    assert 'retired-and-never-read' not in json.dumps(result)
    assert 'DOCICH_SEMANTIC_BACKEND' not in module.SEMANTIC_DECISION_ENV_ALLOWLIST


def test_semantic_decision_direct_route_credential_absent_and_unflagged_backend():
    module = load_collector()
    workers = {'details': {'chat_worker': {'pid': 4242, 'alive': True}}}
    with mock.patch.object(module.Path, 'read_bytes',
                           return_value=_synthetic_environ({'COMMENT_CLASSIFIER_BACKEND': 'jev'})):
        result = module._collect_semantic_decision(workers)
    assert result == {'present': True, 'readable': True, 'comment_classifier_backend': 'jev',
                      'backend': 'jev', 'route': 'direct',
                      'requested_model': 'jev-1.13.0', 'credential': 'absent',
                      'fallback_route': None, 'fallback_credential': 'not_applicable'}
    with mock.patch.object(module.Path, 'read_bytes',
                           return_value=_synthetic_environ({'TYPESAFE_API_KEY': 'unrelated-not-delegating'})):
        result = module._collect_semantic_decision(workers)
    assert result == {'present': True, 'readable': True, 'comment_classifier_backend': None,
                      'backend': 'heuristic', 'route': None,
                      'requested_model': None, 'credential': 'not_applicable',
                      'fallback_route': None, 'fallback_credential': 'not_applicable'}


def test_semantic_decision_reports_comment_classifier_backend_prerequisite_gate():
    # The docich classifier calls Jev only when this is exactly "jev"; its
    # plain value is kept alongside the derived "backend".
    module = load_collector()
    workers = {'details': {'chat_worker': {'pid': 4242, 'alive': True}}}
    with mock.patch.object(module.Path, 'read_bytes',
                           return_value=_synthetic_environ({'COMMENT_CLASSIFIER_BACKEND': 'jev'})):
        result = module._collect_semantic_decision(workers)
    assert result['comment_classifier_backend'] == 'jev'

    with mock.patch.object(module.Path, 'read_bytes',
                           return_value=_synthetic_environ({'COMMENT_CLASSIFIER_BACKEND': ''})):
        result = module._collect_semantic_decision(workers)
    assert result['comment_classifier_backend'] is None

    with mock.patch.object(module.Path, 'read_bytes', return_value=_synthetic_environ({})):
        result = module._collect_semantic_decision(workers)
    assert result['comment_classifier_backend'] is None


def test_semantic_decision_comment_classifier_backend_is_length_capped():
    module = load_collector()
    workers = {'details': {'chat_worker': {'pid': 4242, 'alive': True}}}
    huge = 'x' * 5000
    with mock.patch.object(module.Path, 'read_bytes',
                           return_value=_synthetic_environ({'COMMENT_CLASSIFIER_BACKEND': huge})):
        result = module._collect_semantic_decision(workers)
    assert result['comment_classifier_backend'] == 'x' * module.COMMENT_CLASSIFIER_BACKEND_STR_MAX


class CollectorFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="vmops-diag-")
        self.soren = Path(self.tmp.name) / "soren"
        (self.soren / "tmp" / "state").mkdir(parents=True)
        self.now = int(time.time())

    def tearDown(self):
        self.tmp.cleanup()

    def alive_pid(self):
        proc = subprocess.Popen(["sleep", "60"])
        self.addCleanup(proc.wait)
        self.addCleanup(proc.terminate)
        return proc.pid

    def write_pid(self, name, pid):
        (self.soren / "tmp" / "state" / f"{name}.pid").write_text(f"{pid}\n")

    def write_required_alive(self, skip=()):
        (self.soren / "tmp" / ".soren_loop.lock").mkdir(parents=True, exist_ok=True)
        (self.soren / "tmp" / ".soren_loop.lock" / "pid").write_text(f"{self.alive_pid()}\n")
        for name in ("improve_daemon", "chat_worker", "audio_worker", "deadline_monitor",
                     "radio_worker", "prediction_worker"):
            if name not in skip:
                self.write_pid(name, self.alive_pid())

    def write_lock(self, lane, pid, label="RADIO test", age_sec=10, token="tok"):
        lock = self.soren / "tmp" / "state" / ".ai_generation_locks" / lane
        lock.mkdir(parents=True, exist_ok=True)
        (lock / "owner").write_text(
            f"token={token}\npid={pid}\nlabel={label}\nstarted_at=2026-01-01 00:00:00\n"
        )
        stamp = self.now - age_sec
        os.utime(lock, (stamp, stamp))

    def write_stats(self, events):
        stats = self.soren / "tmp" / "state" / "ai_stats"
        stats.mkdir(parents=True, exist_ok=True)
        day = time.strftime("%Y%m%d", time.localtime(self.now))
        with open(stats / f"{day}.jsonl", "a", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def event(self, kind, label="RADIO", agent="opencode-go:deepseek-v4-flash", rc="", **extra):
        data = {
            "ts": self.now - 60,
            "day": "20260101",
            "event": kind,
            "label": label,
            "agent": agent,
            "rc": rc,
            "resolved_model": "",
        }
        data.update(extra)
        return data

    def snapshot(self):
        before = {}
        for path in sorted((self.soren / "tmp").rglob("*")):
            if path.is_file() and not path.is_symlink():
                stat = path.stat()
                before[str(path)] = (stat.st_mtime_ns, stat.st_size)
        return before

    def run_collector(self, *args):
        return subprocess.run(
            ["python3", str(COLLECTOR), *(args or [str(self.soren)])],
            capture_output=True,
            text=True,
            timeout=120,
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": "/tmp"},
        )


class CollectorContractTests(CollectorFixture):
    def test_healthy_runtime_reports_ok(self):
        self.write_required_alive(skip=("radio_worker", "chat_worker", "audio_worker"))
        self.write_pid("radio_worker", self.alive_pid())
        self.write_pid("chat_worker", self.alive_pid())
        self.write_pid("audio_worker", self.alive_pid())
        self.write_lock("radio", self.alive_pid())
        self.write_stats([self.event("attempt"), self.event("ok")])
        before = self.snapshot()
        proc = self.run_collector()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["workers"]["running"] >= 3, True)
        self.assertEqual(data["queues"]["lanes"]["radio"]["locked"], True)
        self.assertEqual(data["queues"]["lanes"]["radio"]["owner_alive"], True)
        self.assertEqual(data["ai"]["attempts"], 1)
        after = {}
        for path in sorted((self.soren / "tmp").rglob("*")):
            if path.is_file() and not path.is_symlink():
                stat = path.stat()
                after[str(path)] = (stat.st_mtime_ns, stat.st_size)
        self.assertEqual(before, after)

    def test_missing_soren_root_does_not_crash(self):
        proc = self.run_collector(str(self.soren / "nonexistent"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertIn(data["status"], ("ok", "warn", "critical"))
        self.assertEqual(data["meta"]["soren_root_exists"], False)
        self.assertEqual(
            data["corners"]["corner_rotation"],
            {"present": False, "readable": False},
        )

    def test_stopped_required_worker_is_critical(self):
        proc = self.run_collector()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["status"], "critical")
        self.assertIn("radio_worker", data["workers"]["required_down"])

    def test_stale_pid_file_is_reported(self):
        self.write_required_alive(skip=("radio_worker",))
        (self.soren / "tmp" / "state" / "radio_worker.pid").write_text("99999999\n")
        (self.soren / "tmp" / "state" / "garbage.pid").write_text("not-a-pid\n")
        proc = self.run_collector()
        data = json.loads(proc.stdout)
        self.assertIn("radio_worker", data["workers"]["stale_pid_files"])
        self.assertIn("radio_worker", data["workers"]["required_stale"])
        self.assertEqual(data["status"], "critical")

    def test_duplicate_workers_are_reported(self):
        self.write_required_alive(skip=("radio_worker", "chat_worker"))
        self.write_pid("radio_worker", self.alive_pid())
        self.write_pid("chat_worker", self.alive_pid())
        dup = {
            "status": "duplicate",
            "updated_at": self.now,
            "managed": {},
            "counts": {},
            "duplicates": [
                {
                    "name": "radio_worker",
                    "count": 2,
                    "managed_pid": str(os.getpid()),
                    "pids": [str(os.getpid()), "12346"],
                    "extra_pids": ["12346"],
                }
            ],
        }
        (self.soren / "tmp" / "state" / "worker_duplicates.json").write_text(json.dumps(dup))
        proc = self.run_collector()
        data = json.loads(proc.stdout)
        self.assertIn("radio_worker", data["workers"]["duplicates"])
        self.assertNotIn("chat_worker", data["workers"]["duplicates"])
        self.assertEqual(data["status"], "warn")

    def test_unregistered_pid_file_is_warn(self):
        self.write_required_alive()
        self.write_pid("custom_new_worker", self.alive_pid())
        proc = self.run_collector()
        data = json.loads(proc.stdout)
        self.assertIn("custom_new_worker", data["workers"]["unregistered"])
        self.assertEqual(data["status"], "warn")

    def test_infra_pid_file_is_recorded_not_unregistered(self):
        self.write_required_alive()
        (self.soren / "tmp" / "state" / "start_all.pid").write_text(f"{self.alive_pid()}\n")
        proc = self.run_collector()
        data = json.loads(proc.stdout)
        self.assertNotIn("start_all", data["workers"]["unregistered"])
        self.assertEqual(data["workers"]["details"]["start_all"]["infra"], True)
        self.assertEqual(data["workers"]["details"]["start_all"]["alive"], True)

    def test_lane_guard_dirs_are_not_lanes(self):
        self.write_required_alive()
        base = self.soren / "tmp" / "state" / ".ai_generation_locks"
        (base / "radio.owner_guard.lock").mkdir(parents=True, exist_ok=True)
        (base / "radio.owner_guard.d").mkdir(parents=True, exist_ok=True)
        proc = self.run_collector()
        data = json.loads(proc.stdout)
        lanes = data["queues"]["lanes"]
        self.assertIn("radio", lanes)
        self.assertNotIn("radio.owner_guard.lock", lanes)
        self.assertNotIn("radio.owner_guard.d", lanes)

    def test_paused_required_worker_is_warn_not_critical(self):
        self.write_required_alive(skip=("radio_worker",))
        self.write_pid("radio_worker", self.alive_pid())
        (self.soren / "tmp" / "state" / "radio_worker.paused").write_text("{}\n")
        proc = self.run_collector()
        data = json.loads(proc.stdout)
        self.assertIn("radio_worker", data["workers"]["paused"])
        self.assertNotIn("radio_worker", data["workers"]["required_down"])
        self.assertEqual(data["status"], "warn")

    def test_stale_queue_lock_is_suspected_but_kept(self):
        self.write_required_alive()
        self.write_lock("radio", 99999999, age_sec=2000)
        before = (self.soren / "tmp" / "state" / ".ai_generation_locks" / "radio" / "owner").read_bytes()
        proc = self.run_collector()
        data = json.loads(proc.stdout)
        lane = data["queues"]["lanes"]["radio"]
        self.assertEqual(lane["locked"], True)
        self.assertEqual(lane["owner_alive"], False)
        self.assertEqual(lane["stale_suspected"], True)
        self.assertEqual(data["queues"]["stale_locks"], 1)
        after = (self.soren / "tmp" / "state" / ".ai_generation_locks" / "radio" / "owner").read_bytes()
        self.assertEqual(before, after)

    def test_owner_token_never_leaves_the_vm(self):
        self.write_required_alive()
        self.write_lock("comment", self.alive_pid(), token="tok_SUPERSECRET_abc123")
        proc = self.run_collector()
        self.assertNotIn("SUPERSECRET", proc.stdout)
        data = json.loads(proc.stdout)
        self.assertNotIn("token", json.dumps(data["queues"]))

    def test_rate_limit_fallback_allfailed_and_giveup(self):
        self.write_required_alive()
        self.write_stats(
            [
                self.event("attempt", label="RADIO"),
                self.event("fail", label="RADIO", agent="opencode:m1", rc="1", error="boom"),
                self.event("fail", label="RADIO", agent="opencode:m1", rc="79", error="429 slow down"),
                self.event("winner", label="RADIO", agent="amd:DeepSeek-V4-Flash", rc="0"),
                self.event("attempt", label="COMMENT"),
                self.event("all_failed", label="COMMENT", agent=""),
                self.event("queue_giveup", label="RADIO"),
            ]
        )
        proc = self.run_collector()
        data = json.loads(proc.stdout)
        ai = data["ai"]
        self.assertEqual(ai["attempts"], 2)
        self.assertEqual(ai["failures"], 2)
        self.assertEqual(ai["rate_limits"], 1)
        self.assertEqual(ai["fallbacks"], 1)
        self.assertEqual(ai["all_failed"], 1)
        self.assertEqual(ai["queue_giveups"], 1)
        self.assertEqual(data["queues"]["queue_giveups_15m"], 1)
        kinds = [e["event"] for e in ai["recent_events"]]
        self.assertIn("fail", kinds)
        self.assertIn("winner", kinds)
        self.assertIn("all_failed", kinds)
        self.assertNotIn("attempt", kinds)
        fail_event = next(e for e in ai["recent_events"] if e["event"] == "fail" and e["rc"] == "79")
        self.assertEqual(fail_event["provider"], "opencode")
        self.assertEqual(data["status"], "warn")

    def test_malformed_telemetry_is_counted_not_fatal(self):
        self.write_required_alive()
        stats = self.soren / "tmp" / "state" / "ai_stats"
        stats.mkdir(parents=True, exist_ok=True)
        day = time.strftime("%Y%m%d", time.localtime(self.now))
        with open(stats / f"{day}.jsonl", "w", encoding="utf-8") as handle:
            handle.write("not json at all\n")
            handle.write("[1,2,3]\n")
            handle.write(json.dumps(self.event("ok")) + "\n")
        proc = self.run_collector()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["ai"]["malformed_lines"], 2)
        self.assertEqual(data["ai"]["successes"], 1)

    def test_secret_like_error_preview_is_redacted(self):
        self.write_required_alive()
        self.write_stats(
            [self.event("fail", rc="1", error="call failed api_key=AKIAIOSFODNN7EXAMPLE Bearer abc.def.ghi")]
        )
        proc = self.run_collector()
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", proc.stdout)
        self.assertNotIn("abc.def.ghi", proc.stdout)
        data = json.loads(proc.stdout)
        preview = next(e for e in data["ai"]["recent_events"] if e["event"] == "fail")["error_preview"]
        self.assertIn("[REDACTED]", preview)

    def test_huge_telemetry_stays_bounded(self):
        self.write_required_alive()
        stats = self.soren / "tmp" / "state" / "ai_stats"
        stats.mkdir(parents=True, exist_ok=True)
        day = time.strftime("%Y%m%d", time.localtime(self.now))
        with open(stats / f"{day}.jsonl", "w", encoding="utf-8") as handle:
            for _ in range(60000):
                handle.write(json.dumps(self.event("fail", rc="1", error="x" * 5000)) + "\n")
        proc = self.run_collector()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertLessEqual(len(proc.stdout.encode()), 32768)
        data = json.loads(proc.stdout)
        self.assertLessEqual(len(data["ai"]["recent_events"]), 20)

    def test_improve_loop_states(self):
        self.write_required_alive()
        (self.soren / "tmp" / "improve.lock").write_text("")
        (self.soren / "tmp" / "state" / "improve_state.json").write_text(
            json.dumps(
                {
                    "status": "running",
                    "pid": self.alive_pid(),
                    "phase": "harvest",
                    "started_at": self.now - 100,
                    "updated_at": self.now - 10,
                }
            )
        )
        proc = self.run_collector()
        data = json.loads(proc.stdout)
        imp = data["improvement"]
        self.assertEqual(imp["running"], True)
        self.assertEqual(imp["pid_alive"], True)
        self.assertEqual(imp["stale"], False)
        self.assertEqual(imp["lock_present"], True)

    def test_stale_improve_state_is_warn(self):
        self.write_required_alive()
        (self.soren / "tmp" / "state" / "improve_state.json").write_text(
            json.dumps({"status": "running", "pid": 99999999, "started_at": self.now - 9000,
                        "updated_at": self.now - 9000})
        )
        proc = self.run_collector()
        data = json.loads(proc.stdout)
        self.assertEqual(data["improvement"]["stale"], True)
        self.assertEqual(data["status"], "warn")

    def test_backoff_and_retry_signals(self):
        self.write_required_alive()
        (self.soren / "tmp" / "state" / "rate_limit_backoff").write_text("x")
        (self.soren / "tmp" / "state" / "improve_retry_batch.json").write_text("{}")
        proc = self.run_collector()
        data = json.loads(proc.stdout)
        self.assertEqual(data["improvement"]["backing_off"], True)
        self.assertEqual(data["improvement"]["retry_pending"], True)
        self.assertEqual(data["status"], "warn")

    def test_usage_error_without_args(self):
        direct = subprocess.run(["python3", str(COLLECTOR)], capture_output=True, text=True, timeout=60)
        self.assertNotEqual(direct.returncode, 0)


class ProgramCornerStateTests(CollectorFixture):
    def state_dir(self):
        directory = self.soren / "program-state"
        (directory / "trading").mkdir(parents=True, exist_ok=True)
        return directory

    def write_state(self, directory, name, payload):
        (directory / name).write_text(json.dumps(payload, ensure_ascii=False))

    def test_corner_state_reports_lifecycle_without_bodies(self):
        module = load_collector()
        directory = self.state_dir()
        requests = directory / "game-switch" / "requests"
        requests.mkdir(parents=True, exist_ok=True)
        self.write_state(
            directory,
            "game_switch.json",
            {
                "schema_version": 2,
                "phase": "ready",
                "operation": None,
                "next_generation": 68,
                "revision": 12,
                "active": {"game": "sorengame", "generation": 67},
                "last_result": {"status": "succeeded", "error_code": None, "to_game": "sorengame"},
                "updated_at": "2026-09-10T10:00:00Z",
            },
        )
        self.write_state(
            directory,
            "retro_corner.json",
            {
                "schema_version": 2,
                "status": "active",
                "date": "2026-09-10",
                "game": "gnurobots",
                "previous_game": "sorengame",
                "started_at": "2026-09-10T19:00:30+09:00",
                "ends_at": "2026-09-10T19:30:30+09:00",
                "completed_at": None,
                "last_error": None,
            },
        )
        self.write_state(
            requests,
            "queued-request.json",
            {
                "status": "queued",
                "operation": "switch",
                "target": "moon-buggy",
                "generation": 3,
                "created_at": "2026-09-10T10:00:00+00:00",
            },
        )
        self.write_state(
            requests,
            "done-request.json",
            {
                "status": "succeeded",
                "operation": "switch",
                "target": "ninvaders",
                "generation": 2,
                "created_at": "2026-09-10T09:00:00+00:00",
            },
        )
        self.write_state(
            directory,
            "paper_corner.json",
            {
                "status": "active",
                "date": "2026-09-10",
                "previous_game": "sorengame",
                "started_at": 1789000000.0,
                "ends_at": 1789001800.0,
                "last_error": None,
                "reports": {
                    "opening": {"text": "SECRET-BODY-TEXT", "overlay": True, "speech": True},
                    "0": {"text": "SECRET-BODY-TEXT-2", "overlay": True, "speech": False},
                },
            },
        )
        self.write_state(
            directory / "trading",
            "presentation.json",
            {"schema_version": 1, "mode": "detailed", "updated_at": 1789000000.0},
        )

        result = module._collect_programs(directory, self.soren, self.now)
        self.assertEqual(result["state_dir_found"], True)
        game_switch = result["game_switch"]
        self.assertEqual(game_switch["present"], True)
        self.assertEqual(game_switch["phase"], "ready")
        self.assertEqual(game_switch["active_game"], "sorengame")
        self.assertEqual(game_switch["active_generation"], 67)
        self.assertEqual(game_switch["last_status"], "succeeded")
        self.assertIsNone(game_switch["last_error_code"])
        fifo = result["game_switch_fifo"]
        self.assertEqual(fifo["queued_count"], 1)
        self.assertEqual(fifo["terminal_count"], 1)
        self.assertEqual(fifo["head"]["operation"], "switch")
        self.assertEqual(fifo["head"]["target"], "moon-buggy")

        retro = result["retro_corner"]
        self.assertEqual(retro["present"], True)
        self.assertEqual(retro["status"], "active")
        self.assertEqual(retro["game"], "gnurobots")
        self.assertEqual(retro["previous_game"], "sorengame")
        self.assertEqual(retro["recovery_required"], False)

        paper = result["paper_corner"]
        self.assertEqual(paper["present"], True)
        self.assertEqual(paper["status"], "active")
        self.assertEqual(paper["announcements"], {"total": 2, "overlay": 2, "speech": 1})
        self.assertEqual(result["presentation"]["mode"], "detailed")

        rendered = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("SECRET-BODY-TEXT", rendered)
        self.assertNotIn("reports", rendered)

    def test_corner_state_absent_is_not_present(self):
        module = load_collector()
        result = module._collect_programs(self.soren / "nonexistent-state", self.soren, self.now)
        self.assertEqual(result["state_dir_found"], False)
        self.assertEqual(result["game_switch"]["present"], False)
        self.assertEqual(result["paper_corner"]["present"], False)
        self.assertIn("boundary", result)
        self.assertIn("ab", result)

    def test_boundary_freshness_is_reported(self):
        module = load_collector()
        root = self.soren / "tmp" / "state"
        root.mkdir(parents=True, exist_ok=True)
        (root / "corner_boundary_improvement.json").write_text(
            json.dumps({"completed_at": self.now - 300})
        )
        result = module._collect_programs(self.soren / "nonexistent-state", self.soren, self.now)
        boundary = result["boundary"]
        self.assertEqual(boundary["improvement"]["present"], True)
        self.assertEqual(boundary["improvement"]["age_sec"], 300)
        self.assertEqual(boundary["prediction"]["present"], False)
        self.assertEqual(boundary["prediction"]["age_sec"], -1)

    def test_ab_state_is_reported_without_hashes_or_env(self):
        module = load_collector()
        root = self.soren / "tmp" / "state"
        (root / "ab_candidate").mkdir(parents=True, exist_ok=True)
        (root / "ab_candidate" / "meta.json").write_text("{}")
        (root / "ab_candidate" / "strategy.py").write_text("SECRET-STRATEGY-BODY\n")
        (root / "ab_state.json").write_text(
            json.dumps(
                {
                    "pattern": "ABBA",
                    "started_at": "2026-09-10T18:02:00",
                    "games_recorded": 12,
                    "game_num_start": 50140,
                    "a_hash": "a" * 40,
                    "b_hash": "b" * 40,
                    "a_env": "SECRET_ENV=A",
                }
            )
        )
        (root / "ab_games.jsonl").write_text(
            "\n".join(
                [
                    json.dumps({"arm": "A", "tainted": False}),
                    json.dumps({"arm": "B", "tainted": True}),
                ]
            )
            + "\n"
        )
        result = module._collect_programs(self.soren / "nonexistent-state", self.soren, self.now)
        ab = result["ab"]
        self.assertEqual(ab["state_present"], True)
        self.assertEqual(ab["pattern"], "ABBA")
        self.assertEqual(ab["games_recorded"], 12)
        self.assertEqual(ab["game_num_start"], 50140)
        self.assertEqual(ab["games_lines"], 2)
        self.assertEqual(ab["games_tainted"], 1)
        self.assertEqual(ab["last_arm"], "B")
        self.assertEqual(ab["candidate_pending"], True)
        rendered = json.dumps(ab)
        self.assertNotIn("SECRET-STRATEGY-BODY", rendered)
        self.assertNotIn("SECRET_ENV", rendered)
        self.assertNotIn("a" * 40, rendered)
        self.assertNotIn("b" * 40, rendered)

    def test_corner_state_corrupt_is_not_fatal(self):
        module = load_collector()
        directory = self.state_dir()
        (directory / "game_switch.json").write_text("{not json")
        (directory / "paper_corner.json").write_text("[1,2,3]")
        result = module._collect_programs(directory, self.soren, self.now)
        self.assertEqual(result["game_switch"]["present"], True)
        self.assertEqual(result["game_switch"]["readable"], False)
        self.assertEqual(result["paper_corner"]["present"], True)
        self.assertEqual(result["paper_corner"]["readable"], False)

    def test_corner_error_is_redacted(self):
        module = load_collector()
        directory = self.state_dir()
        self.write_state(
            directory,
            "paper_corner.json",
            {"status": "failed", "date": "2026-09-10", "last_error": "boom token=SUPERSECRET123"},
        )
        result = module._collect_programs(directory, self.soren, self.now)
        rendered = json.dumps(result)
        self.assertNotIn("SUPERSECRET123", rendered)
        self.assertIn("[REDACTED]", rendered)

    def test_recovery_required_corner_is_classified_without_exposing_request_identity(self):
        module = load_collector()
        directory = self.state_dir()
        self.write_state(
            directory,
            "retro_corner.json",
            {
                "status": "failed",
                "game": "ninvaders",
                "last_error_code": "recovery_required",
                "last_error": "canonical stateの復旧が必要です (`docich recover`)",
            },
        )
        result = module._collect_programs(directory, self.soren, self.now)
        self.assertEqual(result["retro_corner"]["recovery_required"], True)
        self.assertEqual(result["retro_corner"]["last_error_code"], "recovery_required")
        self.assertIsNone(result["game_switch_fifo"]["head"])

    def test_nethack_corner_states_are_reported_and_redacted(self):
        module = load_collector()
        directory = self.state_dir()
        self.write_state(
            directory,
            "nethack_corner_manual.json",
            {
                "status": "failed",
                "game": "nethack",
                "previous_game": "sorengame",
                "last_error": "restore failed token=SUPERSECRET123",
            },
        )
        self.write_state(
            directory,
            "nethack_corner.json",
            {"status": "completed", "game": "nethack", "previous_game": "sorengame"},
        )
        result = module._collect_programs(directory, self.soren, self.now)
        manual = result["nethack_corner_manual"]
        self.assertEqual(manual["status"], "failed")
        self.assertEqual(manual["previous_game"], "sorengame")
        self.assertIn("last_error", manual)
        rendered = json.dumps(result)
        self.assertNotIn("SUPERSECRET123", rendered)
        self.assertIn("[REDACTED]", rendered)
        self.assertEqual(result["nethack_corner"]["status"], "completed")

    def test_full_output_includes_corners_section(self):
        self.write_required_alive()
        proc = self.run_collector()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertIn("corners", data)
        self.assertIn("game_switch", data["corners"])
        self.assertIn("state_dir_found", data["corners"])
        self.assertIn("boundary", data["corners"])
        self.assertIn("ab", data["corners"])
        self.assertIn("improvement", data["corners"]["boundary"])


class NethackAgentLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="vmops-agent-log-")
        self.state = Path(self.tmp.name) / "run-soren-live"
        (self.state / "logs").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_log_is_absent_not_an_error(self):
        module = load_collector()
        entry = module._collect_nethack_agent_log(self.state, int(time.time()))
        self.assertFalse(entry["present"])
        self.assertFalse(entry["readable"])
        self.assertEqual(entry["lines"], [])

    def test_tail_is_bounded_and_redacted(self):
        module = load_collector()
        path = self.state / "logs" / "agent.log"
        path.write_text(
            "".join(f"line {i} token=sekret{i}\n" for i in range(20)),
            encoding="utf-8",
        )
        entry = module._collect_nethack_agent_log(self.state, int(time.time()))
        self.assertTrue(entry["present"])
        self.assertTrue(entry["readable"])
        self.assertEqual(len(entry["lines"]), module.NETHACK_AGENT_LOG_LINES)
        self.assertIn("line 19", entry["lines"][-1])
        self.assertNotIn("sekret", " ".join(entry["lines"]))


class NethackPaneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="vmops-panes-")
        self.state = Path(self.tmp.name) / "run-soren-live"
        self.state.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _write_switch(self, active):
        (self.state / "game_switch.json").write_text(
            json.dumps({"phase": "ready", "active": active}), encoding="utf-8"
        )

    def test_absent_game_switch_is_reported(self):
        module = load_collector()
        entry = module._collect_nethack_panes(self.state, int(time.time()))
        self.assertFalse(entry["present"])
        self.assertEqual(entry["game"], [])

    def test_only_committed_nethack_runtime_is_captured(self):
        module = load_collector()
        self._write_switch(
            {"game": "sorengame", "adapter_session": "s", "game_window": "w", "generation": 3}
        )
        with mock.patch.object(module.subprocess, "run") as run:
            entry = module._collect_nethack_panes(self.state, int(time.time()))
        self.assertEqual(entry["active_game"], "sorengame")
        run.assert_not_called()
        self.assertEqual(entry["game"], [])
        self.assertEqual(entry["agent"], [])

    def test_process_window_is_captured_read_only(self):
        module = load_collector()
        self._write_switch(
            {
                "game": "nethack",
                "adapter_session": "docich-game-g9",
                "game_window": "game-g9",
                "generation": 9,
            }
        )
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            if argv[1] == "list-windows":
                return mock.Mock(returncode=0, stdout="game-g9\nnethack\nagent-g9\n")
            if argv[1] == "capture-pane":
                return mock.Mock(returncode=0, stdout="Shall I pick a character? [yn]\n")
            return mock.Mock(returncode=1, stdout="")

        with mock.patch.object(module.subprocess, "run", side_effect=fake_run):
            entry = module._collect_nethack_panes(self.state, int(time.time()))
        targets = [call[-1] for call in calls if call[1] == "capture-pane"]
        self.assertIn("docich-game-g9:nethack", targets)
        self.assertIn("docich-game-g9:agent-g9", targets)
        self.assertEqual(entry["game"], ["Shall I pick a character? [yn]"])
        self.assertIn("nethack", entry["windows"])


class RotationTimerUnitProjectionTests(unittest.TestCase):
    """The corner_rotation_timer projection follows the reviewed unit rename."""

    def setUp(self):
        self.module = load_collector()
        self.tmp = tempfile.TemporaryDirectory(prefix="vmops-rotation-unit-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.prod = self.base / "docich"
        self.prod.mkdir()
        self.unit_dir = self.base / ".config" / "systemd" / "user"
        self.unit_dir.mkdir(parents=True)
        self.soren = self.base / "soren"
        (self.soren / "tmp" / "state").mkdir(parents=True)

    def test_pre_migration_reports_the_legacy_unit_without_an_alias(self):
        (self.unit_dir / "docich-retro-corner.timer").write_text("[Timer]\n")
        with mock.patch.object(self.module, "PROD_ROOT", self.prod):
            unit, legacy_alias = self.module._rotation_timer_selection()
        self.assertEqual(unit, "docich-retro-corner.timer")
        self.assertIs(legacy_alias, False)

    def test_post_migration_reports_the_canonical_unit_and_alias(self):
        (self.unit_dir / "docich-corner-rotation.timer").write_text("[Timer]\n")
        (self.unit_dir / "docich-retro-corner.timer").symlink_to(
            "docich-corner-rotation.timer"
        )
        with mock.patch.object(self.module, "PROD_ROOT", self.prod):
            unit, legacy_alias = self.module._rotation_timer_selection()
        self.assertEqual(unit, "docich-corner-rotation.timer")
        self.assertIs(legacy_alias, True)

    def test_wrong_alias_target_is_not_reported_as_migrated(self):
        (self.unit_dir / "docich-corner-rotation.timer").write_text("[Timer]\n")
        (self.unit_dir / "docich-retro-corner.timer").symlink_to("somewhere-else.timer")
        with mock.patch.object(self.module, "PROD_ROOT", self.prod):
            unit, legacy_alias = self.module._rotation_timer_selection()
        self.assertEqual(unit, "docich-corner-rotation.timer")
        self.assertIs(legacy_alias, False)

    def test_missing_unit_dir_fails_closed_to_the_legacy_name(self):
        unit, legacy_alias = self.module._rotation_timer_selection(
            unit_dir=self.unit_dir / "missing"
        )
        self.assertEqual(unit, "docich-retro-corner.timer")
        self.assertIs(legacy_alias, False)

    def test_programs_projection_keeps_fixed_keys_and_bounded_alias_boolean(self):
        (self.unit_dir / "docich-corner-rotation.timer").write_text("[Timer]\n")
        (self.unit_dir / "docich-retro-corner.timer").symlink_to(
            "docich-corner-rotation.timer"
        )
        state_dir = self.prod / "run-soren-live"
        state_dir.mkdir()
        (state_dir / "corner_rotation.json").write_text(
            json.dumps(
                {
                    "status": "waiting",
                    "seed": "DO-NOT-PUBLISH-SEED",
                    "pending": {"request_id": "DO-NOT-PUBLISH-REQUEST"},
                }
            )
        )
        with mock.patch.object(self.module, "PROD_ROOT", self.prod), mock.patch.object(
            self.module, "_unit_is_active", return_value=True
        ), mock.patch.object(self.module, "_unit_is_enabled", return_value=True):
            result = self.module._collect_programs(state_dir, self.soren, 100)
        for key in ("corner_rotation", "corner_rotation_timer", "retro_corner"):
            self.assertIn(key, result)
        timer = result["corner_rotation_timer"]
        self.assertEqual(timer["unit"], "docich-corner-rotation.timer")
        self.assertIs(timer["active"], True)
        self.assertIs(timer["enabled"], True)
        self.assertIs(timer["legacy_alias"], True)
        self.assertNotIn("DO-NOT-PUBLISH", json.dumps(result))
        self.assertNotIn(str(self.base), json.dumps(result))


def test_rotation_latch_projects_fixed_identity_without_request_id(tmp_path):
    """#986: an operator can see which reservation is latched, never its request id."""
    module = load_collector()
    (tmp_path / "corner_rotation.json").write_text(json.dumps({
        "status": "recovery_required",
        "reason": "execution-or-state-unverified",
        "error_kind": "execution-unverified",
        "seed": "DO-NOT-PUBLISH-SEED",
        "slot": 9, "eligible": ["nsnake", "paper"],
        "last_seen_at": 100, "next_due_at": 100, "last_slot_at": 90,
        "pending": {"corner": "nsnake", "phase": "dispatched", "selected_at": 40.0,
                    "request_id": "DO-NOT-PUBLISH-REQUEST",
                    "prompt": "DO-NOT-PUBLISH-BODY"},
    }))
    (tmp_path / "retro_corner.json").write_text(json.dumps({
        "game": "nsnake", "status": "active",
        "rotation_request_id": "DO-NOT-PUBLISH-REQUEST",
        "save": "SECRET-SAVE",
    }))
    output = {}
    module._collect_corner_files(tmp_path, output, 100)
    projection = output["corner_rotation"]
    assert projection["status"] == "recovery_required"
    assert projection["error_kind"] == "execution-unverified"
    assert projection["pending"] is True
    assert projection["pending_corner"] == "nsnake"
    assert projection["pending_phase"] == "dispatched"
    assert projection["pending_age_sec"] == 60
    # the reservation is bound to the corner state that already recorded it
    assert projection["pending_owner"] == "retro_corner"
    assert projection["pending_owner_status"] == "active"
    assert "DO-NOT-PUBLISH" not in json.dumps(output)
    assert "SECRET" not in json.dumps(output)


def test_rotation_pending_owner_never_claims_none_from_unreadable_state(tmp_path):
    module = load_collector()
    (tmp_path / "corner_rotation.json").write_text(json.dumps({
        "status": "recovery_required", "error_kind": "unknown-kind",
        "pending": {"corner": "paper", "phase": "selected", "selected_at": 0,
                    "request_id": "req-1"},
    }))
    (tmp_path / "retro_corner.json").write_text("{ not json")
    output = {}
    module._collect_corner_files(tmp_path, output, 10)
    projection = output["corner_rotation"]
    assert projection["pending_owner"] == "unknown"
    assert projection["pending_owner_status"] == "unknown"
    assert projection["error_kind"] == "unknown"
    assert projection["pending_age_sec"] == 10


def test_rotation_projection_without_a_reservation_stays_absent(tmp_path):
    module = load_collector()
    (tmp_path / "corner_rotation.json").write_text(json.dumps({
        "status": "waiting", "reason": "not-due", "last_seen_at": 100,
        "next_due_at": 200, "slot": 4,
    }))
    output = {}
    module._collect_corner_files(tmp_path, output, 100)
    projection = output["corner_rotation"]
    assert projection["pending"] is False
    assert projection["pending_corner"] is None
    assert projection["pending_phase"] is None
    assert projection["pending_age_sec"] == -1
    assert projection["pending_owner"] == "absent"
    assert projection["error_kind"] is None


def test_rotation_error_kind_taxonomy_matches_the_durable_ledger():
    import sys

    sys.path.insert(0, str(ROOT / "src"))
    from docich.corner_rotation import ERROR_KINDS

    assert load_collector().ROTATION_ERROR_KINDS == ERROR_KINDS


def test_a_latched_common_rotation_reaches_warn_severity():
    module = load_collector()
    workers = {"required_down": [], "required_stale": [], "paused": [],
               "unregistered": [], "duplicates": [], "zombies": []}
    queues = {"stale_locks": 0}
    ai = {"all_failed": 0, "queue_giveups": 0}
    improvement = {"stale": False, "retry_pending": False}
    quiet = lambda corners: module._severity(workers, queues, ai, improvement, corners)
    assert quiet({"corner_rotation": {"status": "waiting"}}) == "ok"
    # the shared plane can be healthy while every automatic corner is stopped
    assert quiet({"corner_rotation": {"status": "recovery_required"}}) == "warn"


if __name__ == "__main__":
    unittest.main()
