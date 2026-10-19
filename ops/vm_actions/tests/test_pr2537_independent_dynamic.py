"""Independent behavioral checks for PR 2537; no VM/network/credentials."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
SHA = '1328c9cfb180a5ca4bf9cb9eb53bc6f3289b7aa8'
SENTINEL = 'SYNTHETIC_PAYLOAD_SENTINEL'
REAL_RUN = subprocess.run


def encode(value):
    return json.dumps(value, separators=(',', ':')).encode()


def with_metadata(data):
    return {**data, 'bundle_storage': {'status': 'ok'},
            'ops_brief_projection': {'status': 'ok'}}


def payload_of_final_size(wanted):
    data = {'status': 'ok', 'marker': SENTINEL, 'measurements': {}}
    rows = data['measurements']
    while len(encode(with_metadata(data))) < wanted - 520:
        rows[f'k{len(rows):03}'] = 'x' * 500
    rows['tail'] = ''
    remaining = wanted - len(encode(with_metadata(data)))
    assert 0 <= remaining <= 500
    rows['tail'] = 'x' * remaining
    assert len(encode(with_metadata(data))) == wanted
    assert len(encode(data)) < 65536  # raw collector input is never truncated
    return data


@pytest.fixture
def harness(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('independent_gateway', ROOT / 'ops/vm_actions/gateway.py')
    gw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gw)
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    cfg = {'state': str(tmp_path / 'state'), 'repos': {'docich': {
        'production': str(ROOT), 'mode': 'git',
        'projections': {'games/soviet_now': str(runtime)}}}}
    config = tmp_path / 'config.json'
    config.write_text(json.dumps(cfg))
    output = {'stdout': encode(payload_of_final_size(50139)), 'returncode': 0}
    calls = []

    def collector_only(argv, *args, **kwargs):
        if len(argv) > 1 and argv[1] == str(ROOT / 'ops/vm_actions/collect_diagnostics.py'):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, output['returncode'], output['stdout'], b'')
        return REAL_RUN(argv, *args, **kwargs)

    monkeypatch.setattr(gw.subprocess, 'run', collector_only)
    monkeypatch.setattr(gw, '_projection_review', lambda *args: {})
    monkeypatch.setattr(gw, '_bundle_storage_review', lambda *args: {'status': 'ok'})
    monkeypatch.setattr(gw, '_ops_brief_health', lambda *args: 'ok')
    monkeypatch.setenv('VMOPS_TESTING', '1')
    monkeypatch.setenv('SSH_ORIGINAL_COMMAND', f'diagnostics docich production {SHA}')
    monkeypatch.setattr(sys, 'argv', [str(ROOT / 'ops/vm_actions/gateway.py'), str(config)])
    return gw, cfg, output, calls


@pytest.mark.parametrize('size', [49153, 50139, 65535, 65536])
def test_actual_diagnostics_result_accepts_old_cap_exceeded_and_new_boundary(harness, size):
    gw, cfg, output, calls = harness
    data = payload_of_final_size(size)
    output['stdout'] = encode(data)
    result = gw.diagnostics_result(cfg, 'docich', 'production', SHA)
    assert result == {'status': 'diagnosed', 'sha': SHA, 'diagnostics': with_metadata(data)}
    assert len(encode(result['diagnostics'])) == size
    assert len(calls) == 1


def test_actual_diagnostics_result_over_boundary_rejects_fixed_reason(harness):
    gw, cfg, output, calls = harness
    output['stdout'] = encode(payload_of_final_size(65537))
    with pytest.raises(ValueError, match='^diagnostics output too large$') as error:
        gw.diagnostics_result(cfg, 'docich', 'production', SHA)
    assert gw.reason_code(error.value) == 'diagnostics_output_too_large'
    assert len(calls) == 1


@pytest.mark.parametrize('size', [49153, 65536])
def test_main_success_uses_real_diagnostics_result(harness, capsys, size):
    gw, cfg, output, calls = harness
    output['stdout'] = encode(payload_of_final_size(size))
    gw.main()
    capture = capsys.readouterr()
    result = json.loads(capture.out)
    assert result['status'] == 'diagnosed'
    assert len(encode(result['diagnostics'])) == size
    assert capture.err == ''
    assert len(calls) == 1


@pytest.mark.parametrize('failure', ['size', 'collector', 'invalid_json', 'preview'])
def test_main_failures_exit_one_and_never_print_payload(harness, monkeypatch, capsys, failure):
    gw, cfg, output, calls = harness
    expected = 'operation_rejected'
    if failure == 'size':
        output['stdout'] = encode(payload_of_final_size(65537))
        expected = 'diagnostics_output_too_large'
    elif failure == 'collector':
        output['returncode'] = 17
    elif failure == 'invalid_json':
        output['stdout'] = SENTINEL.encode()
    elif failure == 'preview':
        monkeypatch.setenv('SSH_ORIGINAL_COMMAND', f'diagnostics docich preview {SHA}')
    with pytest.raises(SystemExit) as exit_info:
        gw.main()
    capture = capsys.readouterr()
    assert exit_info.value.code == 1
    assert capture.out == ''
    assert capture.err == f'VM operation rejected: {expected}\n'
    assert SENTINEL not in capture.err
    assert len(calls) == (0 if failure == 'preview' else 1)


def workflow_run_script():
    source = (ROOT / '.github/workflows/vm-operations.yml').read_text()
    for block in source.split('      - name: '):
        if 'diagnostics_output_too_large' in block:
            run = block.split('        run: |\n', 1)[1]
            return '\n'.join(line[10:] if line.startswith('          ') else line
                             for line in run.splitlines()) + '\n'
    raise AssertionError('diagnostics run step missing')


@pytest.mark.parametrize('reason', ['diagnostics_output_too_large', 'operation_rejected', 'future_unknown_reason'])
def test_real_workflow_script_ssh_failure_stays_failed_and_fixed(tmp_path, reason):
    script = workflow_run_script()
    script_path = tmp_path / 'diagnostics-step.bash'
    script_path.write_text(script)
    # A Bash function shadows only ssh. The extracted workflow body is unchanged.
    prelude = ('ssh() { printf "%s\\n" "VM operation rejected: $STUB_REASON ' + SENTINEL + '" >&2; '
               'printf "%s" "' + SENTINEL + '"; return 23; }; export -f ssh; source "$1"')
    env = {'PATH': '/usr/bin:/bin', 'RUNNER_TEMP': str(tmp_path), 'port': '22',
           'VM_SSH_USER': 'synthetic', 'VM_SSH_HOST': 'invalid.invalid', 'SHA': SHA,
           'STUB_REASON': reason}
    proc = REAL_RUN(['/bin/bash', '-c', prelude, 'test', str(script_path)], env=env, capture_output=True, text=True)
    assert proc.returncode == 1
    assert proc.stdout == ''
    assert SENTINEL not in proc.stderr
    if reason == 'diagnostics_output_too_large':
        assert proc.stderr == ('::error::Diagnostics collector output exceeds the reviewed gateway JSON cap '
            '(DIAGNOSTICS_JSON_MAX). Raise the cap or trim the collector per the reviewed change; '
            'this is not a stale-gateway condition.\n')
    else:
        assert proc.stderr == ('::error::Installed VM gateway does not accept diagnostics. Activate the reviewed '
            'root-owned gateway through the trusted owner-only privileged install path, then rerun diagnostics.\n')
