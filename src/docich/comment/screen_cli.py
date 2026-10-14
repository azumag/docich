"""Private CLI bridge for one legacy comment-generation attempt.

stdin: already classified batch JSON. stdout: candidate text only. There are no
capture commands, paths or image bytes in comment input. Delivery remains legacy.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from docich.llm.contracts import DispatchRequest
from .screen_reply import generate_screen_reply


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument("--agents", required=True)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--last-agent-file", required=True)
    parser.add_argument("--failure-kind-file", required=True)
    args = parser.parse_args(argv)
    # Explicit generation permission, separate from capture approval and Jev.
    if os.environ.get("DOCICH_ALLOW_REAL_AI") != "1":
        return 2
    try:
        from docich.llm.policy import MAX_PROMPT_BYTES, parse_agents
        from docich.comment.guard import is_valid_generation_candidate
        from docich.semantic_decision.validator import strict_json
        for filename in (args.last_agent_file, args.failure_kind_file):
            Path(filename).write_text("", encoding="utf-8")
        with open(args.prompt_file, "rb") as stream:
            raw_prompt = stream.read(MAX_PROMPT_BYTES + 1)
        if len(raw_prompt) > MAX_PROMPT_BYTES:
            return 2
        prompt = raw_prompt.decode("utf-8")
        raw_rows = sys.stdin.buffer.read(1024 * 1024 + 1)
        if len(raw_rows) > 1024 * 1024:
            return 2
        rows = strict_json(raw_rows)
        request = DispatchRequest("COMMENT", prompt, parse_agents(args.agents, os.environ),
                                  timeout_sec=args.timeout, validator=is_valid_generation_candidate)
        reply = generate_screen_reply(request, rows, env=os.environ, overall_timeout_sec=args.timeout)
        result = reply.result
        print(json.dumps({"component": "comment_screen", **reply.metrics()},
                         separators=(",", ":")), file=sys.stderr)
        # Existing compatibility sidecars; no new raw prompt/image logging.
        Path(args.last_agent_file).write_text(result.last_agent + "\n", encoding="utf-8")
        Path(args.failure_kind_file).write_text(result.failure_kind + "\n", encoding="utf-8")
        if result.ok:
            print(result.output)
        return result.returncode
    except Exception:
        # Never disclose exception text, paths, API responses or credentials.
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
