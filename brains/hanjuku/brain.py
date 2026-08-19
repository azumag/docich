#!/usr/bin/env python3
"""半熟英雄 (SFC) 用の外部 brain コマンド。

設計書: docs/hanjuku_brain.md (docich 本体の契約は architecture.md §3/§6)。

docich との境界は CommandBrain のまま変えない: stdin から Observation JSON を
1件読み、stdout には検証済みの `{"actions": [...]}` だけを出力する。毎サイクル
新規プロセスとして起動されるステートレスなコマンドであり、記憶 (state.json /
notes.md) や LLM 呼び出しはすべてこのスクリプト自身の責務である。

Python 3.11+ 標準ライブラリのみで動く。`DOCICH_BRAIN_LLM=api` を選んだときだけ、
呼び出し関数の内部で `anthropic` を import する (未導入なら pip 案内を出して exit 3)。

終了コード:
    0 = 正常 (actions が空でもよい)
    2 = 入力 (stdin の Observation JSON) が不正
    3 = LLM バックエンド呼び出しに失敗 (timeout / 非0終了 / API 例外 / anthropic 未導入)
    4 = LLM 応答の解析・自己検証に失敗
非0終了時、docich 側の CommandBrain は警告ログを出して空アクションで継続する (fail-soft)。
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# --- docich 本体のスキーマを再利用する (自分では複製しない。hanjuku_brain.md §1) ---
REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_DIR = str(REPO_ROOT / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from docich.actions import ActionError, extract_json, parse_actions  # noqa: E402

# --- 定数 (design doc §0-§3) ------------------------------------------------

STATE_SUBDIR = Path("run") / "brain" / "hanjuku"
KNOWLEDGE_ROOT = Path("games") / "hanjuku-sfc-speedrun"

DEFAULT_STAGE = 1
MIN_STAGE = 1
MAX_STAGE = 12

MAX_HOLD_MS = 2000
MAX_WAIT_MS = 10000
NOTES_MAX_CHARS = 8000

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_CLAUDE_BIN = "claude"
DEFAULT_LLM_TIMEOUT_S = 100.0
DEFAULT_MAX_ACTIONS = 8
DEFAULT_THINKING = "off"

API_MAX_TOKENS = 2000


class BrainInputError(Exception):
    """stdin の Observation JSON が不正 (exit 2)."""


class BrainLLMError(Exception):
    """LLM バックエンド呼び出しに失敗した (exit 3)."""


class BrainResponseError(Exception):
    """LLM 応答の解析・スキーマ検証 (自己検証含む) に失敗した (exit 4)."""


# ---------------------------------------------------------------------------
# 設定 (環境変数はすべて brain 内でのみ解釈する。hanjuku_brain.md §2)
# ---------------------------------------------------------------------------


def _env_float(env: dict, name: str, default: float) -> float:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(env: dict, name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass
class BrainConfig:
    llm: str = "claude-cli"
    model: str = DEFAULT_MODEL
    claude_bin: str = DEFAULT_CLAUDE_BIN
    llm_timeout_s: float = DEFAULT_LLM_TIMEOUT_S
    max_actions: int = DEFAULT_MAX_ACTIONS
    thinking: str = DEFAULT_THINKING

    @classmethod
    def from_env(cls, env: dict | None = None) -> "BrainConfig":
        env = env if env is not None else os.environ
        return cls(
            llm=env.get("DOCICH_BRAIN_LLM", "claude-cli"),
            model=env.get("DOCICH_BRAIN_MODEL", DEFAULT_MODEL),
            claude_bin=env.get("DOCICH_BRAIN_CLAUDE_BIN", DEFAULT_CLAUDE_BIN),
            llm_timeout_s=_env_float(env, "DOCICH_BRAIN_LLM_TIMEOUT_S", DEFAULT_LLM_TIMEOUT_S),
            max_actions=_env_int(env, "DOCICH_BRAIN_MAX_ACTIONS", DEFAULT_MAX_ACTIONS),
            thinking=env.get("DOCICH_BRAIN_THINKING", DEFAULT_THINKING),
        )


# ---------------------------------------------------------------------------
# 状態ディレクトリ: run/brain/hanjuku/ (design doc §0)
# ---------------------------------------------------------------------------


def state_dir_for(repo_root: Path) -> Path:
    return repo_root / STATE_SUBDIR


def ensure_state_dir(state_dir: Path) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)


def load_state(state_dir: Path) -> dict:
    """state.json を読む。無い/壊れている場合は空の進行状態として扱う (クラッシュしない)。"""
    path = state_dir / "state.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(state_dir: Path, state: dict) -> None:
    path = state_dir / "state.json"
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def merge_state_patch(state: dict, patch: dict | None) -> dict:
    """state_patch を state.json へ浅いマージする (design doc §3)。元の dict は変更しない。"""
    merged = dict(state)
    if patch:
        merged.update(patch)
    return merged


def clamp_stage(value) -> int:
    """state.json の "stage" を 1..12 に丸める。int に変換できなければ既定値 (1) にする。"""
    try:
        stage = int(value)
    except (TypeError, ValueError):
        return DEFAULT_STAGE
    return max(MIN_STAGE, min(MAX_STAGE, stage))


def load_notes_tail(state_dir: Path, *, max_chars: int = NOTES_MAX_CHARS) -> str:
    path = state_dir / "notes.md"
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    return text[-max_chars:]


def append_note(state_dir: Path, note: str, *, now: datetime | None = None, max_chars: int = NOTES_MAX_CHARS) -> None:
    """note を `## <ISO8601>` 見出し付きで notes.md に追記し、ファイル全体を末尾 max_chars に切り詰める。"""
    path = state_dir / "notes.md"
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    ts = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    updated = existing + f"\n## {ts}\n{note}\n"
    if len(updated) > max_chars:
        updated = updated[-max_chars:]
    path.write_text(updated, encoding="utf-8")


def append_log(
    state_dir: Path,
    *,
    ts: float,
    backend: str,
    latency_ms: int,
    n_actions: int,
    truncated: bool,
    error: str | None,
) -> None:
    """brain.log へ 1 サイクル 1 行の JSONL を追記する (design doc §0)。"""
    path = state_dir / "brain.log"
    record = {
        "ts": ts,
        "backend": backend,
        "latency_ms": latency_ms,
        "n_actions": n_actions,
        "truncated": truncated,
        "error": error,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# 知識注入 (games/hanjuku-sfc-speedrun サブモジュールを読み取り専用で参照。design doc §3)
# ---------------------------------------------------------------------------


def knowledge_files(stage: int) -> dict[str, Path]:
    """常時注入する README/overview と、進行連動の charts/{stage}.md の相対パス表。"""
    return {
        "README.md": KNOWLEDGE_ROOT / "README.md",
        "charts/overview.md": KNOWLEDGE_ROOT / "charts" / "overview.md",
        f"charts/{stage}.md": KNOWLEDGE_ROOT / "charts" / f"{stage}.md",
    }


def load_knowledge(repo_root: Path, stage: int) -> dict[str, str]:
    """知識ファイルを読む。無ければ (サブモジュール未取得等) 「知識ファイルなし: <パス>」を
    その項目の内容として続行する (クラッシュしない)。"""
    result: dict[str, str] = {}
    for label, rel_path in knowledge_files(stage).items():
        path = repo_root / rel_path
        try:
            result[label] = path.read_text(encoding="utf-8")
        except OSError:
            result[label] = f"知識ファイルなし: {path}"
    return result


# ---------------------------------------------------------------------------
# スクリーンショット解決
# ---------------------------------------------------------------------------


def resolve_screenshot(repo_root: Path, screenshot: str | None) -> Path | None:
    """obs.screenshot を repo_root 基準の絶対パスに解決する。
    None またはファイル欠損なら None を返す (呼び出し側は「スクリーンショットなし」として扱う)。"""
    if not screenshot:
        return None
    path = Path(screenshot)
    if not path.is_absolute():
        path = repo_root / path
    if not path.is_file():
        return None
    return path


# ---------------------------------------------------------------------------
# プロンプト組み立て (claude-cli / api 共通。design doc §3)
# ---------------------------------------------------------------------------


def build_prompt(obs: dict, knowledge: dict[str, str], state: dict, notes_tail: str) -> str:
    """毎サイクルのユーザーメッセージ本文を組み立てる。claude-cli / api の両バックエンドが
    同じ本文を使う (api はこれをテキストブロックとし、別途画像ブロックを添付する)。

    `obs["screenshot"]` は既に絶対パス文字列 (または None) に解決済みであることを前提とする
    (resolve_screenshot 参照)。こうすることで本関数はファイルシステム/cwd に依存しない
    純粋な文字列組み立てになり、テストしやすい。
    """
    lines: list[str] = []

    lines.append("# 役割")
    lines.append(
        "あなたはSFC「半熟英雄」をRTAチャートに沿って自動プレイするAIです。"
        "SNESパッドの意味ボタン (a/b/x/y/l/r/start/select/up/down/left/right) を"
        "JSONで返してください。"
    )
    lines.append("")

    lines.append("# 操作契約")
    lines.append(
        "応答はJSONのみを返してください。hold_msの既定は100msです。"
        "メニュー操作は1〜3手ずつ確実に行ってください。"
        "画面が読めない・判断できないときは actions を空配列にするか、短い wait を返してください。"
    )
    lines.append("")

    lines.append("# 知識")
    for label, content in knowledge.items():
        lines.append(f"## {label}")
        lines.append(content)
        lines.append("")

    lines.append("# 申し送りメモ (notes.md 末尾)")
    lines.append(notes_tail if notes_tail else "(まだメモはありません)")
    lines.append("")

    lines.append("# 進行状態 (state.json)")
    lines.append(json.dumps(state, ensure_ascii=False))
    lines.append("")

    lines.append("# スクリーンショット")
    screenshot = obs.get("screenshot")
    if screenshot:
        lines.append(f"スクリーンショット: {screenshot} を Read ツールで確認してから判断せよ")
    else:
        lines.append("スクリーンショットなし")
    lines.append("")

    lines.append("# 応答形式")
    lines.append("次のJSON形式のみで応答してください (説明文やコードフェンスは付けないでください):")
    lines.append(
        '{"note": "画面の要約と次の方針 (1-3行)", "state_patch": {"stage": 2}, '
        '"actions": [{"type": "pad", "buttons": ["a"], "hold_ms": 120}]}'
    )
    lines.append(
        "note は必須です (毎回 notes.md に追記されます)。"
        "state_patch は任意です (state.json への浅いマージ。進行の自己申告)。"
        "actions は必須です (docich の行動スキーマ。空配列も可)。"
    )

    return "\n".join(lines)


def build_api_content(prompt: str, screenshot_path: Path | None) -> list[dict]:
    """api モードのメッセージ content を組み立てる: 画像ブロック (base64, image/png) +
    テキストブロック (design doc §2/§3)。screenshot_path が None ならテキストブロックのみ。"""
    blocks: list[dict] = []
    if screenshot_path is not None:
        data = base64.b64encode(screenshot_path.read_bytes()).decode("ascii")
        blocks.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": data},
            }
        )
    blocks.append({"type": "text", "text": prompt})
    return blocks


# ---------------------------------------------------------------------------
# LLM 応答の解析 (note/state_patch/actions 封筒。design doc §3-7)
# ---------------------------------------------------------------------------


def parse_llm_response(raw_text: str) -> dict:
    """LLM の応答テキストを {"note", "state_patch", "actions"} に解析する。
    ```json フェンスは docich.actions.extract_json で剥がす。"""
    try:
        json_text = extract_json(raw_text)
    except ActionError as exc:
        raise BrainResponseError(f"応答から JSON を取り出せませんでした: {exc}") from exc
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise BrainResponseError(f"応答 JSON の解析に失敗しました: {exc}") from exc

    if not isinstance(data, dict):
        raise BrainResponseError(f"応答は object である必要があります: {type(data).__name__}")

    note = data.get("note")
    if not isinstance(note, str) or not note:
        raise BrainResponseError("応答に note (空でない文字列) がありません")

    actions = data.get("actions")
    if not isinstance(actions, list):
        raise BrainResponseError("応答に actions (リスト) がありません")

    state_patch = data.get("state_patch")
    if state_patch is not None and not isinstance(state_patch, dict):
        raise BrainResponseError(f"state_patch は object である必要があります: {state_patch!r}")

    return {"note": note, "state_patch": state_patch, "actions": actions}


def _is_plain_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def sanitize_actions(actions: list, max_actions: int) -> tuple[list, bool]:
    """DOCICH_BRAIN_MAX_ACTIONS 件に切り捨て、pad/key の hold_ms を MAX_HOLD_MS に、
    wait の ms を MAX_WAIT_MS に上から丸める (design doc §3)。戻り値は (整形後のリスト, 切り捨てたか)。"""
    truncated = len(actions) > max_actions
    sanitized: list = []
    for item in actions[:max_actions]:
        if isinstance(item, dict):
            item = dict(item)
            action_type = item.get("type")
            if action_type in ("pad", "key") and _is_plain_int(item.get("hold_ms")):
                if item["hold_ms"] > MAX_HOLD_MS:
                    item["hold_ms"] = MAX_HOLD_MS
            if action_type == "wait" and _is_plain_int(item.get("ms")):
                if item["ms"] > MAX_WAIT_MS:
                    item["ms"] = MAX_WAIT_MS
        sanitized.append(item)
    return sanitized, truncated


# ---------------------------------------------------------------------------
# LLM バックエンド (design doc §2)
# ---------------------------------------------------------------------------


def _claude_cli_argv(cfg: BrainConfig) -> list[str]:
    """claude CLI の argv を組み立てる純関数 (テスト用に分離)。"""
    return [cfg.claude_bin, "-p", "--model", cfg.model, "--output-format", "text"]


def call_claude_cli(prompt: str, cfg: BrainConfig) -> str:
    """claude-cli バックエンド: `claude -p` にプロンプトを stdin で渡す。"""
    argv = _claude_cli_argv(cfg)
    try:
        result = subprocess.run(
            argv,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=cfg.llm_timeout_s,
        )
    except FileNotFoundError as exc:
        raise BrainLLMError(f"claude CLI が見つかりません ({cfg.claude_bin}): {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise BrainLLMError(f"claude CLI がタイムアウトしました ({cfg.llm_timeout_s}秒): {exc}") from exc
    except OSError as exc:
        raise BrainLLMError(f"claude CLI の起動に失敗しました: {exc}") from exc

    if result.returncode != 0:
        raise BrainLLMError(
            f"claude CLI が異常終了しました (code={result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout


def call_api(prompt: str, screenshot_path: Path | None, cfg: BrainConfig) -> str:
    """api バックエンド: Anthropic Python SDK を選択時のみ import する
    (docich 本体の stdlib-only 制約を破らない。未導入なら導入案内を添えて失敗させる)。"""
    try:
        import anthropic  # 選択時のみ import。ImportError は下で BrainLLMError に変換する。
    except ImportError as exc:
        raise BrainLLMError(
            "anthropic パッケージが見つかりません。`pip install anthropic` を実行してください"
        ) from exc

    client = anthropic.Anthropic()
    content = build_api_content(prompt, screenshot_path)
    kwargs: dict = {}
    if cfg.thinking == "adaptive":
        kwargs["thinking"] = {"type": "adaptive"}

    try:
        response = client.messages.create(
            model=cfg.model,
            max_tokens=API_MAX_TOKENS,
            messages=[{"role": "user", "content": content}],
            **kwargs,
        )
    except Exception as exc:  # Anthropic SDK の例外を広く brain の失敗として扱う
        raise BrainLLMError(f"Anthropic API 呼び出しに失敗しました: {exc}") from exc

    parts = [getattr(block, "text", None) for block in response.content]
    return "".join(part for part in parts if part)


def _read_fake_cursor(cursor_path: Path) -> int:
    if not cursor_path.is_file():
        return 0
    try:
        return int(cursor_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def _write_fake_cursor(cursor_path: Path, value: int) -> None:
    cursor_path.write_text(str(value), encoding="utf-8")


def call_fake(spec: str, repo_root: Path, cursor_path: Path) -> str:
    """`fake:<path>` バックエンド: LLM を呼ばずファイルの内容を応答として返す。
    .json = 毎回同一応答 / .jsonl = 1サイクル1行を順番に消費し、cursor_path に
    次に読む行番号を保存する (行数で wrap around する。design doc §2)。"""
    path = Path(spec)
    if not path.is_absolute():
        path = repo_root / path
    if not path.is_file():
        raise BrainLLMError(f"fake 応答ファイルが見つかりません: {path}")

    if path.suffix == ".json":
        return path.read_text(encoding="utf-8")

    if path.suffix == ".jsonl":
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not lines:
            raise BrainLLMError(f"fake 応答ファイルが空です: {path}")
        cursor = _read_fake_cursor(cursor_path)
        index = cursor % len(lines)
        _write_fake_cursor(cursor_path, cursor + 1)
        return lines[index]

    raise BrainLLMError(f"fake 応答ファイルの拡張子が不明です (.json/.jsonl のみ対応): {path}")


def call_backend(
    prompt: str,
    screenshot_path: Path | None,
    cfg: BrainConfig,
    *,
    repo_root: Path,
    state_dir: Path,
) -> str:
    """DOCICH_BRAIN_LLM の値に応じてバックエンドへ振り分ける。"""
    if cfg.llm == "claude-cli":
        return call_claude_cli(prompt, cfg)
    if cfg.llm == "api":
        return call_api(prompt, screenshot_path, cfg)
    if cfg.llm.startswith("fake:"):
        spec = cfg.llm[len("fake:"):]
        return call_fake(spec, repo_root, state_dir / "fake_cursor")
    raise BrainLLMError(f"未知の DOCICH_BRAIN_LLM です: {cfg.llm!r} (使用可能: claude-cli, api, fake:<path>)")


# ---------------------------------------------------------------------------
# 1サイクル (observe は docich 側で完結済み。ここでは brain だけの処理)
# ---------------------------------------------------------------------------


def parse_observation(raw_stdin: str) -> dict:
    """stdin から受け取った Observation JSON (architecture.md §3.1) をパースする。"""
    if not raw_stdin or not raw_stdin.strip():
        raise BrainInputError("stdin が空です (Observation JSON が渡されていません)")
    try:
        obs = json.loads(raw_stdin)
    except json.JSONDecodeError as exc:
        raise BrainInputError(f"Observation JSON の解析に失敗しました: {exc}") from exc
    if not isinstance(obs, dict):
        raise BrainInputError(f"Observation は object である必要があります: {type(obs).__name__}")
    return obs


def run_cycle(obs: dict, *, repo_root: Path, state_dir: Path, cfg: BrainConfig) -> dict:
    """observe 済みの Observation dict から 1 サイクル分の brain 処理を行う。

    知識注入・プロンプト組み立て・LLM 呼び出し・応答解析・サニタイズ・自己検証・
    state.json/notes.md への反映までを行い、{"actions": [...], "truncated": bool} を返す。
    repo_root/state_dir を引数で受け取るため、テストは実リポジトリの run/ を汚さず
    tmpdir を渡すだけで済む。state_dir が無ければここで作る (呼び出し側に事前 mkdir を要求しない)。
    """
    ensure_state_dir(state_dir)
    state = load_state(state_dir)
    stage = clamp_stage(state.get("stage", DEFAULT_STAGE))
    knowledge = load_knowledge(repo_root, stage)
    notes_tail = load_notes_tail(state_dir)

    screenshot_path = resolve_screenshot(repo_root, obs.get("screenshot"))
    prompt_obs = dict(obs)
    prompt_obs["screenshot"] = str(screenshot_path) if screenshot_path is not None else None
    prompt = build_prompt(prompt_obs, knowledge, state, notes_tail)

    raw_response = call_backend(prompt, screenshot_path, cfg, repo_root=repo_root, state_dir=state_dir)
    parsed = parse_llm_response(raw_response)

    sanitized, truncated = sanitize_actions(parsed["actions"], cfg.max_actions)
    if truncated:
        print(
            f"brain: actions を {cfg.max_actions} 件に切り捨てました (元 {len(parsed['actions'])} 件)",
            file=sys.stderr,
        )

    # 送出前の自己検証 (docich.actions.parse_actions を再利用。hanjuku_brain.md §1)。
    try:
        parse_actions({"actions": sanitized})
    except ActionError as exc:
        raise BrainResponseError(f"actions の自己検証に失敗しました: {exc}") from exc

    new_state = merge_state_patch(state, parsed["state_patch"])
    save_state(state_dir, new_state)
    append_note(state_dir, parsed["note"])

    return {"actions": sanitized, "truncated": truncated}


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------


def _log_failure(state_dir: Path, cfg: BrainConfig, t0: float, exc: Exception) -> None:
    latency_ms = int((time.monotonic() - t0) * 1000)
    append_log(
        state_dir,
        ts=time.time(),
        backend=cfg.llm,
        latency_ms=latency_ms,
        n_actions=0,
        truncated=False,
        error=str(exc),
    )


def main(*, repo_root: Path | None = None) -> int:
    """CLI エントリポイント。`repo_root` はテスト用の差し替え口 (既定は実リポジトリの REPO_ROOT)。"""
    root = repo_root if repo_root is not None else REPO_ROOT
    cfg = BrainConfig.from_env()
    raw_stdin = sys.stdin.read()

    try:
        obs = parse_observation(raw_stdin)
    except BrainInputError as exc:
        print(f"brain: 入力エラー: {exc}", file=sys.stderr)
        return 2

    state_dir = state_dir_for(root)
    ensure_state_dir(state_dir)

    t0 = time.monotonic()
    try:
        result = run_cycle(obs, repo_root=root, state_dir=state_dir, cfg=cfg)
    except BrainLLMError as exc:
        _log_failure(state_dir, cfg, t0, exc)
        print(f"brain: LLM 呼び出しに失敗しました: {exc}", file=sys.stderr)
        return 3
    except BrainResponseError as exc:
        _log_failure(state_dir, cfg, t0, exc)
        print(f"brain: 応答の解析・検証に失敗しました: {exc}", file=sys.stderr)
        return 4

    latency_ms = int((time.monotonic() - t0) * 1000)
    append_log(
        state_dir,
        ts=time.time(),
        backend=cfg.llm,
        latency_ms=latency_ms,
        n_actions=len(result["actions"]),
        truncated=result["truncated"],
        error=None,
    )

    print(json.dumps({"actions": result["actions"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
