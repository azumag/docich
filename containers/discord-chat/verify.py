#!/usr/bin/env python3
"""Build and verify offline Docker contracts with isolated, synthetic resources.

Host Python uses standard library only. No host pip/venv, real credentials or APIs.
"""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
IMAGE = 'docich-discord-chat:offline-verify'
TEST_IMAGE = IMAGE + '-test'
PROJECT = 'discord-verify-' + uuid.uuid4().hex[:10]


def run(args, *, data=None, check=True):
    result = subprocess.run(args, cwd=ROOT, input=data, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=240)
    if check and result.returncode:
        # This verifier has synthetic configuration only; never use with real secrets.
        raise RuntimeError(result.stderr.decode(errors='replace') + result.stdout.decode(errors='replace'))
    return result


def wait_ready(compose):
    for _ in range(100):
        logs = run(compose + ['logs', '--no-color', 'discord-chat']).stdout
        if b'probe ready' in logs:
            return
        time.sleep(.1)
    raise RuntimeError('offline probe did not become ready')


def main():
    dockerfile = 'containers/discord-chat/Dockerfile'
    # Every included path is exact and tracked; fail before transmitting context.
    allowlist = [line[1:] for line in (ROOT / (dockerfile + '.dockerignore')).read_text().splitlines()
                 if line.startswith('!')]
    run(['git', 'ls-files', '--error-unmatch', *allowlist])
    for target, image in [('runtime', IMAGE), ('test', TEST_IMAGE)]:
        built = run(['docker', 'build', '--target', target, '-f', dockerfile, '-t', image, '.'])
        print('Docker build passed: ' + target, flush=True)
    result = run(['docker', 'run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
                  '--security-opt', 'no-new-privileges:true', '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=32m',
                  TEST_IMAGE])
    print(result.stdout.decode(), end='', flush=True)
    persona = 'src/docich/comment/prompts/comment_persona_main.md'
    actual = run(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'python', IMAGE,
                  '-c', f'from pathlib import Path; import hashlib; print(hashlib.sha256(Path("/opt/docich/{persona}").read_bytes()).hexdigest())']).stdout.decode().strip()
    assert actual == hashlib.sha256((ROOT / persona).read_bytes()).hexdigest()
    clean = run(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'python', IMAGE, '-c',
                 'from pathlib import Path; import importlib.util; '
                 'assert importlib.util.find_spec("pytest") is None; '
                 'assert not Path("/opt/docich/tests").exists(); '
                 'assert sorted(p.name for p in Path("/opt/docich/src/docich").iterdir()) == ["__init__.py","comment","discord_chat.py","discord_memory.py","reply_research.py","reply_research_api.py","reply_research_bridge.py","reply_research_diagnostic.py","reply_research_egress.py","reply_research_web.py","reply_routing.py","semantic_decision"]; '
                 'assert sorted(p.name for p in Path("/opt/docich/src/docich/semantic_decision").iterdir()) == ["__init__.py","diagnostics.py","routes.py","transport.py","validator.py"]; '
                 'assert not Path("/opt/docich/.git").exists(); '
                 'assert not Path("/opt/docich/handoff.md").exists()'])
    # Export the image rootfs as a stream, inspect only allowlisted application files.
    cid = run(['docker', 'create', IMAGE]).stdout.decode().strip()
    try:
        import io
        import tarfile
        archive = run(['docker', 'export', cid]).stdout
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            files = [m for m in tar if m.isfile() and m.name.startswith('opt/docich/')]
            for member in files:
                body = tar.extractfile(member).read()
                for canary in (b'synthetic-file-token', b'synthetic-conversation-canary', b'test-bot-secret'):
                    assert canary not in body
    finally:
        run(['docker', 'rm', cid])
    restored_volume = PROJECT + '-restored'
    with tempfile.TemporaryDirectory(prefix='discord-docker-verify-') as tmp:
        tmp = Path(tmp)
        # Synthetic secret preparation is confined to this newly created directory.
        run(['docker', 'run', '--rm', '--network', 'none', '--user', '0:0', '--entrypoint', 'python',
             '--mount', f'type=bind,source={tmp},target=/probe', IMAGE, '-c',
             'from pathlib import Path; import os; '
             '[(p.write_text("synthetic-docker-secret\\n"),p.chmod(0o400),os.chown(p,65532,65532)) '
             'for p in [Path("/probe/token"),Path("/probe/key")]]'])
        settings = tmp / 'settings.env'
        settings.write_text('\n'.join([
            'DOCICH_DISCORD_IMAGE=' + IMAGE, 'DOCICH_DISCORD_TOKEN_SECRET_PATH=' + str(tmp / 'token'),
            'DOCICH_DISCORD_LLM_API_KEY_SECRET_PATH=' + str(tmp / 'key'),
            'DOCICH_DISCORD_LLM_BASE_URL=https://example.invalid/v1',
            'DOCICH_DISCORD_LLM_MODEL=synthetic-model', 'DOCICH_DISCORD_ENABLED=0', 'DOCICH_ALLOW_REAL_AI=0']))
        override = tmp / 'probe.json'
        override.write_text(json.dumps({'services': {'discord-chat': {
            'entrypoint': ['python'], 'command': ['/opt/probe.py', 'hold'],
            'volumes': [{'type': 'bind', 'source': str(ROOT / 'tests/discord_container_probe.py'),
                         'target': '/opt/probe.py', 'read_only': True}]}}}))
        base = ['docker', 'compose', '--env-file', str(settings), '-p', PROJECT,
                '-f', 'compose.discord-chat.yml', '-f', 'compose.discord-chat-api-key.yml',
                '--profile', 'discord-chat']
        compose = base + ['-f', str(override)]
        config = json.loads(run(base + ['config', '--format', 'json']).stdout)
        service = config['services']['discord-chat']
        assert service['environment']['DOCICH_DISCORD_ENABLED'] == '0'
        assert service['environment']['DOCICH_ALLOW_REAL_AI'] == '0'
        assert not service.get('ports') and service['profiles'] == ['discord-chat']
        assert service['user'] == '65532:65532' and service['read_only']
        memory_volume = config['volumes']['memory']['name']
        try:
            # --check validates file readability under the real Compose uid/mounts.
            check = run(base + ['run', '--rm', '-T', '--no-deps', 'discord-chat', '--check'])
            assert b'no network or database writes' in check.stdout
            gated = run(base + ['run', '--rm', '-T', '--no-deps', 'discord-chat'], check=False)
            assert gated.returncode == 2 and b'both DOCICH_DISCORD_ENABLED' in gated.stdout
            for phase in ('seed', 'recall'):
                result = run(compose + ['run', '--rm', '-T', '--no-deps', 'discord-chat', '/opt/probe.py', phase])
                print(result.stdout.decode(), end='', flush=True)
            run(compose + ['up', '-d', '--no-build', 'discord-chat'])
            wait_ready(compose)
            cid = run(compose + ['ps', '-q', 'discord-chat']).stdout.decode().strip()
            # Only selected non-sensitive inspect fields; never dump .Config.Env.
            inspected = json.loads(run(['docker', 'inspect', '--format',
                '{{json .HostConfig}}', cid]).stdout)
            assert inspected['ReadonlyRootfs'] and inspected['Init']
            assert inspected['CapDrop'] == ['ALL']
            assert inspected['SecurityOpt'] == ['no-new-privileges:true']
            assert inspected['Memory'] == inspected['MemorySwap'] == 256 * 1024 * 1024
            assert inspected['NanoCpus'] == 500000000 and inspected['PidsLimit'] == 64
            assert not inspected['Privileged'] and not inspected['PortBindings']
            assert inspected['LogConfig']['Config'] == {'max-file': '3', 'max-size': '1m'}
            print('Applied isolation: nonroot, readonly, caps=0, NNP, seccomp=2, limits, no ports', flush=True)
            lock = run(compose + ['run', '--rm', '-T', '--no-deps', 'discord-chat', '/opt/probe.py', 'lock'])
            assert b'double-start rejected' in lock.stdout
            run(compose + ['stop', 'discord-chat'])
            assert run(['docker', 'inspect', '--format', '{{.State.ExitCode}}', cid]).stdout.strip() == b'0'
            override.write_text(override.read_text().replace('"hold"', '"shutdown"'))
            run(compose + ['up', '-d', '--no-build', '--force-recreate', 'discord-chat'])
            wait_ready(compose)
            cid = run(compose + ['ps', '-q', 'discord-chat']).stdout.decode().strip()
            before = time.monotonic()
            run(compose + ['stop', 'discord-chat'])
            seconds = time.monotonic() - before
            assert run(['docker', 'inspect', '--format', '{{.State.ExitCode}}', cid]).stdout.strip() == b'0'
            logs = run(compose + ['logs', '--no-color', 'discord-chat']).stdout
            assert b'shutdown joined HTTP and closed SQLite' in logs
            for canary in (b'synthetic-docker-secret', b'synthetic-conversation-canary', b'synthetic-reply'):
                assert canary not in logs
            print(f'SIGTERM during HTTP + queued mention: exit=0, joined worker, {seconds:.2f}s', flush=True)
            run(compose + ['run', '--rm', '-T', '--no-deps', 'discord-chat', '/opt/probe.py', 'verify-shutdown'])
            maintenance = ['docker', 'run', '--rm', '-i', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
                           '--security-opt', 'no-new-privileges:true', '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=16m',
                           '--mount']
            snapshot = run(maintenance + [f'type=volume,source={memory_volume},target=/var/lib/docich-discord',
                           '--entrypoint', 'python', IMAGE, '/opt/docich/volume.py', 'backup']).stdout
            run(['docker', 'volume', 'create', restored_volume])
            restore_args = maintenance + [f'type=volume,source={restored_volume},target=/var/lib/docich-discord',
                           '--entrypoint', 'python', IMAGE, '/opt/docich/volume.py', 'restore']
            run(restore_args, data=snapshot)
            assert run(restore_args, data=snapshot, check=False).returncode == 2
            run(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'python',
                 '--mount', f'type=volume,source={restored_volume},target=/var/lib/docich-discord', IMAGE,
                 '-c', 'from docich.discord_memory import MemoryStore; from pathlib import Path; '
                 's=MemoryStore(Path("/var/lib/docich-discord")); '
                 'assert s.db.execute("select count(*) from conversations where state=\'sent\'").fetchone()[0]==2; s.close()'])
            run(compose + ['run', '--rm', '-T', '--no-deps', 'discord-chat', '/opt/probe.py', 'delete'])
            print('Backup/restore, nonempty restore rejection and scoped deletion passed', flush=True)
        finally:
            run(compose + ['down'], check=False)
            for volume in (memory_volume, restored_volume):
                run(['docker', 'volume', 'rm', volume], check=False)
    identity = run(['docker', 'image', 'inspect', '--format', '{{.Id}}', IMAGE]).stdout.decode().strip()
    print('Runtime image: ' + identity, flush=True)
    print('Offline verification complete; test containers/networks/volumes removed. No real Discord/LLM connection.', flush=True)


if __name__ == '__main__':
    main()
