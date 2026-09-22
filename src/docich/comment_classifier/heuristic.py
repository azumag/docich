"""Local comment-category heuristic, normalisation and English safety.

docich owns comment classification (#882 / #829). This is a behaviour-preserving
port of the soviet_now pieces the live pipeline used, taken from soviet_now
``f2c20234``:

- ``broadcast/comment.sh`` ``_classify_comments_heuristic`` (``classify`` and
  ``heuristic_rows`` below),
- ``_normalize_comment_classification_json`` (``normalize_rows``),
- ``_comment_enforce_english_safety`` (``enforce_english_safety``),
- ``lib/comment_bilingual.py`` ``looks_like_english``.

The rules are copied, not redesigned; ``tests/test_comment_classifier_parity.py``
pins byte-identical output against that soviet_now checkout. Change a rule here
only as a deliberate, reviewed classification change.
"""
from __future__ import annotations

import re
from pathlib import Path

# --- lib/comment_bilingual.py (looks_like_english and its constants) --------

URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
JAPANESE_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿]")
CYRILLIC_RE = re.compile(r"[Ѐ-ӿ]")
SHORT_ENGLISH_MESSAGES = {
    # A single natural-language greeting can still be useful, but Twitch
    # emotes and short reaction tokens must not switch an entire batch into
    # bilingual mode.
    "hello",
    "hi",
    "thanks",
    "thank",
    "sorry",
    "welcome",
}
COMMON_ENGLISH_WORDS = {
    "a", "about", "again", "agree", "all", "amazing", "and", "absolutely", "are",
    "awesome", "can", "come", "congrats", "congratulations", "cool", "did", "do",
    "does", "for", "from", "game", "going", "good", "great", "have", "hello",
    "help", "how", "i", "interesting", "is", "it", "just", "like", "love", "me",
    "more", "my", "nice", "of", "on", "play", "played", "please", "really", "say",
    "see", "so", "stream", "that", "the", "this", "to", "today", "victory",
    "want", "what", "when", "where", "why", "with", "watching", "well", "awaits",
    "you", "your",
}


def looks_like_english(text: str) -> bool:
    """Identify natural English without treating Twitch emotes as text."""
    cleaned = URL_RE.sub("", text)
    japanese_count = len(JAPANESE_RE.findall(cleaned))
    cyrillic_count = len(CYRILLIC_RE.findall(cleaned))
    if japanese_count or cyrillic_count:
        return False
    tokens = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", cleaned.lower())
    if not tokens:
        return False
    # Repeated tokens are overwhelmingly emotes, chants, or bot/test noise.
    unique_tokens = set(tokens)
    if len(tokens) >= 2 and len(unique_tokens) == 1:
        return False
    if len(tokens) >= 3 and len(unique_tokens) / len(tokens) < 0.67:
        return False
    if len(tokens) == 1:
        return tokens[0] in SHORT_ENGLISH_MESSAGES
    # At least one common English word is required.
    return any(token in COMMON_ENGLISH_WORDS for token in tokens)


# --- broadcast/comment.sh _classify_comments_heuristic ---------------------

CARD_ACQUIRED_RE = re.compile(r"が\s*(?:【[^】]{1,80}】|\[[^\]]{1,80}\])\s*[^を]{0,360}?を獲得しました")
CARD_MULTI_RE = re.compile(r"が\s*[0-9]+\s*連ガチャで\s*[^を]{0,200}?を獲得しました")
SYSTEM_USERS = frozenset({"wizebot", "nightbot", "streamelements", "streamlabs"})
STREAM_BUG_TERMS = (
    "配信", "映像", "画面", "表示", "出てない", "出ない", "消えた", "止まった",
    "固ま", "フリーズ", "重い", "遅延", "音", "無音", "音楽", "bgm", "音声", "読み上げ", "voicevox",
    "tts", "obs", "overlay", "オーバーレイ", "eventoverlay", "dashboard",
    "ダッシュボード", "show_status", "show-status", "ステータス", "コメント",
    "拾えて", "拾ってない", "反応しない", "返信", "返答", "worker", "ワーカー",
    "chat_worker", "youtube_worker", "audio_worker", "監視", "watchdog", "分類器",
    "classifier", "codex", "コーデックス", "不具合", "バグ", "壊れ", "動いてない",
    "動いてねえ", "動いてねぇ", "動かない", "動かん", "動いていない",
    "不調", "いつもと違う", "record", "レコード",
)


def classify(user: str, comment: str) -> str:
    text = comment.strip()
    lower = text.lower()
    compact = re.sub(r"\s+", "", lower)
    system_user = user.lower() in SYSTEM_USERS
    strategy_hint = re.search(r"戦略|盤面|併合|連鎖|next|nextnext|hold|type\s*[a-z0-9]+|高さ|左|右|置|積|デッドライン|ゲームオーバー|merge|drop|ピース|ロシア|ソ連|建国|おじゃま|相手|順位|盤面タイプ", text, re.I)
    advice_hint = re.search(r"したほうが|した方が|ほうが|方が|ほうがいい|方がいい|よくない|良くない|すべき|べき|狙|優先|避け|やめ|見るべき|考え|意識|改善|閾値|なら|よりも|だめ|ダメ|危ない|注意", text)
    stream_bug_failure = re.search(r"不具合|バグ|壊れ|止ま|固ま|フリーズ|出てない|出ない|出なくなる|でなくなる|消え|拾えてない|拾ってない|反応しない|動いてない|動いてね[えぇ]|動かない|動かん|動いていない|聞こえない|聞こえん|聞こえなくなる|鳴らない|鳴らなくなる|無音|遅延|ずれ|ない|無い|なし|無し|不調|いつもと違う", text, re.I)
    stream_bug_hint = any(term.lower().replace(" ", "") in compact for term in STREAM_BUG_TERMS)
    if system_user and ("raid" in lower or "レイド" in text):
        return "raid"
    if system_user or "配信が終了" in text or "配信が再開" in text or "新しいステータス" in text:
        return "other"
    if stream_bug_hint and stream_bug_failure and not strategy_hint:
        return "stream_bug_report"
    if CARD_ACQUIRED_RE.search(text) or CARD_MULTI_RE.search(text):
        return "card_gacha"
    if "[配信目標達成]" in text:
        return "stream_goal"
    if "bits" in lower or "cheer" in lower:
        return "bits"
    if "sub" in lower or "サブスク" in text:
        return "subscription"
    if "歌" in text or "うた" in text:
        return "sing_request"
    if strategy_hint and advice_hint:
        return "strategy_advice"
    if "?" in text or "？" in text:
        if re.search(r"ゲーム|スコア|盤面|戦略|ロシア|ソ連|建国|何点|何試合", text):
            return "game_question"
        return "general_question"
    if re.search(r"スコア|点|ロシア|ソ連|建国|ウクライナ|カザフ|盤面|落下|テンポ", text):
        return "game_status"
    if re.search(r"したほうが|すべき|狙|置|改善|閾値|ワーカー|返答|コメント", text):
        return "strategy_advice" if re.search(r"戦略|置|狙|スコア|閾値", text) else "comment_advice"
    return "chitchat"


def split_line(raw: str) -> tuple[str, str]:
    if ": " in raw:
        user, comment = raw.split(": ", 1)
        return user, comment
    return "", raw


def read_comment_lines(path: Path) -> list[str]:
    """The batch file contract: one ``user: comment`` per non-blank line."""
    with open(path, encoding="utf-8", errors="ignore") as stream:
        return [line.rstrip("\n") for line in stream if line.strip()]


def heuristic_rows(lines: list[str]) -> list[dict]:
    rows = []
    for idx, raw in enumerate(lines, 1):
        user, comment = split_line(raw)
        is_english = bool(looks_like_english(comment))
        # Card notifications contain ": " inside their own text, so the split
        # can push the acquisition pattern out of `comment`; detect on the
        # whole raw line instead of trusting the split.
        if CARD_ACQUIRED_RE.search(raw) or CARD_MULTI_RE.search(raw):
            category = "card_gacha"
        else:
            category = classify(user, comment)
        rows.append({"index": idx, "user": user, "comment": comment,
                     "category": category, "is_english": is_english})
    if not rows:
        raise ValueError("empty_batch")
    return rows


# --- _normalize_comment_classification_json / _comment_enforce_english_safety

def normalize_rows(rows) -> list:
    if not isinstance(rows, list) or not rows:
        raise ValueError("invalid_classification")
    for item in rows:
        if isinstance(item, dict) and item.get("category") == "short_reaction":
            item["category"] = "chitchat"
        if isinstance(item, dict):
            value = item.get("is_english", False)
            if not isinstance(value, bool):
                value = str(value).strip().lower() in {"1", "true", "yes", "y"}
            item["is_english"] = value
    return rows


_NOISE_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)


def _obvious_non_english_noise(text: str) -> bool:
    text = _NOISE_URL_RE.sub(" ", text).strip()
    if JAPANESE_RE.search(text) or CYRILLIC_RE.search(text):
        return True
    tokens = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", text.lower())
    if not tokens:
        return True
    if len(tokens) >= 2 and len(set(tokens)) == 1:
        return True
    if len(tokens) >= 3 and len(set(tokens)) / len(tokens) < 0.67:
        return True
    return False


def enforce_english_safety(rows, source: list[str]) -> list:
    """The source batch, not classifier-echoed fields, is authoritative."""
    if not isinstance(rows, list) or len(rows) != len(source):
        raise ValueError("invalid_classification")
    for position, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError("invalid_classification")
        try:
            index = int(row.get("index"))
        except Exception as exc:
            raise ValueError("invalid_classification") from exc
        if index != position:
            raise ValueError("invalid_classification")
    for position, row in enumerate(rows, 1):
        user, comment = split_line(source[position - 1])
        row["index"] = position
        row["user"] = user
        row["comment"] = comment
        if row.get("is_english") is True and _obvious_non_english_noise(comment):
            row["is_english"] = False
    return rows


def baseline(lines: list[str]) -> list[dict]:
    """The canonical local classification: heuristic -> normalise -> safety."""
    return enforce_english_safety(normalize_rows(heuristic_rows(lines)), lines)
