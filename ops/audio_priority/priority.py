#!/usr/bin/env python3
"""配信音声の経路にいるプロセスの優先度を、CPU飽和下でも一定に保つ(docich#1811)。

本番VMは4コアでCPU圧力(PSI)が約70%に達し、ゲーム音を出すRetroArchのメインスレッドが
実行時間の2倍以上CPU待ちになって、PulseAudioへ届く音が数十〜百数十ms欠けていた。
RetroArch、配信エンコーダ(ffmpeg)、PulseAudio、TwiCaフィーダの全スレッドを一定のnice値へ保つ。

* 対象は所有者(既定ubuntu)・comm・引数が一致するプロセスだけ。他のプロセスには触らない。
* 優先度を「上げる」方向(nice値を下げる)にだけ作用し、既に目標以下なら何もしない。
* コーナー切替でRetroArchが作り直されても、次の周期(既定5秒)で追随する。
* 例外はプロセス単位で握りつぶし、ループは落とさない。ログは変更があったときだけ。
"""
from __future__ import annotations

import argparse
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


def _is_encoder_ffmpeg(p: Proc) -> bool:
    # 配信エンコーダ(FLV出力)だけ。PNGキャプチャ等の短命ffmpegは含めない。
    return (p.comm == 'ffmpeg' and 'x11grab' in p.argv
            and any(a == 'flv' for a in p.argv))


def _is_twica_feeder(p: Proc) -> bool:
    joined = ' '.join(p.argv)
    return p.comm.startswith('python') and (
        '-m docich.twica_encoder' in joined or '-m docich.twica_ffmpeg' in joined)


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
        for tid, nice in nices.get(p.pid, {}).items():
            if nice > target:
                out.append((p.pid, tid, kind, nice))
    return out


def _read(path: str) -> str:
    with open(path, 'rb') as handle:
        return handle.read().decode('utf-8', 'replace')


def scan(uid: int):
    """/procを走査して(Procの一覧, {pid: {tid: nice}})を返す。comm→引数の順で安価に絞る。"""
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
            argv = tuple(a for a in _read(f'/proc/{pid}/cmdline').split('\0') if a)
            proc = Proc(pid, uid, comm, argv)
            if wanted(proc, uid) is None:
                continue
            per_tid = {}
            for tid in os.listdir(f'/proc/{pid}/task'):
                try:
                    # statのcomm欄は括弧で囲まれ空白を含みうるので、最後の')'以降で分割する。
                    rest = _read(f'/proc/{pid}/task/{tid}/stat').rsplit(')', 1)[1].split()
                    per_tid[int(tid)] = int(rest[16])  # 全体の19番目=nice
                except (OSError, IndexError, ValueError):
                    continue
            processes.append(proc)
            nices[pid] = per_tid
        except (OSError, ValueError):
            continue
    return processes, nices


def apply_once(uid: int, target: int, dry_run: bool) -> int:
    processes, nices = scan(uid)
    todo = plan(processes, nices, uid, target)
    changed = {}
    for pid, tid, kind, _nice in todo:
        if dry_run:
            changed.setdefault((pid, kind), 0)
            changed[(pid, kind)] += 1
            continue
        try:
            os.setpriority(os.PRIO_PROCESS, tid, target)
            changed.setdefault((pid, kind), 0)
            changed[(pid, kind)] += 1
        except OSError:
            continue  # スレッドが既に終了した等。次の周期で再判定する。
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
    if args.interval < 1:
        parser.error('--interval must be at least 1 second')
    uid = args.uid
    if uid is None:
        import pwd
        uid = pwd.getpwnam('ubuntu').pw_uid
    if args.once:
        apply_once(uid, args.nice, args.dry_run)
        return 0
    while True:
        try:
            apply_once(uid, args.nice, args.dry_run)
        except Exception as exc:  # ループは落とさない(原因は次周期で再試行)
            print(f'audio-priority: scan failed: {type(exc).__name__}', file=sys.stderr, flush=True)
        time.sleep(args.interval)


if __name__ == '__main__':
    raise SystemExit(main())
