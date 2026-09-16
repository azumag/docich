"""Arena-blind candidate strategist broker for P5h controlled canaries.

This process runs in a sibling Docker/runsc container which deliberately does
NOT mount ``/canary/episode``.  It sees only the immutable image, a read-only
candidate manifest, and a tiny IPC directory.  The gameplay worker exchanges
strict public ``StrategicRequest`` / ``StrategicProposal`` JSON through a Unix
socket; the candidate process never receives the NetHack playground path.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path

from .nethack_candidate_eval import (
    NethackCandidateError,
    load_candidate_manifest,
    parse_public_replay_request,
)
from .nethack_strategist import CommandStrategist, StrategistDispatchResult

BROKER_SCHEMA_VERSION = 1
MAX_REQUEST_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 32 * 1024
DEFAULT_SOCKET = Path("/canary/ipc/candidate.sock")


class CandidateBrokerError(RuntimeError):
    pass


def _load_manifest(path: Path):
    manifest = load_candidate_manifest(path)
    command = manifest.command if isinstance(manifest.command, str) else list(manifest.command)
    strategist = CommandStrategist(
        command,
        timeout_s=manifest.timeout_s,
        max_request_bytes=manifest.max_request_bytes,
        max_response_bytes=manifest.max_response_bytes,
        cwd=Path("/opt/docich"),
    )
    return manifest, strategist


def _response(result: StrategistDispatchResult) -> dict[str, object]:
    return {
        "schema_version": BROKER_SCHEMA_VERSION,
        "status": result.status,
        "proposal": result.proposal.to_dict() if result.proposal is not None else None,
        "error": result.error,
    }


def dispatch_public_request(strategist: object, raw: object) -> dict[str, object]:
    try:
        request, _observation, _inventory = parse_public_replay_request(raw)
    except (NethackCandidateError, ValueError) as exc:
        return _response(
            StrategistDispatchResult(
                status="error",
                error=f"invalid public request: {str(exc)[:180]}",
            )
        )
    try:
        result = strategist.dispatch(request)
    except Exception as exc:
        result = StrategistDispatchResult(
            status="error",
            error=str(exc).replace("\n", " ")[:240],
        )
    return _response(result)


def _decode_request(payload: bytes) -> object:
    if len(payload) > MAX_REQUEST_BYTES:
        raise CandidateBrokerError("broker request exceeds size limit")
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CandidateBrokerError("broker request is invalid JSON") from exc


def _encode_response(payload: dict[str, object]) -> bytes:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    if len(encoded) > MAX_RESPONSE_BYTES:
        fallback = _response(
            StrategistDispatchResult(status="error", error="broker response exceeds size limit")
        )
        encoded = json.dumps(fallback, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    return encoded


def _serve_connection(conn: socket.socket, strategist: object) -> None:
    conn.settimeout(130.0)
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = conn.recv(4096)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > MAX_REQUEST_BYTES:
            raise CandidateBrokerError("broker request exceeds size limit")
        if b"\n" in chunk:
            break
    data = b"".join(chunks)
    line = data.split(b"\n", 1)[0]
    raw = _decode_request(line)
    conn.sendall(_encode_response(dispatch_public_request(strategist, raw)))


def serve(socket_path: Path, manifest_path: Path) -> int:
    _manifest, strategist = _load_manifest(manifest_path)
    socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(socket_path.parent, 0o700)
    try:
        socket_path.unlink()
    except FileNotFoundError:
        pass
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(str(socket_path))
        os.chmod(socket_path, 0o600)
        server.listen(4)
        while True:
            conn, _addr = server.accept()
            with conn:
                try:
                    _serve_connection(conn, strategist)
                except Exception as exc:
                    conn.sendall(
                        _encode_response(
                            _response(
                                StrategistDispatchResult(
                                    status="error",
                                    error=str(exc).replace("\n", " ")[:240],
                                )
                            )
                        )
                    )
    finally:
        server.close()
        try:
            socket_path.unlink()
        except FileNotFoundError:
            pass


def stdio_once(manifest_path: Path) -> int:
    _manifest, strategist = _load_manifest(manifest_path)
    raw_bytes = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    try:
        raw = _decode_request(raw_bytes.rstrip(b"\r\n"))
        payload = dispatch_public_request(strategist, raw)
    except Exception as exc:
        payload = _response(
            StrategistDispatchResult(
                status="error", error=str(exc).replace("\n", " ")[:240]
            )
        )
    sys.stdout.buffer.write(_encode_response(payload))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docich-nethack-canary-candidate-broker")
    parser.add_argument("--manifest", default="/canary/candidate.json")
    parser.add_argument("--socket", default=str(DEFAULT_SOCKET))
    parser.add_argument("--stdio-once", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        manifest = Path(args.manifest)
        if args.stdio_once:
            return stdio_once(manifest)
        return serve(Path(args.socket), manifest)
    except Exception as exc:
        print(f"candidate broker error: {str(exc)[:240]}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
