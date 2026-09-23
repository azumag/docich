"""Narrow adapters from paper notifications to existing Soren viewer queues."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from ..config import ConfigError, GlobalConfig, load_game
from ..overlay_queue import append_event, regenerate_overlay
from .narration_style import strip_leading_preamble


class SorenOutputError(RuntimeError):
    """Raised when an existing Soren viewer-output queue cannot accept output."""


# Narration personas: the stream's two AI personalities take turns hosting
# the PAPER corner. Chuka (中華AI) speaks in the worker's default voice;
# Meriken (メリケンAI) uses the Soren91 voice. Exactly one persona hosts an
# entire corner run (decided once, effectively at random, when the corner
# starts) rather than switching mid-corner segment to segment; a listener
# hearing both voices alternate within the same 10/30-minute corner read as
# a bug, not a feature. The pick is a deterministic hash of the corner's
# fixed identity (delivery scope + date, i.e. every event_id up to and
# including the date segment) rather than the full per-segment delivery key:
# stable across every segment and retry within one corner, but still varies
# across different corners (different dates, or a fresh manual-run uuid).
PAPER_PERSONA_CHUKA = "chuka"
PAPER_PERSONA_MERIKEN = "meriken"
PAPER_PERSONAS = (PAPER_PERSONA_CHUKA, PAPER_PERSONA_MERIKEN)

_MERIKEN_VOICE_FALLBACK = "46"

# Matches both the daily corner's fixed scope ("paper-corner:...") and any
# operator/manual test run's per-invocation scope ("paper-corner-manual-
# <uuid12hex>:...", "paper-corner-operator-...", etc). Both need the persona
# treatment below (see pick_paper_persona/_paper_corner_speech_text). A
# scope-prefix split is used instead of a regex tied to the exact
# manual-scope shape (e.g. the uuid hex length), so a future scope variant
# is covered by construction rather than needing this file updated in
# lockstep.
def _is_paper_corner_delivery(event_id: str) -> bool:
    scope = str(event_id or "").split(":", 1)[0]
    return scope == "paper-corner" or scope.startswith("paper-corner-")


def _corner_identity(event_id: str) -> str:
    """The part of a delivery key shared by every segment of one corner run.

    ``event_id`` is ``{delivery_scope}:{date}:{key}`` (e.g.
    ``paper-corner:2026-09-18:script:3`` or
    ``paper-corner-manual-<uuid>:2026-09-18:opening``); the first two
    colon-separated parts (scope + date) identify one corner run and are
    shared by every segment/retry inside it, unlike ``key`` itself.
    """
    parts = str(event_id or "").split(":")
    return ":".join(parts[:2]) if len(parts) >= 2 else str(event_id or "")


def pick_paper_persona(event_id: str) -> str:
    """Deterministically pick the one persona hosting this corner run."""
    digest = hashlib.sha256(_corner_identity(event_id).encode("utf-8")).hexdigest()
    return PAPER_PERSONAS[int(digest, 16) % len(PAPER_PERSONAS)]


def _meriken_speaker_id(soren_root: Path) -> str:
    """Meriken voice id from the Soren runtime env (production-tuned value)."""
    try:
        from .. import webui

        raw = (webui._read_dotenv_dict(soren_root).get("SOREN91_VOICEVOX_SPEAKER", "") or "").strip()
        if re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", raw or ""):
            return str(raw)
    except Exception:
        pass
    return _MERIKEN_VOICE_FALLBACK


_PAPER_CORNER_INTRO = "PAPER・暗号資産の模擬売買コーナーです。"
_PAPER_CORNER_CHATTER = (
    "ここからは数字だけでなく、BOTが何を待っているのかも見ていきます。売買回数を増やすこと自体が目的ではなく、条件がそろわない時に何もしないのも立派な判断です。チャートが動いていても、勢い・平均からの乖離・資金上限などが噛み合わなければ見送ります。むしろ、何もしていない時間に理由が説明できるかがBOTの健全性を見るポイントです。",
    "短い値動きはかなり賑やかに見えますが、数秒の上下だけで飛びつくと往復ビンタになりやすいところです。画面のローソク足では細かな揺れを楽しみつつ、売買ロジック側は別の時間軸で条件を確認します。表示が細かくなっても、BOTまでせっかちにするわけではありません。この距離感は意外と大事です。",
    "BTC/JPYのような大型ペアでも、いつも取引するとは限りません。今のBOTは銘柄の知名度で買うのではなく、一定以上のモメンタムや平均からの乖離などを待ちます。大きな銘柄ほど値動きが比較的落ち着く時間もあり、条件未達なら普通に素通りします。『有名だから買う』をやらないのは、機械らしいところですね。",
    "PAPERでも手数料やスリッページを無視すると、細かい利益を積み上げる戦略ほど成績が実態より良く見えてしまいます。今回から模擬約定には取引コストを織り込み、画面に出る損益もコスト込みで見る前提にします。数十円の差でも、回数が増えれば効いてくるので、ここは地味ですが重要な現実寄せです。",
    "途中経過で利益が出ていても、一回の当たりだけでは戦略が良くなったとは言えません。逆に含み損があっても、決めた損切りや保有時間のルールの中なら即失敗とも限りません。いま見たいのは、エントリーした理由と、その後の値動きが噛み合っているかです。結果だけで後付けの物語を作らないように見ていきます。",
    "そろそろ終盤です。最後は勝った負けただけでなく、見送りが多すぎなかったか、特定銘柄へ偏らなかったか、コストを払っても残る優位性があったかを次の改善材料にします。何も起きなかった時間もデータです。派手な売買がなくても、次に変えるべき点が一つ見つかれば、この30分には十分意味があります。",
)


def _paper_corner_speech_text(text: str, event_id: str) -> str:
    """Keep the programme intro one-shot and add bounded between-segment talk.

    Paper-corner reports are durable and delivered every five minutes.  The
    original report text intentionally remains unchanged for the overlay; only
    speech is adapted here.  This keeps retry/dedupe semantics intact while
    preventing the programme title from being spoken on every periodic report.
    """
    body = str(text).strip()
    key = str(event_id or "")
    if not _is_paper_corner_delivery(key):
        return body
    parts = key.split(":")
    suffix = parts[-1]
    if suffix != "opening" and body.startswith(_PAPER_CORNER_INTRO):
        body = body[len(_PAPER_CORNER_INTRO):].lstrip()
    # Preamble removal is generation-side too (corner_script.parse_*), but a
    # durable/replayed text or a model that ignores the prompt must still not
    # open with 「結論からお伝えしますと、」 on air.
    body = strip_leading_preamble(body)
    # Only the direct timed report ids are paper-corner:<date>:<slot>.
    # Script ids are paper-corner:<date>:script:<n> and already contain
    # substantial narration, so do not append the periodic chatter to them.
    if len(parts) == 3 and suffix.isdigit():
        chatter = _PAPER_CORNER_CHATTER[int(suffix) % len(_PAPER_CORNER_CHATTER)]
        body = f"{body} {chatter}".strip()
    return body


def resolve_soren_root(g: GlobalConfig) -> Path:
    raw = (g.webui.soren_root or "").strip()
    if raw:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = g.repo_root / candidate
        return candidate.resolve()
    try:
        game = load_game(g, "sorengame")
        soren_raw = game.raw.get("soren", {}) if isinstance(game.raw, dict) else {}
        runtime_raw = soren_raw.get("root", "") if isinstance(soren_raw, dict) else ""
        if isinstance(runtime_raw, str) and runtime_raw.strip():
            runtime = Path(runtime_raw.strip()).expanduser()
            if not runtime.is_absolute():
                runtime = g.repo_root / runtime
            runtime = runtime.resolve()
            if runtime.is_dir():
                return runtime
    except ConfigError:
        pass
    candidate = g.repo_root / "games" / "soviet_now"
    if (candidate / "eloop_lib.sh").is_file():
        return candidate.resolve()
    cwd = Path.cwd()
    if (cwd / "eloop_lib.sh").is_file():
        return cwd.resolve()
    return candidate.resolve()


def send_overlay(g: GlobalConfig, payload: dict[str, object]) -> None:
    root = resolve_soren_root(g)
    try:
        # Keep queue mutation and HTML regeneration separately observable. If a
        # process dies after the queue write, exact-event dedupe makes retry safe.
        append_event(root, payload, strict=True, regenerate=False)
        if not regenerate_overlay(root):
            raise SorenOutputError("Soren overlay regeneration failed")
    except SorenOutputError:
        raise
    except Exception as exc:
        raise SorenOutputError("Soren overlay queue delivery failed") from exc


def enqueue_speech(g: GlobalConfig, text: str, *, event_id: str = "") -> None:
    # Reuse the same production Soren comment-audio queue used by Web UI.  The
    # paper event id is a durable sink-side dedupe key so a crash after enqueue
    # but before notification ACK cannot cause a later replay.
    speech_text = _paper_corner_speech_text(text, event_id)
    root = resolve_soren_root(g)
    speaker = ""
    if pick_paper_persona(event_id) == PAPER_PERSONA_MERIKEN and _is_paper_corner_delivery(event_id):
        speaker = _meriken_speaker_id(root)
    try:
        from .. import webui
        result = webui._enqueue_audio_text(
            root, speech_text, "crypto_paper", speaker=speaker, delivery_key=event_id
        )
    except Exception as exc:
        raise SorenOutputError("Soren audio queue delivery failed") from exc
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise SorenOutputError("Soren audio queue rejected notification")


def enqueue_chat(g: GlobalConfig, text: str, *, source: str = "docich") -> None:
    """Post one Twitch chat line via the Soren outbound chat queue.

    Same-VM only: runs ``enqueue_chat_message`` from lib/outbound_queue.sh
    with the Soren root as cwd so its relative queue/pause paths resolve.
    Chat-paused or duplicate-suppressed posts are sink-side no-ops (rc=0).
    """
    import subprocess

    if not text or not text.strip():
        raise SorenOutputError("empty chat text")
    root = resolve_soren_root(g)
    try:
        proc = subprocess.run(
            ["bash", "-c", 'source lib/outbound_queue.sh && enqueue_chat_message "$0" "$1"',
             text, source],
            cwd=str(root),
            text=True,
            capture_output=True,
            timeout=30.0,
            check=False,
        )
    except Exception as exc:
        raise SorenOutputError("Soren chat queue delivery failed") from exc
    if proc.returncode != 0:
        raise SorenOutputError(
            f"Soren chat queue rejected notification: {(proc.stderr or '').strip()[:200]}"
        )


def enqueue_audio_text(
    g: GlobalConfig, text: str, *, context: str = "soren91", speaker: str = "",
    runtime_fence: dict | None = None,
) -> None:
    """Enqueue one TTS line into the Soren audio queue with an explicit speaker.

    Used so the Soren91 (Meriken) corner speaks in the Meriken voice rather
    than the default host speaker.  Same-VM only; paused/duplicate-suppressed
    enqueues are sink-side no-ops (rc=0).
    """
    import subprocess

    if not text or not text.strip():
        raise SorenOutputError("empty audio text")
    root = resolve_soren_root(g)
    command = ["bash", "-c",
               'source lib/outbound_queue.sh && enqueue_audio_text "$0" "$1" "$2"',
               text, context, str(speaker)]
    if runtime_fence is not None:
        command[2] = 'source lib/outbound_queue.sh && enqueue_audio_text "$0" "$1" "$2" "$3"'
        command.append(json.dumps(runtime_fence, separators=(",", ":")))
    try:
        proc = subprocess.run(
            command,
            cwd=str(root),
            text=True,
            capture_output=True,
            timeout=30.0,
            check=False,
        )
    except Exception as exc:
        raise SorenOutputError("Soren audio queue delivery failed") from exc
    if proc.returncode != 0:
        raise SorenOutputError(
            f"Soren audio queue rejected notification: {(proc.stderr or '').strip()[:200]}"
        )
