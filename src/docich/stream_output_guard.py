"""Pure, versioned guard for the single encoder's local RTMP publish URL.

Ownership: docich streaming infrastructure. Validation only: this module does
not import the Soviet game, launch FFmpeg, open a socket, or read credentials.
The destination relay remains the exclusive owner of external stream keys.
"""
from urllib.parse import urlsplit

LOOPBACK_RTMP_OUTPUT_CONTRACT_VERSION = 1


def validate_loopback_rtmp_url(raw: str) -> str:
    """Return the unchanged URL only when it is an RTMP loopback endpoint.

    Preserve the established direct-stream validation order and messages while
    the legacy caller continues to enforce its own guard on every request.
    """
    try:
        parsed = urlsplit(raw)
    except ValueError as exc:
        raise ValueError("SOREN_DIRECT_STREAM_LOCAL_URL must be a valid loopback RTMP URL") from exc
    if parsed.scheme != "rtmp":
        raise ValueError("SOREN_DIRECT_STREAM_LOCAL_URL must use rtmp://")
    if parsed.username or parsed.password:
        raise ValueError("SOREN_DIRECT_STREAM_LOCAL_URL must not contain credentials")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("SOREN_DIRECT_STREAM_LOCAL_URL must target loopback")
    if not parsed.path or parsed.path == "/":
        raise ValueError("SOREN_DIRECT_STREAM_LOCAL_URL must include an application/stream path")
    if parsed.query or parsed.fragment:
        raise ValueError("SOREN_DIRECT_STREAM_LOCAL_URL must not include query or fragment data")
    return raw
