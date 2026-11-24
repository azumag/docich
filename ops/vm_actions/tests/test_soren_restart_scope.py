import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('restart_scope', ROOT / 'ops/vm_actions/soren_restart_scope.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
OLD, NEW = 'a' * 40, 'b' * 40


def comparison(paths):
    return dict(base_commit={'sha': OLD}, merge_base_commit={'sha': OLD},
                commits=[{'sha': NEW}], status='ahead', behind_by=0,
                files=[{'filename': p, 'status': 'modified'} for p in paths])


def test_only_exact_bgm_files_skip_shared_radio_restart():
    assert module.classify(comparison(['bgm_worker.sh', 'tests/test_bgm_worker.py']), OLD, NEW) == 'bgm-only'
    assert module.classify(comparison(['bgm_worker.sh', 'core/config.sh']), OLD, NEW) == 'other'
    assert module.classify(comparison(['workers/radio_worker.sh']), OLD, NEW) == 'other'


def test_truncated_or_renamed_file_lists_cannot_skip():
    assert module.classify(comparison(['bgm_worker.sh'] * 300), OLD, NEW) == 'other'
    data = comparison(['bgm_worker.sh'])
    data['files'][0]['previous_filename'] = 'core/config.sh'
    assert module.classify(data, OLD, NEW) == 'other'


@pytest.mark.parametrize('field,value', [
    ('status', 'diverged'), ('behind_by', 1), ('files', []),
    ('base_commit', {'sha': NEW}), ('merge_base_commit', {'sha': NEW}),
    ('commits', [{'sha': OLD}]),
])
def test_unverified_comparisons_fail_before_any_restart(field, value):
    data = comparison(['bgm_worker.sh'])
    data[field] = value
    with pytest.raises(ValueError):
        module.classify(data, OLD, NEW)
