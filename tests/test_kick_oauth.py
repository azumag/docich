"""Offline tests for the Kick broadcaster token refresher (no network)."""
from __future__ import annotations

import base64
import hashlib
import json
import stat
import sys
import urllib.parse
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich import kick_oauth as oauth  # noqa: E402

BROADCASTER = "126249739"
NOW = 1_800_000_000.0
EXP = int(NOW) + 14_000


class FakeKick:
    """Records calls; ``tokens`` maps access token -> introspect data body."""

    def __init__(self, *, token_status=200, token_body=None, tokens=None,
                 channels=None, validate_down=False):
        self.token_status = token_status
        self.token_body = token_body if token_body is not None else {
            "access_token": "newaccess", "refresh_token": "newrefresh",
            "expires_in": "3600", "scope": "channel:read channel:write"}
        self.tokens = tokens if tokens is not None else {}
        self.channels = channels if channels is not None else {
            "data": [{"broadcaster_user_id": int(BROADCASTER)}]}
        self.validate_down = validate_down
        self.calls = []

    def __call__(self, method, url, *, headers=None, form=None, timeout=15):
        self.calls.append((method, url, dict(form or {})))
        if url == oauth.TOKEN_URL:
            return self.token_status, self.token_body
        token = (headers or {})["Authorization"].removeprefix("Bearer ")
        if self.validate_down:
            return 503, {}
        if url == oauth.INTROSPECT_URL:
            body = self.tokens.get(token)
            return (200, {"data": body}) if body else (401, {})
        if url == oauth.CHANNELS_URL:
            if token not in self.tokens:
                return 401, {}
            return 200, self.channels
        raise AssertionError(f"unexpected url {url}")

    @property
    def token_forms(self):
        return [form for _, url, form in self.calls if url == oauth.TOKEN_URL]


def _facts(expires_in=14_000, scopes=oauth.REQUIRED_SCOPES, user_id=BROADCASTER,
           token_type="user"):
    return {"active": True, "token_type": token_type,
            "scope": " ".join(scopes), "exp": int(NOW) + expires_in}


def _env(tmp_path, text):
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)
    return path


CONFIGURED = (
    "OTHER=keep me\n"
    "KICK_CLIENT_ID=cid\n"
    "KICK_CLIENT_SECRET=csecret\n"
    f"KICK_BROADCASTER_USER_ID={BROADCASTER}\n"
    "KICK_OAUTH_REFRESH_TOKEN=oldrefresh\n"
    "export KICK_ACCESS_TOKEN=oldaccess\n"
)


def test_write_env_replaces_keeps_and_appends(tmp_path):
    path = _env(tmp_path, "A=1\nexport KICK_ACCESS_TOKEN='x'\n# KICK_ACCESS_TOKEN=comment\nB=2")
    oauth.write_env(path, {"KICK_ACCESS_TOKEN": "abc", "NEW_KEY": "def"})
    assert path.read_text() == (
        "A=1\nexport KICK_ACCESS_TOKEN=abc\n# KICK_ACCESS_TOKEN=comment\nB=2\nNEW_KEY=def\n")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(tmp_path.glob(".env.kick-oauth.*.tmp"))


def test_write_env_rejects_values_that_could_break_the_shell_file(tmp_path):
    path = _env(tmp_path, "A=1\n")
    with pytest.raises(oauth.KickOAuthError):
        oauth.write_env(path, {"KICK_ACCESS_TOKEN": "a b"})
    assert path.read_text() == "A=1\n"


def test_not_configured_without_secret_or_refresh_token(tmp_path):
    path = _env(tmp_path, "KICK_CLIENT_ID=cid\nKICK_ACCESS_TOKEN=static\n")
    fake = FakeKick()
    assert oauth.refresh(path, http=fake, now=NOW)["status"] == "not_configured"
    assert fake.calls == []


def test_valid_far_from_expiry_is_left_alone(tmp_path):
    path = _env(tmp_path, CONFIGURED)
    before = path.read_text()
    fake = FakeKick(tokens={"oldaccess": _facts(expires_in=10_000)})
    result = oauth.refresh(path, http=fake, now=NOW)
    assert (result["status"], result["action"]) == ("ok", "no_change")
    assert fake.token_forms == [] and path.read_text() == before


@pytest.mark.parametrize("current", [
    {"oldaccess": _facts(expires_in=600)},  # close to expiry
    {"oldaccess": {**_facts(), "active": False}},  # inactive
    {},  # introspect rejects it outright
])
def test_refresh_rotates_the_access_token(tmp_path, current):
    path = _env(tmp_path, CONFIGURED)
    fake = FakeKick(tokens={**current, "newaccess": _facts()})
    result = oauth.refresh(path, http=fake, now=NOW)
    assert (result["status"], result["action"]) == ("ok", "refreshed")
    assert fake.token_forms == [{"grant_type": "refresh_token", "refresh_token": "oldrefresh",
                                 "client_id": "cid", "client_secret": "csecret"}]
    env = oauth.read_env(path)
    assert env["KICK_ACCESS_TOKEN"] == "newaccess"
    assert env["KICK_OAUTH_REFRESH_TOKEN"] == "newrefresh"
    assert env["OTHER"] == "keep me"
    assert "export KICK_ACCESS_TOKEN=newaccess\n" in path.read_text()


def test_validation_outage_still_attempts_refresh(tmp_path):
    path = _env(tmp_path, CONFIGURED)
    fake = FakeKick(validate_down=True)
    with pytest.raises(oauth.KickOAuthError) as caught:
        oauth.refresh(path, http=fake, now=NOW)
    assert len(fake.token_forms) == 1
    assert caught.value.code == "validate_unavailable"
    assert oauth.read_env(path)["KICK_ACCESS_TOKEN"] == "oldaccess"


def test_rejected_refresh_token_requires_reauth_and_keeps_env(tmp_path):
    path = _env(tmp_path, CONFIGURED)
    before = path.read_text()
    fake = FakeKick(token_status=400, token_body={"error": "Invalid request"})
    with pytest.raises(oauth.KickOAuthError) as caught:
        oauth.refresh(path, http=fake, now=NOW)
    assert (caught.value.code, caught.value.exit_code) == ("reauth_required", oauth.EXIT_REAUTH)
    assert path.read_text() == before


@pytest.mark.parametrize("facts,channels,code", [
    (_facts(scopes=["channel:read"]), None, "new_token_missing_scopes"),
    (_facts(), {"data": [{"broadcaster_user_id": 999}]}, "new_token_not_broadcaster"),
    (_facts(), {"data": []}, "new_token_principal_unreadable"),
    ({**_facts(), "token_type": "app"}, None, "new_token_not_user_token"),
    ({**_facts(), "active": False}, None, "new_token_token_inactive"),
])
def test_unusable_new_token_is_never_stored(tmp_path, facts, channels, code):
    path = _env(tmp_path, CONFIGURED)
    before = path.read_text()
    fake = FakeKick(tokens={"newaccess": facts},
                    channels=channels if channels is not None else FakeKick().channels)
    with pytest.raises(oauth.KickOAuthError) as caught:
        oauth.refresh(path, http=fake, now=NOW)
    assert caught.value.code == code
    assert path.read_text() == before


def test_missing_broadcaster_id_is_learned_from_the_principal_channel(tmp_path):
    path = _env(tmp_path, CONFIGURED.replace(f"KICK_BROADCASTER_USER_ID={BROADCASTER}\n", ""))
    fake = FakeKick(tokens={"newaccess": _facts()})
    result = oauth.refresh(path, http=fake, now=NOW)
    assert (result["status"], result["action"]) == ("ok", "refreshed")
    assert result["broadcaster_user_id"] == BROADCASTER
    assert oauth.read_env(path)["KICK_BROADCASTER_USER_ID"] == BROADCASTER


def _authorize(tmp_path, env_path):
    state_path = tmp_path / "state" / "authorize_state.json"
    url = oauth.authorize_url(env_path, state_path, now=NOW)
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert query["response_type"] == ["code"]
    assert query["redirect_uri"] == ["http://localhost"]
    assert set(query["scope"][0].split()) == set(oauth.REQUIRED_SCOPES)
    assert query["code_challenge_method"] == ["S256"]
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600
    return state_path, query


def test_authorize_url_pkce_challenge_matches_stored_verifier(tmp_path):
    path = _env(tmp_path, CONFIGURED)
    state_path, query = _authorize(tmp_path, path)
    saved = json.loads(state_path.read_text())
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(saved["code_verifier"].encode()).digest()).rstrip(b"=").decode()
    assert query["code_challenge"] == [challenge]


def test_exchange_stores_tokens_from_the_redirected_url(tmp_path):
    path = _env(tmp_path, CONFIGURED.replace("KICK_OAUTH_REFRESH_TOKEN=oldrefresh\n", ""))
    state_path, query = _authorize(tmp_path, path)
    state = query["state"][0]
    saved = json.loads(state_path.read_text())
    fake = FakeKick(tokens={"newaccess": _facts()})
    redirected = f"http://localhost/?code=authcode&state={state}\n"
    result = oauth.exchange(path, state_path, redirected, http=fake, now=NOW + 60)
    assert (result["status"], result["action"]) == ("ok", "authorized")
    form = fake.token_forms[0]
    assert form["grant_type"] == "authorization_code"
    assert form["code"] == "authcode"
    assert form["redirect_uri"] == "http://localhost"
    assert form["code_verifier"] == saved["code_verifier"]
    env = oauth.read_env(path)
    assert env["KICK_OAUTH_REFRESH_TOKEN"] == "newrefresh"
    assert env["KICK_ACCESS_TOKEN"] == "newaccess"
    assert not state_path.exists()


@pytest.mark.parametrize("make_url,delay,code", [
    (lambda s: f"http://localhost/?code=c&state={s}x", 0, "state_mismatch_or_expired"),
    (lambda s: f"http://localhost/?code=c&state={s}", oauth.STATE_TTL_SEC + 1, "state_mismatch_or_expired"),
    (lambda s: "http://localhost/?error=access_denied", 0, "consent_denied"),
])
def test_exchange_rejects_foreign_stale_or_denied_redirects(tmp_path, make_url, delay, code):
    path = _env(tmp_path, CONFIGURED)
    before = path.read_text()
    state_path, query = _authorize(tmp_path, path)
    fake = FakeKick(tokens={"newaccess": _facts()})
    with pytest.raises(oauth.KickOAuthError) as caught:
        oauth.exchange(path, state_path, make_url(query["state"][0]), http=fake, now=NOW + delay)
    assert caught.value.code == code
    assert fake.token_forms == [] and path.read_text() == before


def test_main_records_secret_free_status(tmp_path, monkeypatch, capsys):
    path = _env(tmp_path, CONFIGURED)
    status_path = tmp_path / "run" / "kick_oauth" / "status.json"
    monkeypatch.setattr(oauth, "_paths", lambda config: (path, status_path, tmp_path / "s.json"))
    monkeypatch.setattr(oauth, "_http", FakeKick(tokens={"newaccess": _facts()}))
    assert oauth.main(["--config", "x.toml", "refresh"]) == oauth.EXIT_OK
    printed = capsys.readouterr().out
    recorded = status_path.read_text()
    for secret in ("newaccess", "newrefresh", "oldrefresh", "csecret", "oldaccess"):
        assert secret not in printed and secret not in recorded
    assert json.loads(recorded)["action"] == "refreshed"


def test_main_exit_code_marks_reauth_for_systemd(tmp_path, monkeypatch, capsys):
    path = _env(tmp_path, CONFIGURED)
    status_path = tmp_path / "status.json"
    monkeypatch.setattr(oauth, "_paths", lambda config: (path, status_path, tmp_path / "s.json"))
    monkeypatch.setattr(oauth, "_http", FakeKick(token_status=400))
    assert oauth.main(["--config", "x.toml", "refresh"]) == oauth.EXIT_REAUTH
    assert json.loads(status_path.read_text())["status"] == "reauth_required"
    capsys.readouterr()


def test_units_and_deploy_wiring():
    service = (ROOT / "scripts/systemd/docich-kick-oauth-refresh.service").read_text()
    timer = (ROOT / "scripts/systemd/docich-kick-oauth-refresh.timer").read_text()
    ensure = (ROOT / "ops/vm_actions/ensure_kick_oauth_refresh_timer.sh").read_text()
    workflow = (ROOT / ".github/workflows/vm-operations.yml").read_text()
    assert "__DOCICH_ROOT__/bin/docich-kick-oauth --config" in service
    assert service.count(" refresh\n") == 1
    assert "UMask=0077" in service
    assert "OnUnitInactiveSec=15min" in timer
    assert "docich-kick-oauth-refresh.service docich-kick-oauth-refresh.timer" in ensure
    assert "enable --now docich-kick-oauth-refresh.timer" in ensure
    assert "ensure_kick_oauth_refresh_timer.sh" in workflow
    wrapper = ROOT / "bin/docich-kick-oauth"
    assert wrapper.stat().st_mode & 0o111
    # Refresh well inside the access-token lifetime and the timer period.
    assert oauth.REFRESH_MARGIN_SEC >= 2 * 15 * 60
