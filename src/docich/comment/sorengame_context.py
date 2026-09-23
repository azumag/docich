"""Soren game context adapter for the native comment core (#829 PR-3d).

This module is an optional game-owned provider. The comment core defines the
protocol and never imports this adapter; a host chooses and injects it.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
import re
from typing import Mapping

from .contexts import GameContext, normalize_host_mode


SOREN91_GAME_STATE_NOTE = (
    "- いまのメイン画面はソ連ゲーム91(対戦版/メリケンAI)です。このゲームにはスコアの概念がなく、"
    "順位(何人抜きで何位だったか)で振り返ります。本編(ソレンゲーム)のスコア・建国統計は、"
    "いまの画面のゲームのものではないので、91のスコアとして読み上げないこと。"
)

GAME_BLURBS = {
    "sorengame": "ソ連ゲーム(Unity WebGL)。AIが戦略を改善しながらプレイし、ロシア建国・ソ連建国を目指す",
    "robots": "Robots(bsdgames)。ロボットの追跡を避けるターン制パズル(いまは手動操作)",
    "nethack": "NetHack(CLIローグライク)",
    "hanjuku-hero": "半熟英雄(SFCエミュレータ)",
}
ROUTING_NOTE = (
    "- 返答の話題はメイン画面のゲームに合わせる。コメントがソ連ゲームの話"
    "(ソ連建国・スコア・戦略改善)なら、画面が違ってもソレンゲームの話として答える"
)
STATE_LABELS = {
    "MOVE": "プレイ中",
    "WAITING": "待機中",
    "RESULT": "結果画面",
    "GAMEOVER": "ゲームオーバー",
    "START": "開始前",
}
SOREN_CONTEXT_ENV_KEYS = (
    "AB_STATE_FILE",
    "CODEX_WORK_OVERLAY_STATE_FILE",
    "COMMENT_CELEBRATION_HISTORY_ITEMS",
    "COMMENT_OPS_BRIEF_FILE",
    "COMMENT_OPS_BRIEF_ITEMS",
    "COMMENT_OPS_CONTEXT_MAX_CHARS",
    "DOCICH_GAME_SWITCH_CANONICAL_FILE",
    "GAME_STATE",
    "IMPROVE_STATE_FILE",
    "MIN_GAMES_BEFORE_IMPROVE",
    "PREDICTION_WORKER_PAUSED_FILE",
    "RUSSIA_CREATION_HISTORY_FILE",
    "SOREN_GAME_COUNT_FILE",
    "SOREN_GAME_LIFECYCLE_DIR",
    "SOREN_GAME_STATE_FILE",
    "SOREN_IMPROVE_PAUSED_FILE",
    "SOVIET_CREATION_ARCHIVE_FILE",
    "SOVIET_CREATION_HISTORY_FILE",
    "TMP_HISTORY_DIR",
)


class SorenGameContextProvider:
    """Build the legacy Soren-owned game/ops/celebration context from files."""

    def __init__(self, root: str | Path, env: Mapping[str, str] | None = None):
        self.root = Path(root)
        supplied = env or {}
        self.env = {key: supplied[key] for key in SOREN_CONTEXT_ENV_KEYS if key in supplied}

    def _path(self, name: str, default: str) -> Path:
        raw = self.env.get(name) or default
        path = Path(raw)
        return path if path.is_absolute() else self.root / path

    def _optional_path(self, name: str) -> Path | None:
        raw = self.env.get(name) or ""
        if not raw:
            return None
        path = Path(raw)
        return path if path.is_absolute() else self.root / path

    def build(self, *, host_mode: str) -> GameContext:
        mode = normalize_host_mode(host_mode)
        game_state = SOREN91_GAME_STATE_NOTE if mode == "soren91" else self._game_state_context()
        return GameContext(
            game_state_context=game_state,
            comment_ops_context=self._ops_context(mode),
            celebration_history_context=self._celebration_history_context(),
        )

    def _read_json(self, path: Path | None) -> dict:
        if path is None:
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _read_scores(path: Path) -> list[int]:
        scores = []
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            return scores
        for raw in lines:
            cols = raw.strip().split("\t")
            if not cols:
                continue
            try:
                scores.append(int(cols[-1]))
            except Exception:
                continue
        return scores

    @staticmethod
    def _read_creation_entries(path: Path) -> list[dict]:
        rows = []
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            return rows
        for raw in lines:
            cols = raw.strip().split("\t")
            if len(cols) < 5:
                continue
            _iso_ts, local_ts, game_num, score, turns = cols[:5]
            try:
                game_num_value = int(game_num)
            except Exception:
                game_num_value = None
            try:
                score_value = int(score)
            except Exception:
                score_value = None
            try:
                turns_value = int(turns)
            except Exception:
                turns_value = None
            rows.append({
                "local_ts": local_ts.strip(),
                "game_num": game_num_value,
                "score": score_value,
                "turns": turns_value,
            })
        return rows

    @staticmethod
    def _fmt_int(value) -> str:
        if value is None:
            return "不明"
        try:
            return f"{int(value)}"
        except Exception:
            return "不明"

    @staticmethod
    def _summarize_scores(scores: list[int]) -> str:
        if not scores:
            return "score_history.txt は未取得"
        total = len(scores)
        ordered = sorted(scores)
        recent12 = scores[-12:]
        recent50 = scores[-50:]
        recent200 = scores[-200:]
        recent1000 = scores[-1000:]
        parts = [
            f"終了スコア履歴件数={total}",
            f"全体平均={sum(scores) / total:.0f}",
            f"全体中央値={statistics.median(scores):.0f}",
            f"全体最高={max(scores)}",
            f"全体p90={ordered[max(0, min(total - 1, int(total * 0.90) - 1))]}",
            f"直近終了スコア={scores[-1]}",
            f"直近12平均={sum(recent12) / len(recent12):.0f}",
            f"直近12最高={max(recent12)}",
        ]
        if len(recent50) >= 2:
            parts.extend((f"直近50平均={sum(recent50) / len(recent50):.0f}",
                          f"直近50中央値={statistics.median(recent50):.0f}"))
        if len(recent200) >= 2:
            parts.append(f"直近200平均={sum(recent200) / len(recent200):.0f}")
        if len(recent1000) >= 2:
            parts.append(f"直近1000平均={sum(recent1000) / len(recent1000):.0f}")
        return ", ".join(parts)

    @staticmethod
    def _summarize_creation(label: str, entries: list[dict], score_history_count: int) -> str:
        if not entries:
            return f"{label}: total=0, 直近なし"
        total = len(entries)
        latest = entries[-1]
        known_game_numbers = [row["game_num"] for row in entries if row["game_num"] is not None]
        latest_known_game = max(known_game_numbers) if known_game_numbers else None
        parts = [f"{label}: total={total}", f"直近={latest['local_ts'] or '不明'}"]
        if latest["game_num"] is not None:
            parts.append(f"Game#{latest['game_num']}")
            if score_history_count and score_history_count >= latest["game_num"]:
                parts.append(f"そこから約{score_history_count - latest['game_num']}ゲーム経過")
        if latest["score"] is not None:
            parts.append(f"score={latest['score']}")
        if latest["turns"] is not None:
            parts.append(f"turns={latest['turns']}")
        if score_history_count:
            parts.append(f"score_history件数基準の通算率目安={total / score_history_count * 100:.2f}%")
        if latest_known_game:
            for window in (100, 500, 1000):
                recent = [row for row in entries if row["game_num"] is not None
                          and row["game_num"] > latest_known_game - window]
                parts.append(f"履歴Game#基準の直近{window}ゲーム内={len(recent)}回")
        return ", ".join(parts)

    def _game_state_context(self) -> str:
        game_state_file = self._path("GAME_STATE", "game_state.json")
        try:
            game_state = json.loads(game_state_file.read_text(encoding="utf-8"))
        except Exception:
            return "（game_state.json を読めませんでした）"

        state = game_state.get("state", "?")
        score = game_state.get("score")
        record = game_state.get("record", 0)
        make_soren_count = game_state.get("makeSorenCount")
        piece_count = game_state.get("pieceCount")
        pieces = game_state.get("pieces") if isinstance(game_state.get("pieces"), list) else []
        type_counts: dict[int, int] = {}
        max_type = None
        for piece in pieces:
            try:
                piece_type = int(piece.get("type"))
            except Exception:
                continue
            type_counts[piece_type] = type_counts.get(piece_type, 0) + 1
            max_type = piece_type if max_type is None else max(max_type, piece_type)
        top_types = sorted(type_counts.items(), key=lambda item: (-item[0], item[1]))[:6]
        top_type_text = ", ".join(f"type{piece_type}x{count}" for piece_type, count in top_types) \
            if top_types else "盤面ピース情報なし"
        next_piece = game_state.get("next") if isinstance(game_state.get("next"), dict) else {}
        next_next_piece = game_state.get("nextNext") if isinstance(game_state.get("nextNext"), dict) else {}

        history_dir = self.env.get("TMP_HISTORY_DIR") or "tmp/history"
        # The legacy helper reads this fixed file relative to ELOOP_LIB_DIR;
        # do not accidentally honor a worker-only SCORE_HISTORY_FILE override.
        scores = self._read_scores(self.root / "score_history.txt")
        russia = self._read_creation_entries(self._path(
            "RUSSIA_CREATION_HISTORY_FILE", f"{history_dir}/russia_creation_history.tsv"))
        soviet = self._read_creation_entries(self._path(
            "SOVIET_CREATION_HISTORY_FILE", f"{history_dir}/soviet_creation_history.tsv"))
        try:
            prediction_cycle_games = max(1, int(self.env.get("MIN_GAMES_BEFORE_IMPROVE") or "12"))
        except Exception:
            prediction_cycle_games = 12
        total_games = len(scores)
        cycle_completed = total_games % prediction_cycle_games if total_games else None
        cycle_position = cycle_completed + 1 if cycle_completed is not None else None

        lines = [
            "ライブ局面は生成から読み上げまでにズレやすい。返答の中心は終了ゲーム履歴・建国履歴の全体統計に置くこと。",
            f"ライブ状態補助: state={state}({STATE_LABELS.get(str(state), '不明')}), snapshot_score={self._fmt_int(score)}, record={self._fmt_int(record)}, makeSorenCount={self._fmt_int(make_soren_count)}, pieceCount={self._fmt_int(piece_count)}",
            f"ライブ盤面補助: next=type{next_piece.get('type', '?')}, nextNext=type{next_next_piece.get('type', '?')}, 最大type={self._fmt_int(max_type)}, 上位type={top_type_text}",
            f"全体スコア統計: {self._summarize_scores(scores)}",
        ]
        if cycle_position is not None:
            lines.append(f"予想サイクル目安: score_history基準で{prediction_cycle_games}ゲーム中{cycle_position}ゲーム目相当（完了済み {cycle_completed}/{prediction_cycle_games}）")
        lines.extend((
            "全体建国統計: " + self._summarize_creation("ロシア建国", russia, total_games),
            "全体建国統計: " + self._summarize_creation("ソ連建国", soviet, total_games),
            "返答ルール: スコア進捗・建国回数・状態を聞かれたら、まず全体統計と直近ウィンドウ統計を使う。ライブ局面の snapshot_score や盤面補助は、今この瞬間の断定ではなく補足としてだけ扱う。数字が無い時だけ不明と言い、推測で回数やスコアを作らない。",
        ))
        return "\n".join(lines)

    def _env_switches(self) -> tuple[str, str]:
        env_file = self.root / ".env"
        if not env_file.is_file():
            return "", ""
        values = {"SOREN91_ENABLED": "", "SOREN91_DAILY_ENABLED": ""}
        try:
            with env_file.open(encoding="utf-8", errors="ignore") as stream:
                for raw in stream:
                    raw = raw.rstrip("\r\n")
                    for key in values:
                        match = re.fullmatch(rf"{key}=([01])[ \t\v\f]*", raw)
                        if match:
                            values[key] = match.group(1)
        except Exception:
            return "", ""
        return values["SOREN91_ENABLED"], values["SOREN91_DAILY_ENABLED"]

    def _ops_context(self, host_mode: str) -> str:
        max_chars = self._bounded_int("COMMENT_OPS_CONTEXT_MAX_CHARS", 1200, minimum=200)
        brief_items = self._bounded_int("COMMENT_OPS_BRIEF_ITEMS", 3, minimum=0)
        canonical = self._read_json(self._path(
            "DOCICH_GAME_SWITCH_CANONICAL_FILE", "/home/ubuntu/docich/run-soren-live/game_switch.json"))
        active = canonical.get("active")
        current_game = str(active.get("game") or "").strip() if isinstance(active, dict) else ""

        lifecycle_dir = self._path("SOREN_GAME_LIFECYCLE_DIR", "tmp/state/game_lifecycle")
        request = self._read_json(lifecycle_dir / "request.json")
        acknowledgement = self._read_json(lifecycle_dir / "ack.json")
        lifecycle_status = ""
        if request.get("request_id") and isinstance(acknowledgement, dict):
            lifecycle_status = str(acknowledgement.get("status") or "")

        lines: list[str] = []
        if host_mode == "soren91":
            lines.append("- いまメイン画面はメリケンAIのソ連ゲーム91(対戦版)")
        else:
            if current_game:
                blurb = GAME_BLURBS.get(current_game, "")
                lines.append("- いまのメイン画面: " + current_game + (f" — {blurb}" if blurb else ""))
            elif lifecycle_status == "stopped":
                lines.append("- いまのメイン画面: ゲーム切替後の準備中(旧ソレンゲームは停止済み)")
            elif lifecycle_status in ("boundary", "stopping"):
                lines.append("- いまのメイン画面: ソレンゲーム(ただいま切替手続き中)")
            else:
                lines.append("- いまのメイン画面: ソレンゲーム(docich管理外で継続中)")
            lines.append(ROUTING_NOTE)
            if lifecycle_status == "boundary":
                lines.append("- 切替手続き: ソ連ゲームの試合境界を確認済み、資源停止は明示stop待ち")
            elif lifecycle_status == "stopping":
                lines.append("- 切替手続き: ソレンゲームの資源を停止している(間もなく次のゲームへ)")
            elif lifecycle_status == "stopped":
                game_state = self._read_json(self._path("SOREN_GAME_STATE_FILE", "game_state.json"))
                score = game_state.get("score")
                count_file = self._path("SOREN_GAME_COUNT_FILE", "game_count.txt")
                try:
                    count = count_file.read_text(encoding="utf-8", errors="ignore").strip()
                except Exception:
                    count = ""
                extra = ""
                if isinstance(score, (int, float)):
                    extra = f" 最終試合score={int(score)}"
                    if count.isdigit():
                        extra += f" / 通算{count}試合"
                lines.append("- 切替完了: ソレンゲームは停止済み(" + extra.strip() + ")。ソ連ゲームの話は過去形で答える")
            elif lifecycle_status == "timeout":
                lines.append("- 切替要求は期限切れ: ソレンゲームを継続")
            elif lifecycle_status == "failed":
                lines.append("- 切替手続きは失敗: 旧ゲームを継続(復旧待ち)")

        work = self._read_json(self._optional_path("CODEX_WORK_OVERLAY_STATE_FILE"))
        if work.get("active"):
            title = str(work.get("title") or "").strip()
            body = str(work.get("body") or "").strip()
            text = " / ".join(part for part in (title, body) if part)[:140]
            if text:
                lines.append("- 画面右上の作業中バナー(視聴者にも見えている): " + text)
        else:
            lines.append("- 画面右上の作業中バナー: 出ていない(いま人手の作業はしていない)")

        improve_file = self._optional_path("IMPROVE_STATE_FILE")
        ab_file = self._path("AB_STATE_FILE", "tmp/state/ab_state.json")
        improve_paused = self._path("SOREN_IMPROVE_PAUSED_FILE", "tmp/state/improve_daemon.paused").exists()
        improve = self._read_json(improve_file)
        improve_status = str(improve.get("status") or "").strip().lower()
        if improve_paused:
            lines.append("- ソ連ゲームの戦略改善: 手動休止中(オペレーターの指示で停止。再開までは動かない)")
        elif improve_status and improve_status not in ("idle", "done", "finished"):
            phase = str(improve.get("phase") or "").strip()[:40]
            lines.append("- 中華AIの戦略改善: 実行中" + (f"({phase})" if phase else ""))
        else:
            lines.append("- 中華AIの戦略改善: 待機中(いまは改善に入っていない)")

        if host_mode != "soren91" and not (current_game and current_game != "sorengame"):
            soren91_enabled, soren91_daily_enabled = self._env_switches()
            if soren91_enabled == "0":
                lines.append("- メリケンAI(ソ連ゲーム91)はいま停止中で登場しない")
            elif soren91_daily_enabled == "1":
                lines.append("- メリケンAIは待機中(戦略改善中の代打と1日1回の枠で登場)")
            else:
                lines.append("- メリケンAIは待機中(戦略改善に入った時だけ登場)")

        ab = self._read_json(ab_file)
        ab_games = ab.get("games_recorded")
        if isinstance(ab_games, int) and ab_games >= 0:
            lines.append(f"- 戦略のA/B比較を実施中: 2つの戦略を1試合ずつ交互に走らせて比較している(記録済み{ab_games}試合)")

        prediction_paused = self._path("PREDICTION_WORKER_PAUSED_FILE", "tmp/state/prediction_worker.paused").exists()
        if prediction_paused:
            lines.append("- チャネルポイント予想(サナエトークン): いまは停止中。動いている前提で案内しない")

        brief_path = self._path("COMMENT_OPS_BRIEF_FILE", "prompts/ops_brief.md")
        brief = []
        try:
            for raw in brief_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                item = raw.strip()
                if not item or item.startswith("#"):
                    continue
                item = item.lstrip("-*・ ").strip()
                if not item:
                    continue
                brief.append(item[:70])
                if len(brief) >= brief_items:
                    break
        except Exception:
            brief = []
        if brief and brief_items:
            lines.append("- 直近の裏側の改修(聞かれたときだけ噛み砕いて話す): " + " / ".join(brief))

        output = "\n".join(lines).strip() or "(運用状況メモなし)"
        return output[:max_chars].rstrip() + "…" if len(output) > max_chars else output

    def _celebration_history_context(self) -> str:
        history_dir = self.env.get("TMP_HISTORY_DIR") or "tmp/history"
        russia_file = self._path(
            "RUSSIA_CREATION_HISTORY_FILE", f"{history_dir}/russia_creation_history.tsv")
        soviet_file = self._path(
            "SOVIET_CREATION_HISTORY_FILE", f"{history_dir}/soviet_creation_history.tsv")
        archive_file = self._path(
            "SOVIET_CREATION_ARCHIVE_FILE", "data/soviet_creation_history.tsv")
        limit = self._bounded_int("COMMENT_CELEBRATION_HISTORY_ITEMS", 12, minimum=1)

        def read_entries(path: Path) -> list[tuple[str, str, str, str]]:
            entries = []
            try:
                rows = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except Exception:
                return entries
            for raw in rows:
                cols = raw.rstrip("\r\n").split("\t")
                if len(cols) < 5:
                    continue
                _iso_ts, local_ts, game_num, score, turns = cols[:5]
                entries.append((local_ts.strip(), game_num.strip(), score.strip(), turns.strip()))
            return entries

        def merge_entries(*paths: Path) -> list[tuple[str, str, str, str]]:
            rows = []
            seen = set()
            for path in paths:
                for row in read_entries(path):
                    key = row[:3]
                    if key in seen:
                        continue
                    seen.add(key)
                    rows.append(row)
            return rows

        def render_block(label: str, rows: list[tuple[str, str, str, str]]) -> str:
            if not rows:
                return f"{label}:\n- まだ履歴なし"
            output = [f"{label}: 累計{len(rows)}回"]
            for local_ts, game_num, score, turns in reversed(rows[-limit:]):
                parts = [local_ts]
                if game_num:
                    parts.append(f"Game#{game_num}")
                if score:
                    parts.append(f"score={score}")
                if turns:
                    parts.append(f"turns={turns}")
                output.append("- " + " / ".join(parts))
            return "\n".join(output)

        return "\n\n".join((
            render_block("ロシア建国", merge_entries(russia_file)),
            render_block("ソ連建国", merge_entries(archive_file, soviet_file)),
        ))

    def _bounded_int(self, name: str, default: int, *, minimum: int) -> int:
        try:
            return max(minimum, int(self.env.get(name) or default))
        except Exception:
            return default
