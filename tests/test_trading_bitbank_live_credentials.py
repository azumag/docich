"""Read-only key loading: fail closed, reveal nothing (Issue #3623, L3)."""
import json
import os
import pickle
import tempfile
from pathlib import Path

import pytest

from bitbank_live_support import SENTINEL_KEY, SENTINEL_SECRET
from docich.trading.exchanges.bitbank_live import credentials as cr
from docich.trading.live.paths import live_layout


@pytest.fixture
def state():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


def _setup(state, *, body=None, mode=0o600, root_mode=0o700, owner_mode=0o700,
           filename=cr.KEY_FILENAME):
    layout = live_layout(state)
    layout.root.mkdir(mode=0o700)
    layout.owner_dir.mkdir(mode=0o700)
    os.chmod(layout.root, root_mode)
    os.chmod(layout.owner_dir, owner_mode)
    path = layout.owner_dir / filename
    if body is not None:
        path.write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")
        os.chmod(path, mode)
    return path


def _good(**kw):
    data = {"schema_version": 1, "kind": cr.READONLY_KEY_KIND, "api_key": SENTINEL_KEY,
            "api_secret": SENTINEL_SECRET}
    data.update(kw)
    return data


def _reason(state, **kw):
    with pytest.raises(cr.CredentialsUnavailable) as err:
        cr.load_credentials(state, **kw)
    text = str(err.value) + repr(err.value) + repr(err.value.__cause__) + repr(err.value.__context__)
    assert SENTINEL_KEY not in text and SENTINEL_SECRET not in text and str(state) not in text
    return err.value.reason


def test_a_valid_file_loads_and_reveals_nothing_by_accident(state):
    _setup(state, body=_good(note="read-only key"))
    creds = cr.load_credentials(state)
    assert creds.reveal() == (SENTINEL_KEY, SENTINEL_SECRET)
    for text in (repr(creds), str(creds), f"{creds}", f"{creds!r}"):
        assert SENTINEL_KEY not in text and SENTINEL_SECRET not in text
    with pytest.raises(TypeError):
        pickle.dumps(creds)
    with pytest.raises(AttributeError):
        creds.extra = 1
    assert creds.__eq__(creds) is NotImplemented     # no value comparison is offered


def test_missing_everything_is_file_missing_and_nothing_is_created(state):
    assert _reason(state) is cr.CredentialReason.FILE_MISSING
    assert list(state.iterdir()) == []
    _setup(state)                                # directories but no file
    assert _reason(state) is cr.CredentialReason.FILE_MISSING


@pytest.mark.parametrize("kwargs,reason", [
    (dict(body=_good(), mode=0o644), cr.CredentialReason.MODE_NOT_0600),
    (dict(body=_good(), mode=0o640), cr.CredentialReason.MODE_NOT_0600),
    (dict(body=_good(), mode=0o400), cr.CredentialReason.MODE_NOT_0600),
    (dict(body=_good(), owner_mode=0o755), cr.CredentialReason.OWNER_DIR_INVALID),
    (dict(body=_good(), root_mode=0o755), cr.CredentialReason.ROOT_INVALID),
    (dict(body="{not json"), cr.CredentialReason.CORRUPT_JSON),
    (dict(body="[]"), cr.CredentialReason.SCHEMA_INVALID),
    (dict(body=_good(schema_version=2)), cr.CredentialReason.SCHEMA_INVALID),
    (dict(body=_good(kind="other")), cr.CredentialReason.SCHEMA_INVALID),
    (dict(body={k: v for k, v in _good().items() if k != "api_secret"}), cr.CredentialReason.SCHEMA_INVALID),
    (dict(body=_good(extra="x")), cr.CredentialReason.UNKNOWN_KEYS),
    (dict(body=_good(api_key="short")), cr.CredentialReason.VALUE_INVALID),
    (dict(body=_good(api_secret="has space in it 123")), cr.CredentialReason.VALUE_INVALID),
    # A trailing LF is not part of the secret (it used to pass re.match with "$").
    (dict(body=_good(api_secret=SENTINEL_SECRET + "\n")), cr.CredentialReason.VALUE_INVALID),
    (dict(body=_good(api_key=12345678)), cr.CredentialReason.VALUE_INVALID),
    (dict(body=" " * 3000), cr.CredentialReason.TOO_LARGE),
])
def test_bad_files_fail_closed_with_a_reason_that_reveals_nothing(state, kwargs, reason):
    _setup(state, **kwargs)
    assert _reason(state) is reason


def test_a_symlinked_key_file_is_refused(state):
    path = _setup(state)
    target = state / "elsewhere.json"
    target.write_text(json.dumps(_good()))
    os.chmod(target, 0o600)
    path.symlink_to(target)
    assert _reason(state) is cr.CredentialReason.NOT_REGULAR


def test_env_source_reads_then_removes_the_variables():
    env = {cr.ENV_KEY: SENTINEL_KEY, cr.ENV_SECRET: SENTINEL_SECRET, "OTHER": "x"}
    creds = cr.load_credentials("/nonexistent", source="env", environ=env)
    assert creds.reveal() == (SENTINEL_KEY, SENTINEL_SECRET) and env == {"OTHER": "x"}


def test_env_source_failures_also_consume_the_variables_and_leak_nothing():
    env = {cr.ENV_KEY: SENTINEL_KEY}
    assert _reason("/x", source="env", environ=env) is cr.CredentialReason.ENV_MISSING and env == {}
    env = {cr.ENV_KEY: "short", cr.ENV_SECRET: SENTINEL_SECRET}
    assert _reason("/x", source="env", environ=env) is cr.CredentialReason.VALUE_INVALID and env == {}


def test_the_file_source_never_looks_at_the_environment(state, monkeypatch):
    monkeypatch.setenv(cr.ENV_KEY, SENTINEL_KEY)
    monkeypatch.setenv(cr.ENV_SECRET, SENTINEL_SECRET)
    assert _reason(state) is cr.CredentialReason.FILE_MISSING
    assert os.environ[cr.ENV_KEY] == SENTINEL_KEY          # untouched
    assert _reason(state, source="nonsense") is cr.CredentialReason.CHECK_ERROR


# ---- the explicit order-capable process key (file only, same strict checks) ----------------
def _spot_good(**kw):
    data = {"schema_version": 1, "kind": cr.SPOT_ORDER_KEY_KIND, "api_key": SENTINEL_KEY,
            "api_secret": SENTINEL_SECRET}
    data.update(kw)
    return data


def _spot_reason(state):
    with pytest.raises(cr.CredentialsUnavailable) as err:
        cr.load_spot_order_credentials(state)
    text = str(err.value) + repr(err.value) + repr(err.value.__cause__) + repr(err.value.__context__)
    assert SENTINEL_KEY not in text and SENTINEL_SECRET not in text and str(state) not in text
    return err.value.reason


def test_the_spot_order_key_loads_from_its_own_fixed_file_and_kind(state):
    _setup(state, body=_spot_good(note="order-capable process"), filename=cr.SPOT_ORDER_KEY_FILENAME)
    creds = cr.load_spot_order_credentials(state)
    assert creds.reveal() == (SENTINEL_KEY, SENTINEL_SECRET)
    assert SENTINEL_KEY not in repr(creds) and SENTINEL_SECRET not in str(creds)


def test_the_spot_order_key_is_file_only_and_never_uses_the_environment(state, monkeypatch):
    monkeypatch.setenv(cr.ENV_KEY, SENTINEL_KEY)
    monkeypatch.setenv(cr.ENV_SECRET, SENTINEL_SECRET)
    assert _spot_reason(state) is cr.CredentialReason.FILE_MISSING
    assert os.environ[cr.ENV_KEY] == SENTINEL_KEY          # untouched
    assert os.environ[cr.ENV_SECRET] == SENTINEL_SECRET


def test_the_readonly_and_spot_order_files_do_not_stand_in_for_each_other(state):
    path = _setup(state, body=_good())
    assert _spot_reason(state) is cr.CredentialReason.FILE_MISSING
    path.unlink()
    _setup(state, body=_spot_good(), filename=cr.SPOT_ORDER_KEY_FILENAME)
    with pytest.raises(cr.CredentialsUnavailable) as err:
        cr.load_credentials(state)
    assert err.value.reason is cr.CredentialReason.FILE_MISSING


@pytest.mark.parametrize("body,reason", [
    (_spot_good(kind="docich-bitbank-readonly-key"), cr.CredentialReason.SCHEMA_INVALID),
    (_spot_good(schema_version=2), cr.CredentialReason.SCHEMA_INVALID),
    (_spot_good(extra="x"), cr.CredentialReason.UNKNOWN_KEYS),
    ({k: v for k, v in _spot_good().items() if k != "api_secret"}, cr.CredentialReason.SCHEMA_INVALID),
    (_spot_good(api_key="short"), cr.CredentialReason.VALUE_INVALID),
    ("{not json", cr.CredentialReason.CORRUPT_JSON),
])
def test_a_bad_spot_order_key_fails_closed_with_a_safe_reason(state, body, reason):
    _setup(state, body=body, filename=cr.SPOT_ORDER_KEY_FILENAME)
    assert _spot_reason(state) is reason


def test_a_symlinked_spot_order_key_is_refused(state):
    path = _setup(state, filename=cr.SPOT_ORDER_KEY_FILENAME)
    target = state / "elsewhere.json"
    target.write_text(json.dumps(_spot_good()), encoding="utf-8")
    os.chmod(target, 0o600)
    path.symlink_to(target)
    assert _spot_reason(state) is cr.CredentialReason.NOT_REGULAR


def test_the_spot_order_key_is_checked_after_the_read_as_well(state, monkeypatch):
    _setup(state, body=_spot_good(), filename=cr.SPOT_ORDER_KEY_FILENAME)
    monkeypatch.setattr(cr.os, "read", lambda _fd, _size: b" " * (cr.MAX_BYTES + 1))
    assert _spot_reason(state) is cr.CredentialReason.TOO_LARGE
