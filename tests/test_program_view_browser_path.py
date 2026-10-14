from pathlib import Path
from unittest import mock
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.adapters import program  # noqa: E402


def test_browser_bin_falls_back_to_fixed_snap_path():
    def fake_which(name):
        return "/snap/bin/chromium" if name == "/snap/bin/chromium" else None

    with mock.patch.object(program.shutil, "which", side_effect=fake_which) as which:
        assert program._browser_bin() == "/snap/bin/chromium"

    assert [call.args[0] for call in which.call_args_list][-1] == "/snap/bin/chromium"


def test_browser_bin_does_not_accept_configurable_arbitrary_path():
    with mock.patch.object(program.shutil, "which", return_value=None):
        assert program._browser_bin() is None
