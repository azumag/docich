#!/usr/bin/env python3
"""GUI-session launchd service for the existing Soren91 macOS renderer agent.

The secret is read only at launch/status, never put in argv, plist, or output.
This owns the local HTTP agent only; it never requests a game start or stop.
"""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import plistlib
import shutil
import socket
import stat
import subprocess
import sys
import urllib.request

LABEL = "com.docich.soren91-agent"
FIELDS = {"repo", "node", "ffmpeg", "token_file", "host", "port"}


def read_token(path):
    # O_NOFOLLOW closes the symlink race; reject permissive/foreign files.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o077):
            raise ValueError("credential-file-permissions")
        data = stream.read(4097)
    token = data.decode("ascii").strip()
    if not 24 <= len(token) <= 4096 or any(not 33 <= ord(c) <= 126 for c in token):
        raise ValueError("credential-file-shape")
    return token


def validate(config):
    if not isinstance(config, dict) or set(config) != FIELDS:
        raise ValueError("configuration-shape")
    host = ipaddress.ip_address(config["host"])
    if host.version != 4 or host not in ipaddress.ip_network("100.64.0.0/10"):
        raise ValueError("tailscale-address-required")
    if type(config["port"]) is not int or not 1024 <= config["port"] <= 65535:
        raise ValueError("configuration-port")
    for key in ("repo", "node", "ffmpeg", "token_file"):
        if not isinstance(config[key], str) or not Path(config[key]).is_absolute():
            raise ValueError("absolute-path-required")
    if not (Path(config["repo"]) / "tools/soren91_local_agent.mjs").is_file():
        raise ValueError("agent-script-missing")
    for key in ("node", "ffmpeg"):
        if not Path(config[key]).is_file() or not os.access(config[key], os.X_OK):
            raise ValueError("executable-missing")
    return config


def environment(config, inherited=None):
    validate(config)
    inherited = os.environ if inherited is None else inherited
    env = {k: inherited[k] for k in ("HOME", "USER", "LOGNAME", "TMPDIR", "LANG")
           if k in inherited}
    # Do not inherit stale SOREN91_* overrides from an interactive shell.
    env.update({
        "PATH": "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "SOREN91_LOCAL_AGENT_HOST": config["host"],
        "SOREN91_LOCAL_AGENT_PORT": str(config["port"]),
        "SOREN91_LOCAL_AGENT_TOKEN": read_token(config["token_file"]),
        "SOREN91_LOCAL_SESSION_MODE": "cdp-host",
        "SOREN91_CDP_BIND_IP": config["host"],
        "SOREN91_LOCAL_FFMPEG_BIN": config["ffmpeg"],
        "SOREN91_CDP_HOST_SESSION_SEC": "2400",
    })
    return env


def service_plist(python, script, config):
    return {
        "Label": LABEL,
        "ProgramArguments": [str(python), str(script), "run", "--config", str(config)],
        "LimitLoadToSessionType": "Aqua",
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 30,
        "ExitTimeOut": 20,
        "ProcessType": "Interactive",
        # launchd retains the exit status. Query authenticated status for health;
        # child logs may contain URLs or arbitrary output, so never forward them.
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
    }


def paths(home):
    root = home / "Library/Application Support/docich/soren91-agent"
    return root, home / "Library/LaunchAgents" / (LABEL + ".plist")


def install(config):
    if sys.platform != "darwin":
        raise ValueError("macos-required")
    validate(config)
    read_token(config["token_file"])
    domain = "gui/" + str(os.getuid())
    subprocess.run(["launchctl", "print", domain], check=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)
    loaded = subprocess.run(["launchctl", "print", domain + "/" + LABEL],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if loaded.returncode == 0:
        raise ValueError("service-already-loaded")
    # Never replace a manually running agent (or another listener) blindly.
    with socket.socket() as probe:
        probe.settimeout(2)
        if probe.connect_ex((config["host"], config["port"])) == 0:
            raise ValueError("agent-port-occupied")
    root, plist_path = paths(Path.home())
    if root.exists() or plist_path.exists():
        raise ValueError("installation-already-exists")
    root.mkdir(parents=True, mode=0o700)
    script = root / "service.py"
    shutil.copyfile(Path(__file__), script)
    script.chmod(0o700)
    config_path = root / "config.json"
    config_path.write_text(json.dumps(config, sort_keys=True) + "\n")
    config_path.chmod(0o600)
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_bytes(plistlib.dumps(service_plist(
        Path(sys.executable).resolve(), script, config_path)))
    plist_path.chmod(0o600)
    subprocess.run(["launchctl", "bootstrap", domain, str(plist_path)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(json.dumps({"status": "installed", "label": LABEL}))


def status(config):
    validate(config)
    request = urllib.request.Request(
        "http://{}:{}/v1/status".format(config["host"], config["port"]),
        headers={"Authorization": "Bearer " + read_token(config["token_file"])})
    # The private Tailscale request must not traverse an inherited HTTP proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=5) as response:
        value = json.loads(response.read(16385))
    if (value.get("ok") is not True or value.get("backend") != "local-macos"
            or value.get("mode") != "cdp-host" or type(value.get("running")) is not bool):
        raise ValueError("unexpected-agent-status")
    # Fixed projection: never print arbitrary responses, lastExit or URL.
    return {"status": "healthy", "backend": "local-macos", "mode": "cdp-host",
            "renderer_running": value["running"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("install", "run", "status"))
    parser.add_argument("--config", type=Path, required=True,
                        help="non-secret JSON: repo, node, ffmpeg, token_file, host, port")
    args = parser.parse_args()
    try:
        config = validate(json.loads(args.config.read_text()))
        if args.operation == "install":
            install(config)
        elif args.operation == "status":
            print(json.dumps(status(config)))
        else:
            if sys.platform != "darwin":
                raise ValueError("macos-required")
            env = environment(config)
            os.chdir(config["repo"])
            # Exec, rather than detach, makes launchd own the agent PID/group.
            os.execve(config["node"], [config["node"], "tools/soren91_local_agent.mjs"], env)
        return 0
    except Exception:
        # Neither credential values nor arbitrary exception text reach logs.
        print(json.dumps({"status": "failed", "operation": args.operation}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
