from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from docich import nethack_corner_operator as operator
from docich.nethack_corner import NethackCornerError


def test_recover_rejects_active_manual_corner_without_switching() -> None:
    fake_g = SimpleNamespace(config_path=Path("/tmp/docich.toml"))
    manager = mock.Mock()
    manager.status.return_value = {"status": "active", "previous_game": "sorengame"}
    manager._active_game_reader.return_value = "nethack"

    with mock.patch.object(operator, "load_global", return_value=fake_g), mock.patch.object(
        operator, "ManualNethackCornerManager", return_value=manager
    ):
        with pytest.raises(NethackCornerError, match="failed manual corner"):
            operator.recover(fake_g.config_path)

    manager._transition_to.assert_not_called()
