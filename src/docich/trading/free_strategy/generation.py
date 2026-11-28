"""One model request: HTTPS chat-completions by default, optional DSH (disabled).

The operator explicitly configures a compatible HTTPS chat-completions endpoint, or
opts into the DeepSeek harness with ``DOCICH_FREE_STRATEGY_GENERATOR=dsh`` AND
``DOCICH_FREE_STRATEGY_DSH_ENABLED=1``.  Credentials remain in this trusted broker.
Generated source is just data until the immutable artifact reaches DockerSandbox.
"""
from __future__ import annotations

import http.client
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import tempfile
import time
from typing import Callable
from urllib.parse import urlsplit

from .contract import Artifact, StrategyError, bounded_text, decode, encode
from .store import LabStore, MAX_OBSERVED_EXPERIMENTS


_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "generate_strategy.md"
_PROMPT_HEADER = re.compile(r"\A<!--.*?-->\n", re.DOTALL)


def _prompt_template() -> str:
    """The reviewed text before the bounded data. The HTML header is documentation only.

    The template lives next to the code and is loaded relative to ``__file__`` so the
    checked-in prompt is the single source of truth (no package data entry needed).
    """
    try:
        raw = _PROMPT_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise StrategyError("invalid_prompt_template") from exc
    match = _PROMPT_HEADER.match(raw)
    if match is None:
        raise StrategyError("invalid_prompt_template")
    return raw[match.end():]


def build_prompt(*, symbols: list[str], prior: list[dict], brief: str) -> str:
    return _prompt_template() + encode(
        {"allowed_symbols": symbols,
         "prior_families": [{"family": item.get("family")} for item in prior[:12]],
         "prior_results": [item for item in prior[:8] if "net_pnl_jpy" in item],
         "research_brief": brief}).decode()


def request_text(prompt: str) -> str:
    endpoint = os.environ.get("DOCICH_STRATEGY_AI_ENDPOINT", "")
    model = os.environ.get("DOCICH_STRATEGY_AI_MODEL", "")
    token = os.environ.get("DOCICH_STRATEGY_AI_TOKEN", "")
    url = urlsplit(endpoint)
    if (url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment
            or not model or not token or len(model) > 200):
        raise StrategyError("text_provider_not_configured")
    body = encode({"model": model, "messages": [{"role": "user", "content": prompt}],
                   "max_tokens": 6000, "stream": False, "tool_choice": "none"})
    conn = http.client.HTTPSConnection(url.hostname, url.port or 443, timeout=90)
    try:
        conn.request("POST", url.path or "/", body, headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
        response = conn.getresponse()
        # Redirects are never followed, so credentials cannot move to another host.
        if response.status != 200:
            raise StrategyError("text_provider_failed")
        data = decode(response.read(512 * 1024 + 1), limit=512 * 1024)
        choice = data["choices"][0]
        message = choice["message"]
        if choice.get("finish_reason") != "stop" or message.get("tool_calls") or message.get("function_call"):
            raise StrategyError("text_provider_incomplete")
        if not isinstance(message.get("content"), str):
            raise StrategyError("text_provider_invalid")
        return message["content"]
    except StrategyError:
        raise
    except Exception as exc:
        raise StrategyError("text_provider_failed") from exc
    finally:
        conn.close()


# ---- DSH (DeepSeek harness) generator, disabled by default ---------------------------------
#
# The observed final-answer event is ``{"type":"final", ..., "text":"...", "reason":
# {"kind":"completed"}}`` (confirmed with ``--help``, ``--dump-config`` and one short
# harmless real run of ``npx --yes @deepseek-ai/dsh@latest --profile headless
# --approval-policy never --json -`` in an empty temp dir; ids redacted in the test
# fixture).  Unknown event types are ignored and the LAST completed ``final`` event wins.
GENERATOR_ENV = "DOCICH_FREE_STRATEGY_GENERATOR"
DSH_ENABLED_ENV = "DOCICH_FREE_STRATEGY_DSH_ENABLED"

DSH_ARGV = ("npx", "--yes", "@deepseek-ai/dsh@latest", "--profile", "headless",
            "--approval-policy", "never", "--permission-mode", "read-only", "--json", "-")
DSH_TIMEOUT_S = 900.0
DSH_STDOUT_LIMIT = 2 * 1024 * 1024
DSH_STDERR_LIMIT = 64 * 1024

# Only these variables cross into the harness.  DOCICH_* (including the HTTPS provider
# token), LIVE/bitbank/exchange settings and every other ambient value are dropped so a
# generated strategy or prompt injection cannot read docich credentials.
DSH_ENV_ALLOW = (
    "PATH", "HOME", "LANG", "LC_ALL", "TMPDIR",
    "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY",
    "https_proxy", "http_proxy", "no_proxy",
    "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE",
    "DSH_HOME", "npm_config_cache", "OPENCODE_API_KEY",
)


def _dsh_environment() -> dict[str, str]:
    return {name: os.environ[name] for name in DSH_ENV_ALLOW if name in os.environ}


def _terminate_process_group(proc) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except OSError:
        pass
    try:
        proc.kill()
    except OSError:
        pass


def _collect_dsh(proc, payload: bytes) -> bytes:
    """Write stdin and read both pipes with hard size/time bounds.

    The child runs in its own session (``start_new_session=True``), so a timeout or an
    oversized pipe kills the whole process group, not only npx.
    """
    deadline = time.monotonic() + DSH_TIMEOUT_S
    output, errors = bytearray(), bytearray()
    remaining = memoryview(payload)
    try:
        with selectors.DefaultSelector() as selector:
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                os.set_blocking(stream.fileno(), False)
                selector.register(
                    stream,
                    selectors.EVENT_WRITE if stream is proc.stdin else selectors.EVENT_READ,
                )
            while selector.get_map():
                left = deadline - time.monotonic()
                if left <= 0:
                    raise StrategyError("text_provider_failed")
                for key, _ in selector.select(min(left, 0.1)):
                    stream = key.fileobj
                    if stream is proc.stdin:
                        try:
                            count = os.write(stream.fileno(), remaining[:8192])
                        except BrokenPipeError:
                            count = len(remaining)
                        remaining = remaining[count:]
                        if not remaining:
                            selector.unregister(stream)
                            stream.close()
                        continue
                    chunk = os.read(stream.fileno(), 8192)
                    if not chunk:
                        selector.unregister(stream)
                        continue
                    buffer = output if stream is proc.stdout else errors
                    buffer.extend(chunk)
                    limit = DSH_STDOUT_LIMIT if stream is proc.stdout else DSH_STDERR_LIMIT
                    if len(buffer) > limit:
                        raise StrategyError("text_provider_failed")
            try:
                code = proc.wait(timeout=max(0.01, deadline - time.monotonic()))
            except subprocess.TimeoutExpired as exc:
                raise StrategyError("text_provider_failed") from exc
        if code != 0:
            raise StrategyError("text_provider_failed")
        return bytes(output)
    finally:
        if proc.poll() is None:
            _terminate_process_group(proc)
        try:
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                stream.close()
            except OSError:
                pass


def _parse_dsh_output(raw: bytes) -> str:
    answer = None
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            event = decode(line, limit=DSH_STDOUT_LIMIT)
        except StrategyError as exc:
            raise StrategyError("text_provider_failed") from exc
        if not isinstance(event, dict):
            raise StrategyError("text_provider_failed")
        if event.get("type") != "final":
            continue   # status/session/text/unknown events are tolerated
        reason = event.get("reason")
        text = event.get("text")
        if (isinstance(reason, dict) and reason.get("kind") == "completed"
                and isinstance(text, str) and text.strip()):
            answer = text
    if answer is None:
        raise StrategyError("text_provider_failed")
    return answer


def request_text_dsh(prompt: str) -> str:
    """Run one DSH turn and return only its final assistant text.

    Safety boundary (also a review requirement):
    * cwd is a brand-new empty TemporaryDirectory, never the repo or trading dir, and no
      ``--add-dir`` is passed; it is removed afterwards.
    * only ``DSH_ENV_ALLOW`` crosses into the child (see above).
    * the observed ``read-only`` permission preset is passed.  DSH is still an agent with
      tools and network: a prompt injection through ``research_brief`` and the npm
      ``@latest`` supply chain remain residual risks.  The answer is therefore only data;
      it goes through the same Artifact validation and only runs inside gVisor.  It is
      never imported or executed on the host.
    * stdout/stderr and provider errors are bounded and never logged or returned verbatim;
      every failure is the fixed ``text_provider_failed`` code.
    """
    payload = prompt.encode("utf-8")
    with tempfile.TemporaryDirectory(prefix="docich-dsh-") as cwd:
        try:
            proc = subprocess.Popen(
                DSH_ARGV, cwd=cwd, env=_dsh_environment(),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as exc:
            raise StrategyError("text_provider_failed") from exc
        raw = _collect_dsh(proc, payload)
    return _parse_dsh_output(raw)


def select_text_fn() -> Callable[[str], str]:
    """Resolve the configured generator without spawning or calling anything yet."""
    generator = os.environ.get(GENERATOR_ENV, "https")
    if generator == "https":
        return request_text
    if generator != "dsh":
        raise StrategyError("invalid_text_generator")
    if os.environ.get(DSH_ENABLED_ENV) != "1":
        # Same fixed code as an unconfigured HTTPS provider; no process is spawned.
        raise StrategyError("text_provider_not_configured")
    return request_text_dsh


def generate(trading_dir: Path, *, image: str, symbols: list[str], brief: str,
             capital: str = "10000", days: int = 30, now_fn=time.time,
             text_fn: Callable[[str], str] | None = None,
             prior_results: list[dict] | None = None) -> dict:
    """Generate and register one candidate (at most one request per UTC day).

    ``prior_results`` (the improvement loop) replaces the plain family list with bounded
    cost-inclusive results of earlier candidates, so the next one learns from them.
    """
    bounded_text(brief, 1000)
    if text_fn is None:
        text_fn = select_text_fn()
    store = LabStore(Path(trading_dir) / "free-strategies" / "lab.sqlite3")
    try:
        # Validate all operator settings before a billable request.
        Artifact.create(source="# candidate", image=image, name="candidate", family="candidate", thesis=brief[:600], symbols=symbols)
        with store.transaction():
            store.db.execute("CREATE TABLE IF NOT EXISTS generations (day INTEGER PRIMARY KEY, status TEXT NOT NULL)")
            day = int(now_fn() // 86400)
            # Paused experiments still consume depth/status monitoring budget.
            # Reject before the provider call so a full observation queue does
            # not spend generation tokens on an experiment that cannot start.
            if len(store.research_observed_ids()) >= MAX_OBSERVED_EXPERIMENTS:
                return {"status": "skipped", "reason": "experiment_capacity"}
            if store.db.execute("SELECT 1 FROM generations WHERE day=?", (day,)).fetchone():
                return {"status": "skipped", "reason": "generation_daily_budget"}
            prior = list(prior_results) if prior_results is not None else [
                {"family": decode(row[0], limit=512 * 1024)["family"]}
                for row in store.db.execute("SELECT payload FROM artifacts ORDER BY rowid DESC LIMIT 12")]
            store.db.execute("INSERT INTO generations VALUES (?, 'requested')", (day,))
        raw = text_fn(build_prompt(symbols=symbols, prior=prior, brief=brief))
        data = decode(raw.encode(), limit=384 * 1024)
        if not isinstance(data, dict) or set(data) != {"source", "name", "family", "thesis"}:
            raise StrategyError("invalid_generated_artifact")
        artifact = Artifact.create(**data, image=image, symbols=symbols)
        digest = store.register(artifact)
        identity = store.create(digest, now=now_fn(), capital=capital, days=days)
        with store.transaction():
            store.db.execute("UPDATE generations SET status='registered' WHERE day=?", (day,))
        return {"status": "registered", "artifact": digest, "experiment": identity, "mode": "PAPER"}
    finally:
        store.close()
