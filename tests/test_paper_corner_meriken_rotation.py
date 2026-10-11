"""Real rotation/adapter/CLI routing with local queues and simulated runtime IO."""
import datetime as dt
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import corner_adapters, corner_boundary, corner_rotation, webui
from docich import paper_corner_manual, paper_corner_operator
from docich.config import load_global
from docich.game_switch import CanonicalStateStore
from docich.paper_corner import PaperCornerManager
from docich.paper_corner_fast import FastPaperCornerManager
from docich.trading import soren_output


@pytest.mark.parametrize("entry", ["scheduled", "manual", "operator"])
@pytest.mark.parametrize("crash_after_publish", [False, True])
@pytest.mark.parametrize("date", ["2028-01-01", "2028-02-29", "2028-12-31"])
def test_rotation_paper_real_entries_keep_meriken_and_delivery_identity(
    tmp_path, monkeypatch, entry, crash_after_publish, date,
):
    config = tmp_path / "config.toml"
    config.write_text(
        '[paths]\nstate_dir="run"\n'
        '[trading]\npaper_worker_enabled=true\nnotifications_enabled=true\n'
        'notification_speech_enabled=true\n'
        f'[webui]\nsoren_root="{tmp_path / "soren"}"\n'
        '[paper_corner]\nenabled=true\ntimezone="UTC"\n'
        '[corner_rotation]\nenabled=true\n'
        '[[corner_rotation.corners]]\nid="paper"\nadapter="paper"\n'
        'game="paper-view"\nlive_eligible=false\n',
        encoding="utf-8",
    )
    g = load_global(tmp_path, config)
    now = dt.datetime.fromisoformat(date).replace(tzinfo=dt.timezone.utc).timestamp()
    current = [None]
    overlays, executions, transitions, deliveries = [], [], [], []
    monkeypatch.delenv("SOREN91_VOICEVOX_SPEAKER", raising=False)
    monkeypatch.setattr(CanonicalStateStore, "load", lambda self: ({
        "phase": "ready" if current[0] else "idle",
        "active": {"game": current[0], "runtime_id": "g1-paper"} if current[0] else None,
    }, False))
    # Keep the real program-slot lock; simulate only the external game's boundary.
    monkeypatch.setattr(corner_boundary, "boundary_ready", lambda *a, **k: True)

    def start(game, request_id=None):
        transitions.append(("start", game, request_id))
        current[0] = game
        return SimpleNamespace(status="succeeded")

    def stop(request_id=None):
        transitions.append(("stop", current[0], request_id))
        current[0] = None
        return SimpleNamespace(status="succeeded")

    init = PaperCornerManager.__init__

    def local_init(self, config, **kwargs):
        # The production speech default is deliberately left untouched.
        init(self, config, clock=lambda: now, sleep=lambda _: None,
             overlay=lambda _g, event: overlays.append(event),
             coordinator=SimpleNamespace(start=start, stop=stop),
             stream_game=lambda _: None, stream_paper=lambda: None, **kwargs)
        self._wait_for_speech = lambda state: True
        self._spawn_improve_once = lambda state: None

    monkeypatch.setattr(PaperCornerManager, "__init__", local_init)
    rotation_init = corner_rotation.CornerRotationManager.__init__

    def local_rotation_init(self, config, **kwargs):
        rotation_init(self, config, clock=lambda: now, sleep=lambda _: None, **kwargs)

    monkeypatch.setattr(corner_rotation.CornerRotationManager, "__init__", local_rotation_init)
    execute = corner_adapters.CornerExecutionCoordinator.execute

    def record_execution(self, adapter, request):
        executions.append((type(adapter).__name__, dict(request)))
        return execute(self, adapter, request)

    monkeypatch.setattr(corner_adapters.CornerExecutionCoordinator, "execute", record_execution)
    enqueue = webui._enqueue_audio_text
    crashed = []

    def publish_then_crash(*args, **kwargs):
        result = enqueue(*args, **kwargs)
        deliveries.append((kwargs["delivery_key"], kwargs["speaker"], result["dedup"]))
        if crash_after_publish and not crashed and kwargs["delivery_key"].endswith(":script:4"):
            crashed.append(True)
            raise SystemExit("simulated death after durable queue publication, before report ACK")
        return result

    monkeypatch.setattr(webui, "_enqueue_audio_text", publish_then_crash)

    def resume_after_crash(run):
        if crash_after_publish:
            with pytest.raises(SystemExit, match="simulated death"):
                run()
        return run()

    if entry == "scheduled":
        assert resume_after_crash(lambda: FastPaperCornerManager(g).tick()) == "ready"
        state_path = g.state_dir / "paper_corner.json"
        assert all(name == "PaperCornerAdapter" for name, _ in executions)
    elif entry == "manual":
        assert resume_after_crash(lambda: paper_corner_manual.ManualPaperCornerManager(g).start()) == "completed"
        state_path = g.state_dir / "paper_corner_manual.json"
    else:
        monkeypatch.setattr(paper_corner_manual, "_repo_root", lambda: tmp_path)
        monkeypatch.setattr(paper_corner_operator, "_repo_root", lambda: tmp_path)
        monkeypatch.setattr(paper_corner_operator.time, "sleep", lambda _: None)

        def local_child(argv, **kwargs):
            assert argv[1:3] == ["-m", "docich.paper_corner_manual"]
            assert argv[3:] == ["--config", str(config), "start"]
            # Simulate the launched child and its restart using the actual CLI.
            assert resume_after_crash(lambda: paper_corner_manual.main(argv[3:])) == 0
            return SimpleNamespace(pid=12345, poll=lambda: None)

        monkeypatch.setattr(paper_corner_operator.subprocess, "Popen", local_child)
        assert paper_corner_operator.launch(config)["status"] == "started"
        state_path = g.state_dir / "paper_corner_manual.json"

    state = json.loads(state_path.read_text())
    request_id = executions[0][1]["request_id"]
    assert {request["request_id"] for _, request in executions} == {request_id}
    assert state["delivery_scope"] == f"corner-rotation:{request_id}"
    assert state["rotation_request_id"] == request_id
    assert state["rotation_runtime_id"] == "g1-paper"
    assert state["live_eligible"] is False
    assert state["status"] == "completed"
    assert state["date"] == date
    assert {key for key in state["reports"] if key.startswith("script:")} == {
        f"script:{slot}" for slot in range(1, 9)
    }
    keys = {f"corner-rotation:{request_id}:{date}:{key}" for key in state["reports"]}
    assert {key for key, _, _ in deliveries} == keys
    assert {event["source_id"] for event in overlays} == keys
    assert {speaker for _, speaker, _ in deliveries} == {"14"}
    assert sum(dedup for _, _, dedup in deliveries) == int(crash_after_publish)
    queue = tmp_path / "soren/tmp/.comment_queue"
    audio = list(queue.glob("*_crypto_paper.txt"))
    assert len(audio) == len(keys) == 10  # opening + eight slots + end
    assert all(Path(str(path) + ".speaker").read_text() == "14" for path in audio)
    assert all(state["reports"][f"script:{slot}"]["drained"] for slot in range(1, 9))
    before = {str(path.relative_to(queue)): path.read_bytes() for path in queue.rglob("*") if path.is_file()}
    # A terminal request replays without another transition or queue write.
    manager = (FastPaperCornerManager if entry == "scheduled" else paper_corner_manual.ManualPaperCornerManager)(g)
    assert manager.run_rotation(request_id) == "completed"
    assert {str(path.relative_to(queue)): path.read_bytes() for path in queue.rglob("*") if path.is_file()} == before
    assert [action for action, _, _ in transitions] == ["start", "stop"]
    assert current[0] is None


@pytest.mark.parametrize("owner", ["", "retro", "meriken", "weather", "paper-view"])
def test_shared_rotation_namespace_does_not_claim_other_corners(tmp_path, monkeypatch, owner):
    monkeypatch.setenv("SOREN91_VOICEVOX_SPEAKER", "314")
    monkeypatch.setattr(soren_output, "resolve_soren_root", lambda _g: tmp_path)
    g = load_global(tmp_path)
    key = "corner-rotation:other-owner:2026-10-05:script:1"
    text = "PAPER・暗号資産の模擬売買コーナーです。元の本文です。"
    assert soren_output.pick_paper_persona(key, corner_owner=owner) == "chuka"
    soren_output.enqueue_speech(g, text, event_id=key, corner_owner=owner)
    queue = tmp_path / "tmp/.comment_queue"
    audio, = queue.glob("*_crypto_paper.txt")
    assert audio.read_text().strip() == text
    assert not Path(str(audio) + ".speaker").exists()


def test_paper_owned_rotation_uses_same_text_rules_and_override_without_changing_key(tmp_path, monkeypatch):
    monkeypatch.setenv("SOREN91_VOICEVOX_SPEAKER", "314")
    monkeypatch.setattr(soren_output, "resolve_soren_root", lambda _g: tmp_path)
    g = load_global(tmp_path)
    key = "corner-rotation:paper-owner:2026-10-05:script:1"
    intro = "PAPER・暗号資産の模擬売買コーナーです。"
    text = intro + "結論からお伝えしますと、損益は確認待ちです。"
    assert soren_output.pick_paper_persona(key, corner_owner="paper") == "meriken"
    soren_output.enqueue_paper_corner_speech(g, text, event_id=key)
    soren_output.enqueue_paper_corner_speech(g, text, event_id=key)
    queue = tmp_path / "tmp/.comment_queue"
    audio, = queue.glob("*_crypto_paper.txt")
    assert audio.read_text().strip() == "損益は確認待ちです。"
    assert Path(str(audio) + ".speaker").read_text() == "314"
