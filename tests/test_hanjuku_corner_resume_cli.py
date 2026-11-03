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


def test_resume_recovery_refusals_map_to_fixed_exit_codes():
    from docich import hanjuku_corner
    from docich.hanjuku_manual_cancel import CancelRefused

    expected = {
        "expected_reservation_required": 90,
        "reservation_not_in_scope": 91,
        "invalid_evidence": 92,
        "reservation_changed": 93,
        "context_changed": 94,
        "gameplay_started": 95,
        "startup_rollback_unverified": 96,
        "switch_not_stable": 97,
        "newer_progress_unverified": 98,
        "target_paused_or_unavailable": 99,
        "checkpoint_changed": 100,
        "owner_not_terminal": 101,
        "retry_exhausted": 102,
        "unsafe_state": 103,
        "missing_evidence": 104,
        "unreadable_evidence": 105,
        "unsafe_lock": 106,
        "busy": 107,
        "lock_unavailable": 108,
        "runtime_resources_present": 109,
        "clock_regressed": 72,
        "manual_queue_conflict": 74,
    }
    for reason, code in expected.items():
        assert hanjuku_corner._fixed_failure_exit(CancelRefused(reason)) == code
        assert hanjuku_corner.EXIT_FAILURE_REASON[code] == reason


def test_unknown_resume_refusal_never_leaks_exception_text(monkeypatch, capsys):
    from docich import hanjuku_corner
    from docich.hanjuku_manual_cancel import CancelRefused

    def refuse(*, resume_latest=False):
        assert resume_latest is True
        raise CancelRefused("PRIVATE checkpoint /path must not escape")

    monkeypatch.setattr(hanjuku_corner, "start", refuse)
    assert hanjuku_corner.main(["--resume-latest"]) == 77
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "hanjuku_queue_failure=queue_rejected\n"
    assert "PRIVATE" not in captured.err
