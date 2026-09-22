"""Typed client facade for the common semantic decision transport."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import time
from typing import Any, Callable, Mapping

from .contracts import DecisionRequest, RouteProfile, TransportResult
from .metrics import append_event, build_event
from .routes import resolve_route, resolve_timeout_ms
from .validator import validate_response


@dataclass(frozen=True)
class DecisionResult:
    """Sanitized decision result and its secret-free metric event."""

    status: str
    attempted: bool
    data: Mapping[str, Any] | None
    metric: Mapping[str, Any]
    retry_after: int = 0


class SemanticDecisionClient:
    """Resolve one fixed route and perform one bounded decision request."""

    def __init__(
        self,
        *,
        env: Mapping[str, str] | None = None,
        transport: Callable[..., TransportResult] | None = None,
        clock: Callable[[], float] = time.monotonic,
        metrics_path: Path | None = None,
    ) -> None:
        self.env = os.environ if env is None else dict(env)
        self.profile: RouteProfile = resolve_route(self.env)
        self.timeout_ms = resolve_timeout_ms(self.env, self.profile)
        if transport is None:
            from .transport import request_once

            transport = request_once
        self.transport = transport
        self.clock = clock
        self.metrics_path = metrics_path

    def decide(self, request: DecisionRequest) -> DecisionResult:
        started = self.clock()
        result: TransportResult
        if request.model != self.profile.requested_model:
            result = TransportResult("invalid_config", attempted=False)
        else:
            credential = self.env.get(self.profile.credential_env, "")
            if not credential:
                result = TransportResult("missing_key", attempted=False)
            else:
                try:
                    result = self.transport(
                        request.payload,
                        self.profile,
                        credential,
                        self.timeout_ms / 1000,
                    )
                except Exception:
                    # Provider errors never escape with raw details or stop the
                    # caller's fallback path.
                    result = TransportResult("network_error", attempted=True)

        data: Mapping[str, Any] | None = None
        status = result.status
        if status == "ok":
            try:
                data = validate_response(result.data or {}, request)
            except Exception:
                status = "invalid_response"
                data = None
        elapsed_ms = max(0.0, (self.clock() - started) * 1000)
        metric = build_event(
            request,
            self.profile,
            status=status,
            latency_ms=elapsed_ms,
            resolved_model=data.get("model") if data else None,
            usage=data.get("usage") if data else None,
            fallback_reason=status if status != "ok" else None,
        )
        if self.metrics_path is not None:
            append_event(self.metrics_path, metric)
        return DecisionResult(
            status=status,
            attempted=result.attempted,
            data=data,
            metric=metric,
            retry_after=result.retry_after,
        )
