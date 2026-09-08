"""Exercise the actual native shell writer together with docich, without OBS."""
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from docich.overlay_queue import append_event, load_events

REPO = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get("SOREN_OVERLAY_SOURCE", REPO / "games/soviet_now"))
DOCICH = "from pathlib import Path; from docich.overlay_queue import append_event; import sys; root=Path(sys.argv[1]); [append_event(root, {'category':'worker','title':f'PAPER {sys.argv[2]}-{i}','body':'模擬通知'}, keep=500, regenerate=False) for i in range(12)]"


def prepare(root):
    (root / "core").mkdir(parents=True)
    for name in ("overlay_notify.sh", "generate_event_overlay.py", "core/config.sh"):
        shutil.copyfile(SOURCE / name, root / name)
    obs = root / "obs_control.sh"
    obs.write_text("#!/bin/sh\nexit 0\n")
    obs.chmod(0o755)
    (root / "tmp/state").mkdir(parents=True)
    (root / "tmp/state/.migrated").touch()


def env(root, events):
    return dict(os.environ, PYTHONPATH=str(REPO / "src"), EXPLORE_MODE="0",
                EVENT_OVERLAY_EVENTS_FILE=str(events), EVENT_OVERLAY_KEEP_EVENTS="500",
                EVENT_OVERLAY_HTML_FILE=str(root / "tmp/state/event_overlay.html"),
                OVERLAY_NOTIFY_OBS_SHOW="0")


def finish(children):
    for child in children:
        if child.poll() is None:
            child.kill()
        child.communicate()


def test_docich_honors_shared_lock_without_mtime_stealing(tmp_path):
    events = tmp_path / "events.jsonl"
    lock_path = events.with_name(events.name + ".lock")
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        os.utime(lock_path, (1, 1))
        child = subprocess.Popen([sys.executable, "-c", DOCICH, str(tmp_path), "locked"],
                                 env=env(tmp_path, events), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            with pytest.raises(subprocess.TimeoutExpired):
                child.communicate(timeout=1)
            assert not events.exists()
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            _, err = child.communicate(timeout=10)
        assert child.returncode == 0, err.decode()
    assert len(events.read_text().splitlines()) == 12
    assert lock_path.is_file()


@pytest.mark.parametrize("custom_path", ["default", "absolute", "relative", "parent_alias"])
def test_native_and_docich_writers_have_no_lost_updates(tmp_path, custom_path):
    prepare(tmp_path)
    events = tmp_path / ("tmp/state/overlay_events.jsonl" if custom_path == "default" else "custom/events.jsonl")
    events.parent.mkdir(parents=True, exist_ok=True)
    native_events = events
    docich_events = events
    if custom_path == "relative":
        native_events = docich_events = Path("custom/events.jsonl")
    elif custom_path == "parent_alias":
        (tmp_path / "alias").symlink_to(events.parent, target_is_directory=True)
        native_events = tmp_path / "alias/events.jsonl"
    children = [subprocess.Popen([sys.executable, "-c", DOCICH, str(tmp_path), str(i)],
                                env=env(tmp_path, docich_events), stdout=subprocess.PIPE, stderr=subprocess.PIPE) for i in range(2)]
    children += [subprocess.Popen(["bash", str(tmp_path / "overlay_notify.sh"), "worker", f"native-{i}", "通知本文", "info"],
                                 env=env(tmp_path, native_events), stdout=subprocess.PIPE, stderr=subprocess.PIPE) for i in range(12)]
    try:
        for child in children:
            _, err = child.communicate(timeout=20)
            assert child.returncode == 0, err.decode()
    finally:
        finish(children)
    rows = [json.loads(x) for x in events.read_text().splitlines()]
    expected = {f"PAPER {i}-{j}" for i in range(2) for j in range(12)} | {f"native-{i}" for i in range(12)}
    assert {x["title"] for x in rows} == expected
    assert len(rows) == 36
    assert (tmp_path / "tmp/state/event_overlay.html").stat().st_size > 0


def test_dead_owner_releases_lock_without_removing_inode(tmp_path, monkeypatch):
    events = tmp_path / "events.jsonl"
    lock_path = events.with_name(events.name + ".lock")
    owner = subprocess.Popen([sys.executable, "-c", "import fcntl,sys,time; f=open(sys.argv[1],'a'); fcntl.flock(f,fcntl.LOCK_EX); print('locked',flush=True); time.sleep(30)", str(lock_path)], stdout=subprocess.PIPE, text=True)
    try:
        assert owner.stdout.readline().strip() == "locked"
        inode = lock_path.stat().st_ino
    finally:
        owner.kill()
        owner.communicate(timeout=5)
    monkeypatch.setenv("EVENT_OVERLAY_EVENTS_FILE", str(events))
    assert append_event(tmp_path, {"category":"worker", "title":"PAPER recovery", "body":"模擬"}, regenerate=False)
    assert len(load_events(tmp_path)) == 1
    assert lock_path.stat().st_ino == inode


def test_webui_delete_preserves_native_append_while_waiting(tmp_path, monkeypatch):
    from contextlib import contextmanager
    import threading
    from types import SimpleNamespace
    from docich import overlay_queue, webui

    events = tmp_path / "events.jsonl"
    monkeypatch.setenv("EVENT_OVERLAY_EVENTS_FILE", str(events))
    old = {"category":"worker", "title":"old", "body":"old"}
    new = {"category":"worker", "title":"native", "body":"new"}
    events.write_text(json.dumps(old) + "\n")
    attempting = threading.Event()
    original_lock = overlay_queue._overlay_lock
    @contextmanager
    def observed_lock(root):
        attempting.set()
        with original_lock(root):
            yield
    monkeypatch.setattr(overlay_queue, "_overlay_lock", observed_lock)
    monkeypatch.setattr(webui, "_regenerate_event_overlay", lambda root: True)
    responses = []
    handler = SimpleNamespace(soren_root=tmp_path, _send_json=lambda *args: responses.append(args),
                              _send_error_json=lambda *args: responses.append(args))
    with events.with_name(events.name + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        worker = threading.Thread(target=webui._Handler._handle_delete_overlay_event, args=(handler, "0"))
        worker.start()
        try:
            assert attempting.wait(5)
            # Simulate the native writer's completed append under the same lock.
            events.write_text(json.dumps(old) + "\n" + json.dumps(new) + "\n")
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            worker.join(10)
    assert not worker.is_alive()
    assert responses[0][0] == 200
    assert [row["title"] for row in load_events(tmp_path)] == ["native"]


def test_webui_busy_clear_preserves_409_response(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from docich import overlay_queue, webui
    events = tmp_path / "events.jsonl"
    monkeypatch.setenv("EVENT_OVERLAY_EVENTS_FILE", str(events))
    monkeypatch.setattr(overlay_queue, "LOCK_TIMEOUT_SECONDS", 0)
    responses = []
    handler = SimpleNamespace(soren_root=tmp_path, _send_json=lambda *args: responses.append(args),
                              _send_error_json=lambda *args: responses.append(args))
    with events.with_name(events.name + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        result = webui._Handler._handle_clear_overlay_events(handler)
    assert result == responses[0][0] == 409
    assert not events.exists()
