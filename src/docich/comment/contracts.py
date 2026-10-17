"""Native comment contracts (#829 PR-3e).

Small typed DTOs for the native comment orchestration pipeline. The pipeline
never imports soviet_now; production adapters convert transport-specific
messages into ``CommentMessage`` / ``CommentIntake`` values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:  # pragma: no cover - type-only import
    from docich.llm.contracts import DispatchResult

_NONE = "（なし）"


@dataclass(frozen=True)
class CommentMessage:
    """One normalized inbound comment."""

    author: str
    text: str
    source: str = "fixture"
    stable_id: str = ""
    message_id: str = ""
    # Actual poster identity (Twitch login). May differ from the display name
    # in ``author``. Used to decide whether a comment is the bot's own past
    # statement; display names and in-text self-declarations are NOT trusted.
    author_login: str = ""

    @property
    def line(self) -> str:
        author = self.author.strip() or "unknown"
        return f"{author}: {self.text}"

    @property
    def identity_login(self) -> str:
        return (self.author_login or self.author).strip().lower()


@dataclass(frozen=True)
class CommentIntake:
    """A normalized batch ready for native processing."""

    comments: tuple[CommentMessage, ...]
    source: str = "fixture"
    batch_id: str = ""
    host_mode: str = "main"
    proposal_id: str = ""

    def lines(self) -> list[str]:
        return [item.line for item in self.comments]


@dataclass(frozen=True)
class CommentDiagnosis:
    """Machine-readable facts about one pipeline run."""

    category: str | None
    classification_rows: list[dict] = field(default_factory=list)
    comments: int = 0


@dataclass(frozen=True)
class CommentPipelineResult:
    """Handoff returned by one orchestration attempt."""

    intake: CommentIntake
    diagnosis: CommentDiagnosis
    prompt: str
    reply_text: str = ""
    translated_text: str = ""
    delivery_files: tuple[str, ...] = ()
    dispatch_detail: str = ""
    ok: bool = False


class CommentDeliverySink(Protocol):
    """Fixed interface used by the core; implementations may be compat sinks."""

    def deliver(self, *, intake: CommentIntake, text: str, metadata: dict) -> tuple[str, ...]:
        ...


@dataclass
class FileDeliverySink:
    """Write the generated reply and sidecars to one caller-given directory."""

    root: Path

    def deliver(self, *, intake: CommentIntake, text: str, metadata: dict) -> tuple[str, ...]:
        directory = Path(self.root)
        directory.mkdir(parents=True, exist_ok=True)
        reply_name = f"{intake.batch_id or 'comment'}.reply.txt"
        reply_path = directory / reply_name
        reply_path.write_text(text, encoding="utf-8")
        sidecar_path = directory / f"{intake.batch_id or 'comment'}.meta.json"
        import json

        sidecar_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        ack_path = directory / f"{intake.batch_id or 'comment'}.ack.json"
        ack_path.write_text(
            json.dumps({"batch_id": intake.batch_id, "status": "delivered", "chars": len(text)}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return (str(reply_path), str(sidecar_path), str(ack_path))
