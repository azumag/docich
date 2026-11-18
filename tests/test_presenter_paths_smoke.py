"""Pin the apt hardening of the presenter-paths-smoke install steps.

Run 44360312635 of #4625 showed the four install steps still exposed to the
slow Azure apt mirror that ci.yml already swaps out: cli-nethack-presenter and
retroarch-presenter were killed by their job timeout while ``apt-get install``
kept crawling through ``Get:`` lines. Every install step must therefore

* swap ``azure.archive.ubuntu.com`` for ``archive.ubuntu.com`` the same way the
  ci.yml system-dependency step does,
* bound every apt call and give up after three attempts instead of letting a
  stalled mirror burn the whole job budget,
* keep the per-attempt install bound short enough to abandon a stalled mirror
  early — apt resumes downloads left in /var/cache/apt/archives/partial, so
  three 300 s attempts still give a degraded mirror ~15 minutes of cumulative
  download time without letting one round eat the job,
* persist the downloaded .deb files through actions/cache and stage them into
  /var/cache/apt/archives so the bounded retries only ever fetch what the
  cache missed — a ~100 KB/s mirror otherwise starves a 13 MB deb through all
  three 300 s attempts (#4625 run 44370031644, libflite1 on soren91-presenter).
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess

import pytest

WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/presenter-paths-smoke.yml'


def _job(name: str) -> str:
    text = WORKFLOW.read_text()
    chunk = text.split(f'\n  {name}:\n', 1)[1]
    # Job keys are the only two-space-indented entries; deeper indentation
    # belongs to the job body.
    return re.split(r'\n  \S', chunk, maxsplit=1)[0]


def _install_step(job_name: str) -> str:
    step = _job(job_name).split('      - name: Install isolated', 1)[1]
    step = step.split('      - name:', 1)[0]
    body = step.split('        run: |\n', 1)[1]
    return '\n'.join(line[10:] for line in body.splitlines())


CACHE_STEP_NAME = '      - name: Cache the downloaded apt archives\n'
CACHE_ACTION = 'actions/cache@0057852bfaa89a56745cba8c7296529d2fc39830'


def _cache_step(job_name: str) -> str:
    step = _job(job_name).split(CACHE_STEP_NAME, 1)[1]
    return step.split('      - name:', 1)[0]


INSTALL_JOBS = [
    'cli-nethack-presenter',
    'retroarch-presenter',
    'soren91-presenter',
    'paper-view-presenter',
]


@pytest.mark.parametrize('job_name', INSTALL_JOBS)
def test_install_steps_swap_the_slow_azure_mirror(job_name):
    step = _install_step(job_name)
    # ci.yml applies this swap to its system-dependency step; the presenter
    # jobs install from the same runner image and need the same fix (#4625).
    assert 'azure.archive.ubuntu.com#archive.ubuntu.com' in step
    assert 'apt-mirrors.txt' in step
    # The swap must happen before the first apt call, and stay guarded so a
    # hermetic replay of the step never touches a real package index.
    swap = step.index('apt-mirrors.txt')
    apt = step.index('apt-get')
    assert swap < apt
    assert 'GITHUB_ACTIONS' in step[:swap]


@pytest.mark.parametrize('job_name', INSTALL_JOBS)
def test_every_apt_call_is_bounded_and_retried_then_fails_fast(job_name):
    step = _install_step(job_name)
    assert step.count('for i in 1 2 3; do') == 2
    assert 'timeout 120 sudo apt-get update' in step
    install = re.search(r'timeout (\d+) sudo apt-get install -y (.*)', step)
    assert install, 'unbounded apt-get install'
    # 600 s once outgrew the job budget (#4625); 300 s abandons a stalled
    # mirror early and apt resumes the partial download on the next attempt.
    assert install.group(1) == '300'
    assert "apt-get update failed 3 times; giving up" in step
    assert "apt-get install failed 3 times; giving up" in step


@pytest.mark.parametrize('job_name', INSTALL_JOBS)
def test_every_apt_job_caches_the_downloaded_archives(job_name):
    step = _cache_step(job_name)
    # Run 44370031644 (#4625): libflite1 kept crawling at ~100 KB/s until the
    # third 300 s bound fired. Persisting the .deb files turns a green job
    # into the seed the next run installs from instead of the mirror.
    assert f'        uses: {CACHE_ACTION}\n' in step
    assert '          path: ~/apt-debs\n' in step
    assert f'          key: apt-debs-ubuntu-24.04-{job_name}-${{{{ github.run_id }}}}-${{{{ github.run_attempt }}}}\n' in step
    # Every apt job on this image shares one prefix so a green job can seed
    # another job's restore.
    restore_keys = step.split('restore-keys: |', 1)[1]
    assert f'            apt-debs-ubuntu-24.04-{job_name}-\n' in restore_keys
    assert '            apt-debs-ubuntu-24.04-\n' in restore_keys
    # The restore must run before the install step that consumes it.
    job = _job(job_name)
    assert job.index(CACHE_STEP_NAME) < job.index('      - name: Install isolated')


@pytest.mark.parametrize('job_name', INSTALL_JOBS)
def test_install_steps_stage_the_restored_archives(job_name):
    step = _install_step(job_name)
    # actions/cache cannot write the root-owned archive directory, so the
    # step seeds /var/cache/apt/archives from ~/apt-debs and traps a harvest
    # of whatever apt still downloaded — covering the failing-attempt exit
    # path as well. Like the mirror swap it sits behind a GITHUB_ACTIONS
    # guard so a hermetic replay never touches a real archive directory.
    staging = re.search(
        r'if \[ "\$\{GITHUB_ACTIONS:-\}" = "true" \]; then\n'
        r'(?P<body>.*?)\n *fi\n',
        step, re.S)
    assert staging, 'the apt-debs staging must sit behind a GITHUB_ACTIONS guard'
    body = staging.group('body')
    assert 'mkdir -p ~/apt-debs' in body
    assert 'sudo cp -n ~/apt-debs/*.deb /var/cache/apt/archives/' in body
    assert "trap 'cp -n /var/cache/apt/archives/*.deb ~/apt-debs/" in body
    # Seeding must happen before the first apt call so the install resolves
    # from the archives instead of the mirror.
    assert step.index('apt-debs/*.deb /var/cache/apt/archives/') < step.index('apt-get update')


@pytest.mark.skipif(os.name != 'posix' or not Path('/bin/bash').exists(), reason='bash required')
@pytest.mark.parametrize('job_name', INSTALL_JOBS)
def test_install_steps_parse_as_bash(job_name):
    result = subprocess.run(
        ['/bin/bash', '--noprofile', '--norc', '-n'],
        input=_install_step(job_name),
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_apt_jobs_carry_the_raised_job_timeouts():
    # The 15/20-minute budgets were consumed by a still-running install before
    # the bound retries could resolve it. These floors keep a degraded-but-
    # working mirror (bounded retries resuming partial downloads) inside the
    # job budget; retroarch pulls the largest dependency tree.
    assert 'timeout-minutes: 20' in _job('cli-nethack-presenter')
    assert 'timeout-minutes: 25' in _job('retroarch-presenter')


def test_install_sets_cover_every_binary_the_smokes_require():
    # The install set may not shrink below what each smoke script checks for,
    # or the job would pass its setup and fail on the dependency probe.
    cli = _install_step('cli-nethack-presenter')
    for pkg in ('ffmpeg', 'tmux', 'xterm', 'xdotool', 'xvfb', 'x11-utils', 'nethack-console'):
        assert pkg in cli
    retro = _install_step('retroarch-presenter')
    for pkg in ('ffmpeg', 'tmux', 'xdotool', 'xvfb', 'x11-utils', 'dbus-x11', 'retroarch'):
        assert pkg in retro
