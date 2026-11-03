from types import SimpleNamespace


def _patch_start(monkeypatch, *, recovered):
    from docich import hanjuku_corner
    import docich.corner_rotation as rotation_module
    import docich.hanjuku_resume_recovery as recovery_module

    g = SimpleNamespace(state_dir="/tmp/unused")
    calls = {"recovery": 0, "queue": []}

    # hanjuku_corner imports load_global directly, so patch the symbol actually
    # used by start() rather than docich.config.load_global.
    monkeypatch.setattr(hanjuku_corner, "load_global", lambda *args, **kwargs: g)

    def recover(config):
        assert config is g
        calls["recovery"] += 1
        return recovered

    monkeypatch.setattr(recovery_module, "queue_failed_start", recover)

    class Manager:
        def __init__(self, config):
            assert config is g

        def queue_manual(self, game, **kwargs):
            calls["queue"].append((game, kwargs))
            return {"status": "queued", "corner": "hanjuku-hero", "request_id": "queued"}

    monkeypatch.setattr(rotation_module, "CornerRotationManager", Manager)
    return calls


def test_resume_cli_recovers_failed_before_ai_before_new_manual_queue(monkeypatch):
    from docich import hanjuku_corner

    recovered = {"status": "queued", "corner": "hanjuku-hero", "request_id": "recovery"}
    calls = _patch_start(monkeypatch, recovered=recovered)

    assert hanjuku_corner.start(resume_latest=True) is recovered
    assert calls["recovery"] == 1
    assert calls["queue"] == []


def test_resume_cli_falls_back_to_normal_verified_resume_when_no_failed_start(monkeypatch):
    from docich import hanjuku_corner

    calls = _patch_start(monkeypatch, recovered=None)

    result = hanjuku_corner.start(resume_latest=True)
    assert result["status"] == "queued"
    assert calls["recovery"] == 1
    assert calls["queue"] == [("hanjuku-hero", {"resume_latest": True})]


def test_fresh_start_does_not_touch_checkpoint_recovery(monkeypatch):
    from docich import hanjuku_corner

    calls = _patch_start(monkeypatch, recovered={"status": "queued"})

    result = hanjuku_corner.start()
    assert result["status"] == "queued"
    assert calls["recovery"] == 0
    assert calls["queue"] == [("hanjuku-hero", {})]
