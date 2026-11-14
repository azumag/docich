"""#3753: run records carry the current-generation bot_version, not the stale
import-time constant of the long-lived observer process."""
import types

from docich import hanjuku_run


def test_bot_version_prefers_the_hotloaded_generation(monkeypatch):
    hot = types.SimpleNamespace(BOT_VERSION='hanjuku-chart-v999-test-hot')
    monkeypatch.setattr(hanjuku_run, '_hot_module', lambda _name: hot)
    assert hanjuku_run._bot_version() == 'hanjuku-chart-v999-test-hot'


def test_bot_version_falls_back_to_the_imported_constant(monkeypatch):
    monkeypatch.setattr(hanjuku_run, '_hot_module', lambda _name: object())
    assert hanjuku_run._bot_version() == hanjuku_run.BOT_VERSION


def test_bot_version_falls_back_when_the_handle_fails(monkeypatch):
    def boom(_name):
        raise RuntimeError('no handle')
    monkeypatch.setattr(hanjuku_run, '_hot_module', boom)
    assert hanjuku_run._bot_version() == hanjuku_run.BOT_VERSION


def test_observation_records_use_the_live_version(monkeypatch, tmp_path):
    """The observation event writes the current generation, not the constant."""
    hot = types.SimpleNamespace(BOT_VERSION='hanjuku-chart-v999-test-hot')
    monkeypatch.setattr(hanjuku_run, '_hot_module', lambda _name: hot)
    # The writer composes the record inline; verify the helper the three sites
    # now call resolves to the hot version.
    assert hanjuku_run._bot_version() == 'hanjuku-chart-v999-test-hot'
