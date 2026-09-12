"""Trusted image entry point; executed ONLY inside the configured gVisor guest.

This is not a Python language sandbox. The external runtime enforces isolation.
"""
import json
import os
import random
import resource
import sys


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_FSIZE, (8 * 1024 * 1024, 8 * 1024 * 1024))
    # A guest clock is not promised to be deterministic. Persist only JSON state.
    envelope = json.loads(sys.stdin.buffer.read(1024 * 1024 + 1))
    context = envelope["context"]
    random.seed(context["seed"])
    scope = {"__name__": "strategy", "__file__": "/strategy.py"}
    exec(compile(envelope["source"], "/strategy.py", "exec"), scope)
    result = scope["decide"](context)
    raw = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    if len(raw) > 64 * 1024:
        raise ValueError("output_limit")
    sys.stdout.buffer.write(raw)
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        # Do not echo source, context, or exception strings into container logs.
        os.write(2, b"strategy_failed\n")
        os._exit(1)
