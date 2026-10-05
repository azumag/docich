#!/usr/bin/env python3
"""配信音声の経路にいるプロセスの優先度を、CPU飽和下でも一定に保つ(docich#1811)。

本番VMは4コアでCPU圧力(PSI)が約70%に達し、ゲーム音を出すRetroArchのメインスレッドが
実行時間の2倍以上CPU待ちになって、PulseAudioへ届く音が数十〜百数十ms欠けていた。
RetroArch、配信エンコーダ(ffmpeg)、PulseAudio、TwiCaフィーダの全スレッドを一定のnice値へ保つ。

* 対象は所有者(既定ubuntu)・comm・引数が一致するプロセスだけ。他のプロセスには触らない。
* 優先度を「上げる」方向(nice値を下げる)にだけ作用し、既に目標以下なら何もしない。
* コーナー切替でRetroArchが作り直されても、次の周期(既定5秒)で追随する。
* 終了済みのプロセス・スレッドは無視する。権限不足などは警告し、同種の警告は間引く。
"""
from __future__ import annotations

import argparse
import errno
import math
import os
import sys
import time
from dataclasses import dataclass

TARGET_NICE = -10
INTERVAL_SEC = 5.0


@dataclass(frozen=True)
class Proc:
    pid: int
    uid: int
    comm: str
    argv: tuple[str, ...]
    starttime: int = 0


@dataclass(frozen=True)
class Thread:
    nice: int
    starttime: int


class Warnings:
    """権限不足を可視化しつつ、スレッド数・5秒周期によるログの連発を抑える。"""
    def __init__(self):
        self.errors = 0
        self._last = {}

    def report(self, operation: str, exc: OSError, pid: int, tid: int | None = None):
        if exc.errno in (errno.ESRCH, errno.ENOENT):
            return  # /proc走査中に終了するのは正常。
        self.errors += 1
        key = (operation, exc.errno)
        now = time.monotonic()
        if key in self._last and now - self._last[key] < 60:
            return
        self._last[key] = now
        code = errno.errorcode.get(exc.errno, 'UNKNOWN')
        # cmdline・例外本文は出さない（配信先などの機密情報を含み得る）。
        print(f'audio-priority: warning operation={operation} pid={pid} tid={tid} '
              f'errno={code} (same warning limited to once per 60s)', file=sys.stderr, flush=True)


def _is_encoder_ffmpeg(p: Proc) -> bool:
    # 配信エンコーダ(FLV出力)だけ。PNGキャプチャ等の短命ffmpegは含めない。
    return (p.comm == 'ffmpeg' and 'x11grab' in p.argv
            and any(a == 'flv' for a in p.argv))


def _is_twica_feeder(p: Proc) -> bool:
    # 起動側が使う「python [-u/-I等] -m MODULE」のみ。-cやスクリプトの引数は読まない。
    if not p.comm.startswith('python'):
        return False
    for index in range(1, len(p.argv)):
        arg = p.argv[index]
        if arg == '-m':
            return index + 1 < len(p.argv) and p.argv[index + 1] in (
                'docich.twica_encoder', 'docich.twica_ffmpeg')
        if arg not in ('-u', '-B', '-E', '-I', '-s', '-S', '-P', '-q', '-O', '-OO'):
            return False  # 未対応の起動形式は安全側に倒す。
    return False


def wanted(p: Proc, uid: int) -> str | None:
    """対象なら理由(分類名)を返す。対象外はNone。"""
    if p.uid != uid:
        return None
    if p.comm == 'retroarch':
        return 'retroarch'
    if p.comm == 'pulseaudio':
        return 'pulseaudio'
    if _is_encoder_ffmpeg(p):
        return 'encoder-ffmpeg'
    if _is_twica_feeder(p):
        return 'twica-feeder'
    return None


def plan(processes, nices, uid: int, target: int = TARGET_NICE):
    """(pid, tid, 分類, 現在nice)のうち、目標より低優先(nice値が大きい)なものを返す。"""
    out = []
    for p in processes:
        kind = wanted(p, uid)
        if kind is None:
            continue
        for tid, thread in nices.get(p.pid, {}).items():
            if thread.nice > target:
                out.append((p.pid, tid, kind, thread.nice))
    return out


def _read(path: str) -> str:
    with open(path, 'rb') as handle:
        return handle.read().decode('utf-8', 'replace')


def _thread(path: str) -> Thread:
    # statのcomm欄は空白や')'を含みうるので、最後の')'以降で分割する。
    rest = _read(path).rsplit(')', 1)[1].split()
    return Thread(nice=int(rest[16]), starttime=int(rest[19]))  # 全体の19/22番目


def _process(pid: int) -> Proc:
    root = f'/proc/{pid}'
    uid = os.stat(root).st_uid
    starttime = _thread(f'{root}/stat').starttime
    comm = _read(f'{root}/comm').strip()
    argv = tuple(a for a in _read(f'{root}/cmdline').split('\0') if a)
    return Proc(pid, uid, comm, argv, starttime)


def scan(uid: int, warnings: Warnings | None = None):
    """/procを走査して(Procの一覧, {pid: {tid: Thread}})を返す。"""
    if warnings is None:
        warnings = Warnings()
    interesting = ('retroarch', 'pulseaudio', 'ffmpeg')
    processes, nices = [], {}
    for name in os.listdir('/proc'):
        if not name.isdigit():
            continue
        pid = int(name)
        try:
            if os.stat(f'/proc/{pid}').st_uid != uid:
                continue
            comm = _read(f'/proc/{pid}/comm').strip()
            if not (comm in interesting or comm.startswith('python')):
                continue
            proc = _process(pid)
            if wanted(proc, uid) is None:
                continue
            per_tid = {}
            for tid in os.listdir(f'/proc/{pid}/task'):
                try:
                    per_tid[int(tid)] = _thread(f'/proc/{pid}/task/{tid}/stat')
                except OSError as exc:
                    warnings.report('scan-thread', exc, pid, int(tid))
                except (IndexError, ValueError):
                    continue
            processes.append(proc)
            nices[pid] = per_tid
        except OSError as exc:
            warnings.report('scan-process', exc, pid)
        except (IndexError, ValueError):
            continue
    return processes, nices


def apply_once(uid: int, target: int, dry_run: bool, warnings: Warnings | None = None) -> int:
    if warnings is None:
        warnings = Warnings()
    processes, nices = scan(uid, warnings)
    by_pid = {p.pid: p for p in processes}
    todo = plan(processes, nices, uid, target)
    changed = {}
    for pid, tid, kind, _nice in todo:
        if dry_run:
            changed.setdefault((pid, kind), 0)
            changed[(pid, kind)] += 1
            continue
        try:
            # 古い走査結果のPID/TIDをそのまま使わない。exec・所有者変更・再利用を検出する。
            if _process(pid) != by_pid[pid]:
                continue
            task = f'/proc/{pid}/task/{tid}'
            if os.stat(task).st_uid != uid:
                continue
            if _thread(f'{task}/stat').starttime != nices[pid][tid].starttime:
                continue
            # 他の管理者が既に優先度を上げていれば、その値を維持する。
            if os.getpriority(os.PRIO_PROCESS, tid) <= target:
                continue
            os.setpriority(os.PRIO_PROCESS, tid, target)
            changed.setdefault((pid, kind), 0)
            changed[(pid, kind)] += 1
        except OSError as exc:
            warnings.report('apply', exc, pid, tid)
        except (IndexError, ValueError):
            continue
    for (pid, kind), count in sorted(changed.items()):
        verb = 'would renice' if dry_run else 'reniced'
        print(f'audio-priority: {verb} pid={pid} kind={kind} threads={count} nice={target}', flush=True)
    return sum(changed.values())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--uid', type=int, default=None, help='対象プロセスの所有者UID(既定: ubuntu)')
    parser.add_argument('--nice', type=int, default=TARGET_NICE)
    parser.add_argument('--interval', type=float, default=INTERVAL_SEC)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    if not -20 <= args.nice <= 0:
        parser.error('--nice must be between -20 and 0 (this tool only raises priority)')
    if not math.isfinite(args.interval) or args.interval < 1:
        parser.error('--interval must be at least 1 second')
    uid = args.uid
    if uid is None:
        import pwd
        try:
            uid = pwd.getpwnam('ubuntu').pw_uid
        except KeyError:
            parser.error('ubuntu user not found; supply --uid explicitly')
    if uid < 0:
        parser.error('--uid must not be negative')
    warnings = Warnings()
    if args.once:
        apply_once(uid, args.nice, args.dry_run, warnings)
        return int(warnings.errors != 0)
    print(f'audio-priority: started uid={uid} nice={args.nice} interval={args.interval} '
          f'dry_run={args.dry_run}', flush=True)
    while True:
        try:
            apply_once(uid, args.nice, args.dry_run, warnings)
        except Exception as exc:  # ループは落とさない(原因は次周期で再試行)
            print(f'audio-priority: scan failed: {type(exc).__name__}', file=sys.stderr, flush=True)
        time.sleep(args.interval)


if __name__ == '__main__':
    raise SystemExit(main())
