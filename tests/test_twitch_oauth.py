"""Offline tests for the Twitch broadcaster token refresher (no network)."""
from __future__ import annotations

import json
import stat
import sys
import urllib.parse
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich import twitch_oauth as oauth  # noqa: E402

BROADCASTER = "424242"
NOW = 1_800_000_000.0


class FakeTwitch:
    """Records calls; ``tokens`` maps access token -> validate body."""

    def __init__(self, *, token_status=200, token_body=None, tokens=None, validate_down=False):
        self.token_status = token_status
        self.token_body = token_body if token_body is not None else {
            "access_token": "newaccess", "refresh_token": "newrefresh"}
        self.tokens = tokens if tokens is not None else {}
        self.validate_down = validate_down
        self.calls = []

    def __call__(self, method, url, *, headers=None, form=None, timeout=15):
        self.calls.append((method, url, dict(form or {})))
        if url == oauth.TOKEN_URL:
            return self.token_status, self.token_body
        if self.validate_down:
            return 503, {}
        token = (headers or {})["Authorization"].removeprefix("OAuth ")
        body = self.tokens.get(token)
        return (200, body) if body else (401, {})

    @property
    def token_forms(self):
        return [form for _, url, form in self.calls if url == oauth.TOKEN_URL]


def _facts(expires_in=14_000, scopes=oauth.REQUIRED_SCOPES, user_id=BROADCASTER):
    return {"login": "dociai", "user_id": user_id, "scopes": list(scopes), "expires_in": expires_in}


def _env(tmp_path, text):
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)
    return path


CONFIGURED = (
    "OTHER=keep me\n"
    "TWITCH_CLIENT_ID=cid\n"
    "TWITCH_CLIENT_SECRET=csecret\n"
    f"TWITCH_BROADCASTER_ID={BROADCASTER}\n"
    "TWITCH_OAUTH_REFRESH_TOKEN=oldrefresh\n"
    "export TWITCH_BOT_TOKEN=oldaccess\n"
    'TWITCH_PREDICTIONS_TOKEN="oldaccess"\n'
    "TWITCH_POLLS_TOKEN=oldaccess\n"
    "TWITCH_CLIP_TOKEN=oldaccess\n"
)


def test_write_env_replaces_keeps_and_appends(tmp_path):
    path = _env(tmp_path, "A=1\nexport TWITCH_BOT_TOKEN='x'\n# TWITCH_BOT_TOKEN=comment\nB=2")
    oauth.write_env(path, {"TWITCH_BOT_TOKEN": "abc", "NEW_KEY": "def"})
    assert path.read_text() == (
        "A=1\nexport TWITCH_BOT_TOKEN=abc\n# TWITCH_BOT_TOKEN=comment\nB=2\nNEW_KEY=def\n")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(tmp_path.glob(".env.twitch-oauth.*.tmp"))


def test_write_env_rejects_values_that_could_break_the_shell_file(tmp_path):
    path = _env(tmp_path, "A=1\n")
    with pytest.raises(oauth.TwitchOAuthError):
        oauth.write_env(path, {"TWITCH_BOT_TOKEN": "a b"})
    assert path.read_text() == "A=1\n"


def test_not_configured_without_secret_or_refresh_token(tmp_path):
    path = _env(tmp_path, "TWITCH_CLIENT_ID=cid\nTWITCH_BOT_TOKEN=implicit\n")
    fake = FakeTwitch()
    assert oauth.refresh(path, http=fake, now=NOW)["status"] == "not_configured"
    assert fake.calls == []


def test_valid_far_from_expiry_is_left_alone(tmp_path):
    path = _env(tmp_path, CONFIGURED)
    before = path.read_text()
    fake = FakeTwitch(tokens={"oldaccess": _facts(expires_in=10_000)})
    result = oauth.refresh(path, http=fake, now=NOW)
    assert (result["status"], result["action"]) == ("ok", "no_change")
    assert fake.token_forms == [] and path.read_text() == before


@pytest.mark.parametrize("current", [
    {"oldaccess": _facts(expires_in=600)},  # close to expiry
    {},  # already invalid
])
def test_refresh_rotates_every_managed_key(tmp_path, current):
    path = _env(tmp_path, CONFIGURED)
    fake = FakeTwitch(tokens={**current, "newaccess": _facts()})
    result = oauth.refresh(path, http=fake, now=NOW)
    assert (result["status"], result["action"]) == ("ok", "refreshed")
    assert fake.token_forms == [{"grant_type": "refresh_token", "refresh_token": "oldrefresh",
                                 "client_id": "cid", "client_secret": "csecret"}]
    env = oauth.read_env(path)
    assert all(env[key] == "newaccess" for key in oauth.MANAGED_KEYS)
    assert env["TWITCH_OAUTH_REFRESH_TOKEN"] == "newrefresh"
    assert env["OTHER"] == "keep me"
    assert "export TWITCH_BOT_TOKEN=newaccess\n" in path.read_text()


def test_inconsistent_keys_are_unified_even_when_bot_token_is_fresh(tmp_path):
    path = _env(tmp_path, CONFIGURED.replace("TWITCH_CLIP_TOKEN=oldaccess", "TWITCH_CLIP_TOKEN=other"))
    fake = FakeTwitch(tokens={"oldaccess": _facts(), "newaccess": _facts()})
    assert oauth.refresh(path, http=fake, now=NOW)["action"] == "refreshed"
    assert oauth.read_env(path)["TWITCH_CLIP_TOKEN"] == "newaccess"


def test_validation_outage_still_attempts_refresh(tmp_path):
    path = _env(tmp_path, CONFIGURED)
    fake = FakeTwitch(validate_down=True)
    with pytest.raises(oauth.TwitchOAuthError) as caught:
        oauth.refresh(path, http=fake, now=NOW)
    assert len(fake.token_forms) == 1
    assert caught.value.code == "validate_unavailable"
    assert oauth.read_env(path)["TWITCH_BOT_TOKEN"] == "oldaccess"


def test_rejected_refresh_token_requires_reauth_and_keeps_env(tmp_path):
    path = _env(tmp_path, CONFIGURED)
    before = path.read_text()
    fake = FakeTwitch(token_status=400, token_body={"message": "Invalid refresh token"})
    with pytest.raises(oauth.TwitchOAuthError) as caught:
        oauth.refresh(path, http=fake, now=NOW)
    assert (caught.value.code, caught.value.exit_code) == ("reauth_required", oauth.EXIT_REAUTH)
    assert path.read_text() == before


@pytest.mark.parametrize("facts,code", [
    (_facts(scopes=["chat:read"]), "new_token_missing_scopes"),
    (_facts(user_id="999"), "new_token_not_broadcaster"),
])
def test_unusable_new_token_is_never_stored(tmp_path, facts, code):
    path = _env(tmp_path, CONFIGURED)
    before = path.read_text()
    with pytest.raises(oauth.TwitchOAuthError) as caught:
        oauth.refresh(path, http=FakeTwitch(tokens={"newaccess": facts}), now=NOW)
    assert caught.value.code == code
    assert path.read_text() == before


def _authorize(tmp_path, env_path):
    state_path = tmp_path / "state" / "authorize_state.json"
    url = oauth.authorize_url(env_path, state_path, now=NOW)
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert query["response_type"] == ["code"]
    assert query["redirect_uri"] == ["http://localhost"]
    assert set(query["scope"][0].split()) == set(oauth.REQUIRED_SCOPES)
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600
    return state_path, query["state"][0]


def test_exchange_stores_tokens_from_the_redirected_url(tmp_path):
    path = _env(tmp_path, CONFIGURED.replace("TWITCH_OAUTH_REFRESH_TOKEN=oldrefresh\n", ""))
    state_path, state = _authorize(tmp_path, path)
    fake = FakeTwitch(tokens={"newaccess": _facts()})
    redirected = f"http://localhost/?code=authcode&scope=chat%3Aread&state={state}\n"
    result = oauth.exchange(path, state_path, redirected, http=fake, now=NOW + 60)
    assert (result["status"], result["action"]) == ("ok", "authorized")
    assert fake.token_forms[0]["grant_type"] == "authorization_code"
    assert fake.token_forms[0]["code"] == "authcode"
    assert fake.token_forms[0]["redirect_uri"] == "http://localhost"
    env = oauth.read_env(path)
    assert env["TWITCH_OAUTH_REFRESH_TOKEN"] == "newrefresh"
    assert all(env[key] == "newaccess" for key in oauth.MANAGED_KEYS)
    assert not state_path.exists()


@pytest.mark.parametrize("make_url,delay,code", [
    (lambda s: f"http://localhost/?code=c&state={s}x", 0, "state_mismatch_or_expired"),
    (lambda s: f"http://localhost/?code=c&state={s}", oauth.STATE_TTL_SEC + 1, "state_mismatch_or_expired"),
    (lambda s: "http://localhost/?error=access_denied", 0, "consent_denied"),
])
def test_exchange_rejects_foreign_stale_or_denied_redirects(tmp_path, make_url, delay, code):
    path = _env(tmp_path, CONFIGURED)
    before = path.read_text()
    state_path, state = _authorize(tmp_path, path)
    fake = FakeTwitch(tokens={"newaccess": _facts()})
    with pytest.raises(oauth.TwitchOAuthError) as caught:
        oauth.exchange(path, state_path, make_url(state), http=fake, now=NOW + delay)
    assert caught.value.code == code
    assert fake.token_forms == [] and path.read_text() == before


def test_main_records_secret_free_status(tmp_path, monkeypatch, capsys):
    path = _env(tmp_path, CONFIGURED)
    status_path = tmp_path / "run" / "twitch_oauth" / "status.json"
    monkeypatch.setattr(oauth, "_paths", lambda config: (path, status_path, tmp_path / "s.json"))
    monkeypatch.setattr(oauth, "_http", FakeTwitch(tokens={"newaccess": _facts()}))
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
    monkeypatch.setattr(oauth, "_http", FakeTwitch(token_status=400))
    assert oauth.main(["--config", "x.toml", "refresh"]) == oauth.EXIT_REAUTH
    assert json.loads(status_path.read_text())["status"] == "reauth_required"
    capsys.readouterr()


def test_units_and_deploy_wiring():
    service = (ROOT / "scripts/systemd/docich-twitch-oauth-refresh.service").read_text()
    timer = (ROOT / "scripts/systemd/docich-twitch-oauth-refresh.timer").read_text()
    ensure = (ROOT / "ops/vm_actions/ensure_twitch_oauth_refresh_timer.sh").read_text()
    workflow = (ROOT / ".github/workflows/vm-operations.yml").read_text()
    assert "__DOCICH_ROOT__/bin/docich-twitch-oauth --config" in service
    assert service.count(" refresh\n") == 1
    assert "UMask=0077" in service
    assert "OnUnitInactiveSec=15min" in timer
    assert "docich-twitch-oauth-refresh.service docich-twitch-oauth-refresh.timer" in ensure
    assert "enable --now docich-twitch-oauth-refresh.timer" in ensure
    assert "ensure_twitch_oauth_refresh_timer.sh" in workflow
    wrapper = ROOT / "bin/docich-twitch-oauth"
    assert wrapper.stat().st_mode & 0o111
    # Refresh well inside the ~4h access-token lifetime and the timer period.
    assert oauth.REFRESH_MARGIN_SEC >= 2 * 15 * 60
