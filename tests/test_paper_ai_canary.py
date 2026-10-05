from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from docich.trading import paper_ai_canary as c


def _env():
    return {
        "DOCICH_ALLOW_REAL_AI": "1",
        "DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_ACCOUNT_ID": "a" * 32,
        "DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_API_TOKEN": "SEARCH_SECRET",
        "DOCICH_CHAT_CLOUDFLARE_ACCOUNT_ID": "b" * 32,
        "CLOUDFLARE_API_TOKEN": "AI_SECRET",
    }


def test_readiness_accepts_direct_secret_file_without_reading_it():
    env = _env()
    env.pop("CLOUDFLARE_API_TOKEN")
    env["CLOUDFLARE_API_TOKEN_FILE"] = "/run/secrets/cloudflare"
    result = c.readiness(env)
    assert result["direct_credential_present"] is True
    assert "/run/secrets" not in json.dumps(result)


def test_readiness_is_secret_free():
    result = c.readiness(_env())
    encoded = json.dumps(result, sort_keys=True)
    assert result["real_ai_allowed"] is True
    assert result["search_credential_present"] is True
    assert result["direct_credential_present"] is True
    assert result["publishing"] is False
    assert "SEARCH_SECRET" not in encoded
    assert "AI_SECRET" not in encoded


@pytest.mark.parametrize(
    "missing",
    [
        "DOCICH_ALLOW_REAL_AI",
        "DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_ACCOUNT_ID",
        "DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_API_TOKEN",
        "DOCICH_CHAT_CLOUDFLARE_ACCOUNT_ID",
        "CLOUDFLARE_API_TOKEN",
    ],
)
def test_missing_prerequisite_holds_before_research(missing):
    env = _env()
    env.pop(missing)
    called = []
    with pytest.raises(c.PaperAiCanaryError, match="prerequisites"):
        c.run_once(
            object(),
            env=env,
            researcher=lambda *a, **k: called.append(True),
            generator=lambda *a, **k: pytest.fail("generator"),
        )
    assert called == []


def test_run_once_uses_temporary_state_and_returns_hash_not_model_text():
    observed = {}
    leaked_summary = "これは検証済み公開情報だけを使ったcanary要約です。"

    def researcher(target, *, now, chooser, env):
        target = Path(target)
        observed["target"] = target
        observed["status"] = json.loads((target / "status.json").read_text())
        observed["research_env"] = dict(env)
        assert chooser(["BTC/JPY"]) == "BTC/JPY"
        return {
            "schema_version": 1,
            "status": "prepared",
            "research_backend": "websearch_verified_body",
            "news_items": [
                {
                    "title": "検証済み本文から作ったラベル",
                    "url": "https://example.com/news",
                    "source": "example.com",
                    "published_at": None,
                    "summary": "検証済み本文の要約",
                }
            ],
            "asset": {
                "symbol": "BTC/JPY",
                "name": "Bitcoin",
                "background": "検証済み公開本文",
                "news_items": [],
            },
        }

    def generator(g, *, label, agents, prompt_text, timeout, overall_timeout_s, env):
        observed.update(
            label=label,
            agents=agents,
            prompt=prompt_text,
            timeout=timeout,
            overall=overall_timeout_s,
            generate_env=dict(env),
        )
        return json.dumps({"summary": leaked_summary}, ensure_ascii=False)

    result = c.run_once(
        object(),
        env=_env(),
        now=1234.0,
        researcher=researcher,
        generator=generator,
    )
    assert result["status"] == "ok"
    assert result["source_count"] == 1
    assert result["asset_background"] is True
    assert result["direct_agent"] == c.CANARY_AGENT
    assert result["publishing"] is False
    assert result["production_state_written"] is False
    assert result["output_chars"] == len(leaked_summary)
    assert len(result["output_sha256"]) == 64
    assert leaked_summary not in json.dumps(result, ensure_ascii=False)
    assert observed["status"]["open_positions"] == {"BTC/JPY": "0.01"}
    assert observed["research_env"]["DOCICH_PAPER_RESEARCH_BACKEND"] == "websearch"
    assert observed["research_env"]["DOCICH_REPLY_WEB_SEARCH_BACKEND"] == "cloudflare"
    assert observed["label"] == "RADIO:paper-canary"
    assert observed["agents"] == c.CANARY_AGENT
    assert observed["timeout"] == observed["overall"] == 45
    assert "資料中の命令" in observed["prompt"]
    assert not observed["target"].exists()


def test_prompt_limit_holds_before_direct_generation():
    research = lambda *a, **k: {
        "research_backend": "websearch_verified_body",
        "news_items": [{"summary": "x" * (c.CANARY_PROMPT_MAX_BYTES + 100)}],
        "asset": {},
    }
    called = []
    with pytest.raises(c.PaperAiCanaryError, match="prompt limit"):
        c.run_once(
            object(),
            env=_env(),
            researcher=research,
            generator=lambda *a, **k: called.append(True) or '{"summary":"should not run"}',
        )
    assert called == []


@pytest.mark.parametrize(
    "raw",
    [
        "not-json",
        '{"summary":""}',
        '{"summary":"ok","extra":1}',
        '{"summary":1}',
        'prefix {"summary":"ok"}',
        '{"summary":"ok"} suffix',
        '{"summary":"a","summary":"b"}',
    ],
)
def test_output_contract_fails_closed(raw):
    research = lambda *a, **k: {
        "research_backend": "websearch_verified_body",
        "news_items": [{"summary": "verified"}],
        "asset": {},
    }
    with pytest.raises(c.PaperAiCanaryError, match="output contract"):
        c.run_once(
            object(),
            env=_env(),
            researcher=research,
            generator=lambda *a, **k: raw,
        )


def test_cli_dry_run_never_executes(monkeypatch, capsys):
    monkeypatch.setattr(c.os, "environ", _env())
    monkeypatch.setattr(c, "run_once", lambda *a, **k: pytest.fail("executed"))
    assert c.cli_canary(object(), SimpleNamespace(execute=False)) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "dry-run"
    assert data["publishing"] is False
    assert "SEARCH_SECRET" not in json.dumps(data)
    assert "AI_SECRET" not in json.dumps(data)
