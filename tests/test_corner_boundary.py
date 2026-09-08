import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich.corner_boundary import boundary_ready


def test_boundary_requires_new_confirmed_completion(tmp_path):
    assert not boundary_ready(tmp_path, 100)
    (tmp_path / 'corner_boundary_prediction.json').write_text(json.dumps({'completed_at': 99}))
    assert not boundary_ready(tmp_path, 100)
    (tmp_path / 'corner_boundary_improvement.json').write_text(json.dumps({'completed_at': 101}))
    assert boundary_ready(tmp_path, 100)


def test_corrupt_future_or_nonfinite_boundary_does_not_unlock(tmp_path):
    for value in ['{', '{"completed_at": NaN}', '{"completed_at": true}', '{"completed_at": "101"}']:
        (tmp_path / 'corner_boundary_prediction.json').write_text(value)
        assert not boundary_ready(tmp_path, 100)
