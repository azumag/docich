"""Game-independent contexts and the game-context injection boundary (#829 PR-3d).

The comment core can build conversation/history context without importing a
game implementation. Game-specific state is supplied through
``GameContextProvider`` by the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
import glob
import os
from pathlib import Path
import re
import time
from typing import Protocol, runtime_checkable


NONE = "（なし）"


@dataclass(frozen=True)
class GameContext:
    """The complete game-owned portion of a comment prompt."""

    game_state_context: str = ""
    comment_ops_context: str = ""
    celebration_history_context: str = ""


@runtime_checkable
class GameContextProvider(Protocol):
    """Inject game-owned prompt context without coupling the core to a game."""

    def build(self, *, host_mode: str) -> GameContext:
        """Return the game, operational, and celebration context for a mode."""


def normalize_host_mode(mode: str | None) -> str:
    return "soren91" if mode == "soren91" else "main"


def build_game_context(provider: GameContextProvider, *, host_mode: str) -> GameContext:
    """Call the selected provider once for the normalized host mode."""
    return provider.build(host_mode=normalize_host_mode(host_mode))


def format_batch_context(text: str) -> str:
    """Format each comment with its immediate neighbours (legacy bytes)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    items: list[tuple[str, str, str]] = []
    for raw in (line.strip() for line in text.splitlines()):
        if not raw:
            continue
        user, message = raw.split(": ", 1) if ": " in raw else ("不明", raw)
        items.append((user.strip(), message.strip(), raw))

    output: list[str] = []
    for index, (user, message, _raw) in enumerate(items, start=1):
        previous = items[index - 2][2] if index > 1 else NONE
        following = items[index][2] if index < len(items) else NONE
        same_user = "あり" if index > 1 and items[index - 2][0] == user else "なし"
        output.extend((
            f"[{index}] {user}: {message}",
            f"  直前: {previous}",
            f"  直後: {following}",
            f"  直前が同一ユーザー: {same_user}",
            "",
        ))
    return "".join(line + "\n" for line in output)


def _mode_suffix(mode: str) -> str:
    return f"_{normalize_host_mode(mode)}.txt"


def _matches_history_mode(path: str, mode: str) -> bool:
    name = os.path.basename(path)
    selected_suffix = _mode_suffix(mode)
    if name.endswith(("_main.txt", "_soren91.txt")):
        return name.endswith(selected_suffix)
    return normalize_host_mode(mode) == "main"


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _recent_excerpt(path: str, item_limit: int) -> str:
    try:
        with open(path, encoding="utf-8", errors="ignore") as stream:
            raw = stream.read()
    except Exception:
        return ""
    kept = []
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.match(r"^[✗✕×].*\b(read|glob|grep|ls|edit|write|multiedit)\b.*\bfailed\b", line, re.I):
            continue
        if re.match(r"^[✱→►▸]\s*(read|glob|grep|ls|edit|write|multiedit)\b", line, re.I):
            continue
        if re.match(r"^(read|glob|grep|ls|edit|write|multiedit)\b", line, re.I):
            continue
        if re.match(r"^(error|warning)\s*:", line, re.I):
            continue
        if re.search(
            r"file not found:|no such file or directory|permission denied|invalid arguments|"
            r"could not find oldstring|no changes to apply",
            line,
            re.I,
        ):
            continue
        kept.append(raw_line)
    text = _collapse("\n".join(kept))
    if len(text) > item_limit:
        text = text[:item_limit].rstrip() + "..."
    return text


def recent_spoken_context(
    history_dir: str | os.PathLike[str],
    *,
    history_limit: int = 10,
    total_limit: int = 1200,
    item_limit: int = 240,
    current_file: str = "",
    mode: str = "main",
    include_current: bool = False,
) -> str:
    """Build bounded same-mode recent speech context from recorded replies."""
    history_limit = max(0, int(history_limit))
    total_limit = max(200, int(total_limit))
    item_limit = max(80, int(item_limit))
    mode = normalize_host_mode(mode)

    entries: list[tuple[str, float, str]] = []
    seen: set[str] = set()
    if include_current and current_file and os.path.isfile(current_file):
        entries.append(("再生中", os.path.getmtime(current_file), current_file))
        seen.add(os.path.realpath(current_file))

    history_files = sorted(
        path for path in glob.glob(os.path.join(os.fspath(history_dir), "*.txt"))
        if _matches_history_mode(path, mode)
    )
    if history_limit > 0:
        history_files = history_files[-history_limit:]
    for path in reversed(history_files):
        real_path = os.path.realpath(path)
        if real_path in seen:
            continue
        entries.append(("", os.path.getmtime(path), path))
        seen.add(real_path)

    lines: list[str] = []
    used = 0
    for tag, timestamp, path in entries:
        text = _recent_excerpt(path, item_limit)
        if not text:
            continue
        stamp = time.strftime("%H:%M", time.localtime(timestamp))
        line = f"[{tag} {stamp}] {text}" if tag else f"[{stamp}] {text}"
        if used and used + len(line) + 1 > total_limit:
            break
        if not used and len(line) > total_limit:
            keep = max(40, total_limit - 16)
            line = line[:keep].rstrip() + "..."
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines) if lines else NONE


def _followup_history_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="ignore") as stream:
            raw = stream.read()
    except Exception:
        return ""
    kept = []
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.match(r"^[✗✕×].*\b(read|glob|grep|ls|edit|write|multiedit)\b.*\bfailed\b", line, re.I):
            continue
        if re.match(r"^[✱→►▸]\s*(read|glob|grep|ls|edit|write|multiedit)\b", line, re.I):
            continue
        if re.match(r"^(read|glob|grep|ls|edit|write|multiedit)\b", line, re.I):
            continue
        if re.match(r"^(error|warning)\s*:", line, re.I):
            continue
        kept.append(raw_line)
    return _collapse("\n".join(kept))


def _parse_comment_line(line: str) -> tuple[str, str]:
    match = re.match(r"([^:]{1,40}):\s*(.+)$", line)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return "", line.strip()


def _is_short_followup(text: str) -> bool:
    normalized = _collapse(text)
    if not normalized or re.search(r"[?？]", normalized):
        return False
    return bool(re.fullmatch(
        r"(?:なるほど|へえ|へぇ|ほう|そうなんだ|そうなんですね|そういうこと|"
        r"たしかに|確かに|それな|しらなかった|知らなかった|すごい|助かる|"
        r"面白い|おもしろい|わかる|[wWｗＷ]+|笑|草)[。！!、…ー\s]*",
        normalized,
    ))


def _extract_followup_terms(text: str) -> list[str]:
    normalized = _collapse(text)
    patterns = (
        r"[「『]([^」』]{1,24})[」』]",
        r"([^\s、。！？]{2,24})(?:なんだ|なんですね|ってこと|って|とは)",
        r"([A-Za-z][A-Za-z0-9_+\-]{1,24})",
        r"([ァ-ヶー]{2,24})",
    )
    stop = {"それ", "これ", "あれ", "さっき", "今の", "その話", "この話", "こと", "感じ"}
    terms = []
    for pattern in patterns:
        for match in re.finditer(pattern, normalized):
            term = _collapse(match.group(1))
            if len(term) >= 2 and term not in stop:
                terms.append(term)
    if not terms and len(normalized) <= 20:
        terms.append(normalized[:20])
    seen: set[str] = set()
    unique = []
    for term in terms:
        key = term.lower()
        if key not in seen:
            seen.add(key)
            unique.append(term)
    return unique[:4]


def followup_hints(
    batch_file: str | os.PathLike[str],
    history_dir: str | os.PathLike[str],
    *,
    history_limit: int = 10,
    current_file: str = "",
    mode: str = "main",
    include_current: bool = False,
) -> str:
    """Build short acknowledgment hints using only the same mode's replies."""
    batch_file = os.fspath(batch_file)
    history_dir = os.fspath(history_dir)
    mode = normalize_host_mode(mode)
    try:
        history_limit = int(history_limit)
    except Exception:
        history_limit = 10

    if not os.path.isfile(batch_file):
        return NONE

    recent_texts: list[str] = []
    seen_paths: set[str] = set()
    if include_current and current_file and os.path.isfile(current_file):
        seen_paths.add(os.path.realpath(current_file))
        text = _followup_history_text(current_file)
        if text:
            recent_texts.append(text)

    history_files = sorted(
        path for path in glob.glob(os.path.join(history_dir, "*.txt"))
        if _matches_history_mode(path, mode)
    )
    if history_limit > 0:
        history_files = history_files[-history_limit:]
    for path in reversed(history_files):
        real_path = os.path.realpath(path)
        if real_path in seen_paths:
            continue
        seen_paths.add(real_path)
        text = _followup_history_text(path)
        if text:
            recent_texts.append(text)

    recent_blob = "\n".join(recent_texts[:6])
    recent_blob_lower = recent_blob.lower()
    try:
        with open(batch_file, encoding="utf-8", errors="ignore") as stream:
            batch_lines = [line.strip() for line in stream if line.strip()]
    except Exception:
        return NONE

    hints: list[str] = []
    seen_hints: set[str] = set()
    for line in batch_lines:
        user, text = _parse_comment_line(line)
        if not _is_short_followup(text):
            continue
        matched_term = ""
        for term in _extract_followup_terms(text):
            if term in recent_blob or term.lower() in recent_blob_lower:
                matched_term = term
                break
        if matched_term:
            hint = (
                f"- {user or 'リスナー'}: 「{matched_term}」は直近返答で説明済み。"
                "相づちには自然に短く返し、必要な補足は一点まで。"
                "明示的な再説明要求には同じ事実を説明し直してよい"
            )
        else:
            hint = (
                f"- {user or 'リスナー'}: 単独の相づち・反応。直前の文脈につなげて自然に短く返す。"
                "感情や背景を創作せず、不要な説明や会話を続けるだけの質問は足さない"
            )
        if hint in seen_hints:
            continue
        seen_hints.add(hint)
        hints.append(hint)
        if len(hints) >= 4:
            break
    return "\n".join(hints) if hints else NONE


def advice_context_tail(advice_file: str | os.PathLike[str], max_lines: int = 40) -> str:
    """Return the latest non-empty advice rows, preserving their original text."""
    try:
        lines = Path(advice_file).read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return NONE
    try:
        max_lines = int(max_lines)
    except Exception:
        return NONE
    if max_lines < 0:
        lines = lines[:max_lines]
    elif max_lines == 0:
        lines = []
    else:
        lines = lines[-max_lines:]
    kept = [
        line for line in lines
        if line.strip() and not re.fullmatch(r"\s*-?\s*（なし）\s*", line)
    ]
    return "\n".join(kept) if kept else NONE


@dataclass(frozen=True)
class StructuredAdvice:
    """A classified advice row extracted from one comment batch."""

    kind: str
    mode: str
    item: str

    def as_legacy_row(self) -> str:
        return f"{self.kind}\t{self.mode}\t{self.item}"


def extract_structured_advice(
    batch_file: str | os.PathLike[str], *, fallback_mode: str = "main"
) -> list[StructuredAdvice]:
    """Extract bounded strategy/comment/system advice from a batch file."""
    try:
        with open(batch_file, encoding="utf-8", errors="ignore") as stream:
            lines = [line.strip() for line in stream if line.strip()]
    except Exception:
        return []

    strategy_terms = (
        "戦略", "盤面", "併合", "連鎖", "next", "nextnext", "next-next", "hold",
        "type", "高さ", "左", "右", "上に", "下に", "置く", "置き", "積む",
        "積み", "デッドライン", "ゲームオーバー", "merge", "sandwich", "サンドイッチ",
        "おじゃま", "garbage", "rank", "順位", "ピース", "盤面タイプ", "drop", "gauge",
    )
    comment_terms = (
        "コメント", "コメント返し", "返答", "返信", "読み上げ", "しゃべり", "話し方",
        "口調", "文量", "長め", "短め", "テンポ", "語尾", "言い回し", "実況",
        "試合中コメント", "順位コメント", "戦略説明", "説明文", "カードガチャ",
        "カード説明", "発音", "読み", "ラジオ", "ニュース", "ニーサ", "nisa",
    )
    system_terms = (
        "仕組み", "システム", "改善ループ", "自動改善", "戦略改善", "次のループ",
        "codex", "コーデックス", "拾える", "拾って", "取り入れ", "反映", "修正依頼",
        "意見", "要望", "監視", "watchdog", "ワーカー", "worker", "daemon",
        "runtime", "ランタイム", "show-status", "show_status", "dashboard",
        "ダッシュボード", "overlay", "オーバーレイ", "obs", "分類器", "classifier",
        "コメント分類", "ログ", "レポート", "恒久対応", "運用",
    )
    directive_terms = (
        "して", "しろ", "すべき", "したほうがいい", "した方がいい", "やめて",
        "避けて", "見るべき", "見て", "考えて", "意識して", "優先", "禁止",
        "改善して", "直して", "変えて", "分けて", "保存して", "参照して",
        "読んで", "増やして", "減らして", "別にして", "統一して", "変換して",
        "長くして", "短くして", "伸ばして", "抑えて", "残して", "今まで通り",
        "ほうがいい", "方がいい", "べき", "いかん", "だめ", "ダメ", "するな",
        "しないで", "するといかん", "するとだめ", "よくない", "まずい", "やばい",
        "危ない", "注意", "気をつけ", "取り入れ", "反映", "拾える", "拾って",
        "残して", "メモして",
    )
    noise_terms = ("レイド", "nightbot", "show-status", "show_status", "dashboard", "blackhole",
                   "ffmpeg", "url", "http://", "https://")
    main_terms = ("中華ai", "strategy.py", "[main]", "[soren]")
    soren91_terms = ("メリケン", "メリケンai", "soren91", "[soren91]", "対戦版", "91人",
                     "おじゃま", "試合", "盤面タイプ")

    def normalized_terms(terms: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(term.lower().replace(" ", "") for term in terms)

    strategy_terms_norm = normalized_terms(strategy_terms)
    comment_terms_norm = normalized_terms(comment_terms)
    system_terms_norm = normalized_terms(system_terms)
    noise_terms_norm = normalized_terms(noise_terms)
    main_terms_norm = normalized_terms(main_terms)
    soren91_terms_norm = normalized_terms(soren91_terms)

    def parse_line(line: str) -> tuple[str, str]:
        match = re.match(r"([^:]{1,40}):\s*(.+)$", line)
        return (match.group(1).strip(), match.group(2).strip()) if match else ("", line)

    def clean_body(text: str) -> str:
        body = _collapse(text)
        return body[1:-1].strip() if body.startswith("[") and body.endswith("]") else body

    def looks_like_internal_error(raw: str) -> bool:
        patterns = (
            r"申し訳(?:ありません|ございません|ない).*(?:エラーメッセージ|提供|ユーザーメッセージ|指示|タスク|情報|スクリーンショット)",
            r"(?:エラーメッセージ|ユーザーメッセージ|具体的な指示|明確な指示|具体的なタスク).*(?:提供されてい|見当たりません|ありません|ない|不足)",
            r"(?:何も言えません|語ることはできません|控えておくべき|確認させてください|どうすればよい|何を.*すれば)",
            r"(?:tool_call|tool_result|assistant_response|System Context|permission denied|no such file or directory|file not found|read failed|edit failed|write failed)",
            r"(?:unexpected token|syntaxerror|referenceerror|typeerror|could not find oldstring|no changes to apply|rejected permission)",
            r"(?:invalid bearer token|authentication_error|api error|request_id|invalid token|not logged in|please run /login|rate limit|too many requests|429\b|quota|usage limit)",
        )
        return any(re.search(pattern, raw, re.I) for pattern in patterns)

    def has_any(normalized: str, terms: tuple[str, ...]) -> bool:
        return any(term in normalized for term in terms)

    def has_directive(raw: str) -> bool:
        return any(term in raw for term in directive_terms)

    def looks_like_comment_advice(raw: str) -> bool:
        if len(raw) < 5:
            return False
        normalized = raw.lower().replace(" ", "")
        has_comment = has_any(normalized, comment_terms_norm)
        if has_comment and (has_directive(raw) or "改善" in raw or "今まで通り" in raw):
            return True
        if ("nisa" in normalized or "ニーサ" in raw) and any(
            term in raw for term in ("変換", "読み", "発音", "呼び")
        ):
            return True
        if ("試合中コメント" in raw or "順位コメント" in raw) and any(
            term in raw for term in ("1回", "一回", "減ら", "抑え", "禁止")
        ):
            return True
        return False

    def looks_like_system_advice(raw: str) -> bool:
        if len(raw) < 6:
            return False
        normalized = raw.lower().replace(" ", "")
        if not has_any(normalized, system_terms_norm):
            return False
        return has_directive(raw) or any(term in raw for term in ("改善", "修正", "意見", "要望", "依頼", "問題", "不具合"))

    def looks_like_strategy_advice(raw: str) -> bool:
        if len(raw) < 6:
            return False
        normalized = raw.lower().replace(" ", "")
        if looks_like_system_advice(raw) and not has_any(normalized, strategy_terms_norm):
            return False
        if looks_like_comment_advice(raw):
            return False
        has_game = has_any(normalized, strategy_terms_norm) or bool(re.search(r"type\s*[a-z0-9]+", raw, re.I))
        noisy = has_any(normalized, noise_terms_norm)
        if noisy and not has_game:
            return False
        if has_game and has_directive(raw):
            return True
        if "改善" in raw and has_game:
            return True
        return raw.startswith("[") and raw.endswith("]") and has_game

    def detect_mode(raw: str) -> str:
        normalized = raw.lower().replace(" ", "")
        if normalized.startswith(("[main]", "[soren]")):
            return "main"
        if normalized.startswith("[soren91]"):
            return "soren91"
        has_main = has_any(normalized, main_terms_norm)
        has_soren91 = has_any(normalized, soren91_terms_norm)
        if has_soren91 and not has_main:
            return "soren91"
        if has_main and not has_soren91:
            return "main"
        return fallback_mode

    result: list[StructuredAdvice] = []
    seen: set[tuple[str, str, str]] = set()
    for line in lines:
        user, text = parse_line(line)
        body = clean_body(text)
        if not body or looks_like_internal_error(body):
            continue
        kind = ""
        mode = "-"
        if looks_like_system_advice(body):
            kind = "codex"
        elif looks_like_comment_advice(body):
            kind = "comment"
        elif looks_like_strategy_advice(body):
            kind = "strategy"
            mode = detect_mode(body)
        if not kind:
            continue
        item = f"{user}: {body}" if user else body
        if len(item) > 220:
            item = item[:217].rstrip() + "..."
        key = (kind, mode, item)
        if key in seen:
            continue
        seen.add(key)
        result.append(StructuredAdvice(kind, mode, item))
    return result


def past_topics_block(path: str | os.PathLike[str], limit: int = 12) -> str:
    """Summarize recent radio topics without exposing their original payload."""
    try:
        limit = max(1, int(limit))
    except Exception:
        limit = 12
    try:
        rows = Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        rows = []
    corner_labels = {
        "strategy": "戦略変更の解説をしました",
        "rollback": "戦略の失敗分析をしました",
        "news": "ニュース考察をしました",
        "jiji": "時事ニュースの考察をしました",
        "theme": "脱線テーマの雑談をしました",
        "soviet": "ソ連史や社会の話をしました",
        "market": "市場や景気の話をしました",
        "weather": "天気の話をしました",
        "fortune": "占いコーナーをしました",
        "dinner": "夕飯の話をしました",
        "world_dinner": "世界の食卓の話をしました",
        "night_snack": "夜食の話をしました",
        "deals": "節約や暮らしの話をしました",
        "survival": "サバイバル知識の話をしました",
        "rakugo": "創作落語をしました",
        "bluegrass": "音楽雑談をしました",
        "soviet_lifehack": "生活の小ネタを話しました",
        "breakfast": "世界の朝食を紹介しました",
        "redefine": "言葉の再定義をしました",
        "devil_dict": "悪魔辞典の話をしました",
    }
    entries = []
    for raw in rows:
        match = re.match(r"^\[(\d{2}:\d{2})\] Game#\d+(?: [^ ]+)? \[([^\]]+)\]:\s*(.*)$", raw)
        if not match:
            continue
        time_text, corner, _payload = match.groups()
        summary = corner_labels.get(corner, "別の題材を扱いました")
        entries.append(f"- {time_text} [{corner}] {summary}")
    entries = entries[-limit:]
    if not entries:
        return "まだ過去のトークはありません。自由に話してください。"
    return "\n".join((
        "直近ラジオの重複回避メモ:",
        *entries,
        "- 同じ固有名詞の読み直しではなく、切り口を変えること",
        "- 政治、戦争、歴史、人名そのものを避ける必要はありません",
    ))
