"""OpenCode Go の無料枠 2 種が各チェーンとバックオフ既定に残ることを固定する。

`longcat-2.5-preview-free` と `space-bunny-free` は models.dev / `opencode models opencode-go`
で cost.input=cost.output=0 の実在モデル。無料枠なので 429 時に 1 日 backoff を行い、
チェーンには既存の無料枠の並び（先頭付近・有料より前）に置く。
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich.llm.backoff import model_backoff_seconds
from docich.llm.policy import parse_agents
from docich import webui

FREE_GO = ("opencode-go:longcat-2.5-preview-free", "opencode-go:space-bunny-free")

WEBUI_CHAINS = (
    "AI_COMMON_AGENTS",
    "MODEL_IMPROVE_LIST",
    "PEAK_HOURS_AGENT_PREFERENCE",
)


def _raws(chain: str) -> list[str]:
    return [spec.raw for spec in parse_agents(webui.DEFAULTS[chain])]


def _toml_agents(rel: str) -> list[str]:
    import tomllib

    with (ROOT / rel).open("rb") as fp:
        return tomllib.load(fp)


def test_webui_chain_defaults_include_free_go_models():
    for chain in WEBUI_CHAINS:
        raws = _raws(chain)
        for agent in FREE_GO:
            assert agent in raws, f"{chain} に {agent} が無い"


def test_free_go_models_sit_before_paid_opencode_go_models():
    # 無料枠を優先させるため、有料 opencode-go より後ろに置かない。
    paid = "opencode-go:deepseek-v4.1-flash"
    for chain in ("AI_COMMON_AGENTS", "MODEL_IMPROVE_LIST"):
        raws = _raws(chain)
        assert paid in raws
        last_free = max(raws.index(agent) for agent in FREE_GO)
        assert last_free < raws.index(paid), chain


def test_free_go_models_have_daily_backoff_in_defaults():
    env = {"AI_BACKOFF_SEC_ITEMS": webui.DEFAULTS["AI_BACKOFF_SEC_ITEMS"]}
    for agent in FREE_GO:
        spec = parse_agents(agent, env)[0]
        assert model_backoff_seconds(spec, "RADIO", env, now=0) == 86400
        # Native docich jobs do not inherit WebUI DEFAULTS, so the dispatcher
        # itself must retain the safe free-tier default when the env key is absent.
        assert model_backoff_seconds(spec, "RADIO", {}, now=0) == 86400

    # The built-in default is scoped to OpenCode Go; a same-suffix model on
    # another provider keeps the normal RADIO fallback unless explicitly configured.
    other_provider = parse_agents("opencode:space-bunny-free")[0]
    assert model_backoff_seconds(other_provider, "RADIO", {}, now=0) == 18000


def test_dotenv_values_override_webui_defaults():
    chain_override = "opencode-go:deepseek-v4.1-flash"
    backoff_override = "longcat-2.5-preview-free:300"
    assert webui._effective_value(
        "AI_COMMON_AGENTS", {"AI_COMMON_AGENTS": chain_override}
    ) == chain_override
    assert webui._effective_value(
        "MODEL_IMPROVE_LIST", {"MODEL_IMPROVE_LIST": chain_override}
    ) == chain_override
    assert webui._effective_value(
        "AI_BACKOFF_SEC_ITEMS", {"AI_BACKOFF_SEC_ITEMS": backoff_override}
    ) == backoff_override

    spec = parse_agents(FREE_GO[0])[0]
    assert model_backoff_seconds(
        spec, "RADIO", {"AI_BACKOFF_SEC_ITEMS": backoff_override}, now=0
    ) == 300


def test_live_profile_chains_include_free_go_models():
    profile = _toml_agents("config/docich.soren-live.toml")
    chains = {
        "retro_corner.improve_agents": profile["retro_corner"]["improve_agents"],
        "paper_corner.script_agents": profile["paper_corner"]["script_agents"],
        "paper_corner.improve_agents": profile["paper_corner"]["improve_agents"],
    }
    game = _toml_agents("config/games/hanjuku-hero.toml")
    chains["hanjuku.chart_adjust.agents"] = game["hanjuku"]["chart_adjust"]["agents"]

    paid = "opencode-go:deepseek-v4.1-flash"
    for name, raw in chains.items():
        raws = [spec.raw for spec in parse_agents(raw)]
        for agent in FREE_GO:
            assert agent in raws, f"{name} に {agent} が無い"
        assert paid in raws, f"{name} に {paid} が無い"
        assert max(raws.index(agent) for agent in FREE_GO) < raws.index(paid), name
