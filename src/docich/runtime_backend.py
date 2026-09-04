"""RuntimeBackend: webui.py から read-only worker/status 取得を抽出した capability 層。

背景 (issue #43): `src/docich/webui.py` は Soren (soviet_now) 固有の pid ファイル
パス・worker 名・game_state.json レイアウトと、UI/HTTP route の配線を同居させて
巨大化していた。本モジュールはその最初の縦切りとして、read-only な

  - `list_workers()` (GET /api/workers が使う worker 一覧+生死+pause 状態)
  - `get_status()`   (GET /api/game_state が使う Soren game_state.json スナップショット)

の2つだけを `RuntimeBackend` という capability interface に抽出する。command
route (POST /api/workers 等の書き込み系) と frontend 分割は対象外 (issue #43 は
read-only 縦切りのみ)。

設計方針:
  - `RuntimeBackend` は実プロセス・実VMに触れずに単体テストできる純粋な interface。
    実装 (`SorenBackend`) だけがファイルシステム/プロセスに触れる。
  - `capabilities()` は対応 capability を明示的に返す。soviet_now が未配置
    (soren_root に eloop_lib.sh が無い) 環境では `SorenBackend` は両 capability
    を False として返し、呼び出し側 (webui.py の route) は明示的に unsupported
    を HTTP レスポンスへ反映する。black-box に例外を伝播させたり、"未配置なのに
    stopped と誤表示する" ことを避けるのが目的。
  - `DocichBackend` は soviet_now に依存しない mock/自リポジトリ用の実装。
    コンストラクタに渡した固定データをそのまま返すだけで、interface が
    Soren 専用ではないことを示す。
"""
from __future__ import annotations

import abc
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

# --- capability 定義 ---------------------------------------------------------

CAPABILITY_LIST_WORKERS = "list_workers"
CAPABILITY_GET_STATUS = "get_status"
ALL_CAPABILITIES = (CAPABILITY_LIST_WORKERS, CAPABILITY_GET_STATUS)


class CapabilityUnsupportedError(RuntimeError):
    """backend がサポートしない capability を呼び出した場合に送出する。

    呼び出し側は本来 `capabilities()` を先に見て分岐すべきであり、この例外は
    「見ずに呼んだ」実装ミスを検出するための安全網として存在する。
    """

    def __init__(self, capability: str):
        super().__init__(f"backend does not support capability: {capability}")
        self.capability = capability


class RuntimeBackend(abc.ABC):
    """read-only worker/status 取得の capability interface。

    実プロセス・実VMに触れずに単体テストできることを前提とする (`DocichBackend`
    がその純粋な例)。soviet_now 側の事情に依存する実装は `SorenBackend` に閉じる。
    """

    @abc.abstractmethod
    def capabilities(self) -> dict[str, bool]:
        """対応 capability の辞書 (`ALL_CAPABILITIES` の各キーに bool) を返す。"""
        raise NotImplementedError

    @abc.abstractmethod
    def list_workers(self) -> list[dict[str, Any]]:
        """worker 一覧 (pid/alive/paused/status) を返す。

        `capabilities()[CAPABILITY_LIST_WORKERS]` が False のときに呼ぶのは
        呼び出し側のバグであり、`CapabilityUnsupportedError` を送出してよい。
        """
        raise NotImplementedError

    @abc.abstractmethod
    def get_status(self) -> dict[str, Any]:
        """ゲーム/ランタイムの状態スナップショットを返す。

        `capabilities()[CAPABILITY_GET_STATUS]` が False のときに呼ぶのは
        呼び出し側のバグであり、`CapabilityUnsupportedError` を送出してよい。
        """
        raise NotImplementedError


# --- DocichBackend (mock / 自リポジトリ用) -----------------------------------


class DocichBackend(RuntimeBackend):
    """soviet_now に依存しない mock 実装。

    実プロセス・実ファイルに一切触れず、コンストラクタで渡した固定データを
    そのまま返す。RuntimeBackend interface の単体テストや、soviet_now を使わない
    docich 単体運用時のプレースホルダとして使う。
    """

    def __init__(
        self,
        workers: list[dict[str, Any]] | None = None,
        status: dict[str, Any] | None = None,
    ):
        self._workers = list(workers) if workers is not None else []
        self._status: dict[str, Any] = (
            dict(status)
            if status is not None
            else {"exists": False, "path": None, "mtime": 0, "data": None, "state": "", "score": None}
        )

    def capabilities(self) -> dict[str, bool]:
        return {CAPABILITY_LIST_WORKERS: True, CAPABILITY_GET_STATUS: True}

    def list_workers(self) -> list[dict[str, Any]]:
        return [dict(w) for w in self._workers]

    def get_status(self) -> dict[str, Any]:
        return dict(self._status)


# --- SorenBackend (soviet_now adapter) ---------------------------------------
#
# 以下のヘルパーは webui.py に直書きされていた Soren 固有ロジック (pid ファイル
# 走査・worker 名・game_state.json レイアウト) をそのまま移設したもの。webui.py
# 側は後方互換のためこれらを `from .runtime_backend import ...` で re-export し、
# 書き込み系 route (POST /api/workers 等、対象外) からも同じ実装を使い続ける。

# 予想 (prediction_worker) / 改善 (improve_daemon) / 配信 (direct_stream) /
# チャット (chat_worker) は supervisor (start_all.sh) の pause gate
# (`tmp/state/<worker>.paused` マーカー) でオンオフする対象。
TOGGLEABLE_WORKERS = ("direct_stream", "chat_worker", "prediction_worker", "improve_daemon")

# GET /api/workers が既定で列挙する worker 名 (pid ファイルが無ければ not_running
# として表示する)。実際に稼働している pidfile は tmp/state/*.pid を後段で
# 追加スキャンして拾う。
_KNOWN_WORKERS = (
    "radio_worker",
    "chat_worker",
    "improve_daemon",
    "audio_worker",
    "prediction_worker",
    "youtube_worker",
)


def _process_is_zombie(pid: int) -> bool:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="ignore")
        # comm は括弧を含み得るため、末尾側の安定フィールドから状態を読む。
        return raw.rsplit(")", 1)[1].split()[0].startswith("Z")
    except OSError:
        pass
    try:
        result = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.stdout.strip().startswith("Z")


def _pid_is_active(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return not _process_is_zombie(pid)


def _find_worker_pid(soren_root: Path, worker: str) -> int | None:
    # worker e.g. "radio_worker" -> pid file tmp/state/radio_worker.pid
    pid_file = soren_root / "tmp/state" / f"{worker}.pid"
    try:
        raw = pid_file.read_text(encoding="utf-8", errors="ignore").strip().splitlines()[0]
        pid = int(raw.strip())
        # check alive
        try:
            os.kill(pid, 0)
            return pid if _pid_is_active(pid) else None
        except ProcessLookupError:
            return None
        except PermissionError:
            return pid
    except Exception:
        return None


def _worker_pause_marker_path(soren_root: Path, worker: str) -> Path:
    if worker not in TOGGLEABLE_WORKERS:
        raise ValueError(f"unsupported worker: {worker}")
    return soren_root / "tmp/state" / f"{worker}.paused"


def _is_worker_paused(soren_root: Path, worker: str) -> bool:
    try:
        return _worker_pause_marker_path(soren_root, worker).is_file()
    except Exception:
        return False


def _set_worker_paused(soren_root: Path, worker: str, paused: bool) -> None:
    marker = _worker_pause_marker_path(soren_root, worker)
    marker.parent.mkdir(parents=True, exist_ok=True)
    if paused:
        payload = json.dumps(
            {"paused": True, "ts": int(time.time()), "source": "webui"},
            ensure_ascii=False,
        )
        tmp = marker.with_name(marker.name + f".tmp{os.getpid()}")
        tmp.write_text(payload + "\n", encoding="utf-8")
        os.replace(tmp, marker)
        try:
            os.chmod(marker, 0o644)
        except OSError:
            pass
    else:
        try:
            marker.unlink()
        except FileNotFoundError:
            pass


def _pid_matches_worker_process(pid: int, worker: str) -> bool:
    """Linux /proc で cmdline を確認し、誤って無関係プロセスを殺さないガード。

    /proc が読めない環境 (macOS 等) は確認不能のため True (許可) を返す。
    判定は start_all.sh の `_pattern_for_worker` と同じ形状 (basename 含む
    スクリプトパス) を Python 正規表現に置き換えたもの。
    """
    patterns: dict[str, str] = {
        "prediction_worker": r"[/ ]workers/prediction_worker\.sh(\s|$)",
        "improve_daemon": r"[/ ]improve_daemon\.sh(\s|$)",
    }
    pat = patterns.get(worker)
    if pat is None:
        return False
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return True
    except Exception:
        return True
    parts = [p.decode("utf-8", "ignore") for p in raw.split(b"\x00") if p]
    if not parts:
        return False
    joined = " ".join(parts)
    return re.search(pat, joined) is not None


def _get_workers_status(soren_root: Path) -> list[dict[str, Any]]:
    workers = list(_KNOWN_WORKERS)
    results: list[dict[str, Any]] = []
    for w in workers:
        pid = _find_worker_pid(soren_root, w)
        if w == "improve_daemon" and pid is None:
            try:
                pf = soren_root / "tmp/state/improve_daemon.pid"
                if pf.is_file():
                    raw = pf.read_text(encoding="utf-8", errors="ignore").strip().splitlines()[0]
                    cand = int(raw.strip())
                    try:
                        os.kill(cand, 0)
                        pid = cand
                    except ProcessLookupError:
                        pid = None
                    except PermissionError:
                        pid = cand
            except Exception:
                pid = None
        alive = pid is not None
        if w in TOGGLEABLE_WORKERS:
            paused = _is_worker_paused(soren_root, w)
            if alive and not paused:
                status = "ok"
            elif paused:
                status = "paused"
            else:
                status = "not_running"
        else:
            paused = False
            status = "ok" if alive else "not_running"
        results.append({"worker": w, "pid": pid, "alive": alive, "paused": bool(paused), "status": status})
    try:
        sdir = soren_root / "tmp/state"
        if sdir.is_dir():
            for p in sdir.glob("*.pid"):
                name = p.stem
                if any(r["worker"] == name for r in results):
                    continue
                pid = None
                try:
                    raw = p.read_text(encoding="utf-8", errors="ignore").strip().splitlines()[0]
                    cand = int(raw.strip())
                    try:
                        os.kill(cand, 0)
                        pid = cand
                    except ProcessLookupError:
                        pid = None
                    except PermissionError:
                        pid = cand
                except Exception:
                    pid = None
                results.append({"worker": name, "pid": pid, "alive": pid is not None, "status": "ok" if pid is not None else "not_running"})
    except Exception:
        pass
    return results


def _read_json_file(path: Path) -> Any | None:
    """webui.py の `_load_json_file` と同等の、汎用の JSON 読み込みヘルパー。

    循環 import を避けるため webui.py 側の実装とは独立に持つ (ロジックは同一)。
    """
    try:
        if not path.is_file():
            return None
        txt = path.read_text(encoding="utf-8", errors="ignore")
        if not txt.strip():
            return None
        return json.loads(txt)
    except Exception:
        return None


def _game_state_path(soren_root: Path) -> Path:
    return soren_root / "game_state.json"


def _read_game_status(soren_root: Path) -> dict[str, Any]:
    path = _game_state_path(soren_root)
    mtime = 0
    try:
        mtime = int(path.stat().st_mtime) if path.is_file() else 0
    except Exception:
        mtime = 0
    data = _read_json_file(path)
    exists = data is not None
    if not exists:
        data = None
    state = ""
    score = None
    if isinstance(data, dict):
        state = str(data.get("state", "") or "")
        score = data.get("score")
    return {"exists": exists, "path": str(path), "mtime": mtime, "data": data, "state": state, "score": score}


def soren_deployed(soren_root: Path) -> bool:
    """soren_root に soviet_now が実際に配置されているかを判定する。

    `eloop_lib.sh` の存在を目印にする (webui.py の起動時警告と同じ基準)。未配置
    (submodule 未 checkout・パス誤り等) なら False。両 capability の可否を
    この 1 関数に集約し、`SorenBackend.capabilities()` と実データ取得メソッドで
    判定がずれないようにする。
    """
    try:
        return soren_root.is_dir() and (soren_root / "eloop_lib.sh").is_file()
    except OSError:
        return False


class SorenBackend(RuntimeBackend):
    """soviet_now (Soren) 運用環境向けの RuntimeBackend adapter。

    webui.py に直書きされていた worker pid ファイル走査・game_state.json 読み取り
    ロジックを移設したもの。soren_root が未配置 (eloop_lib.sh が無い) の場合は
    `capabilities()` で両 capability を False として明示し、`list_workers()` /
    `get_status()` を誤って呼んだ場合は `CapabilityUnsupportedError` を送出する。
    """

    def __init__(self, soren_root: Path):
        self.soren_root = soren_root

    def capabilities(self) -> dict[str, bool]:
        ok = soren_deployed(self.soren_root)
        return {CAPABILITY_LIST_WORKERS: ok, CAPABILITY_GET_STATUS: ok}

    def list_workers(self) -> list[dict[str, Any]]:
        if not soren_deployed(self.soren_root):
            raise CapabilityUnsupportedError(CAPABILITY_LIST_WORKERS)
        return _get_workers_status(self.soren_root)

    def get_status(self) -> dict[str, Any]:
        if not soren_deployed(self.soren_root):
            raise CapabilityUnsupportedError(CAPABILITY_GET_STATUS)
        return _read_game_status(self.soren_root)
