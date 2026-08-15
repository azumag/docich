"""運用強化: フリーズ検知 + window 消失復旧 (architecture.md §10 Phase 3)。

`docich run watchdog` (= tmux の watchdog window) から
`supervise.run_callable_loop` に載せて回される。純ロジックである
`FreezeDetector` / `next_rotation_game` は X11/tmux に依存せず単体テスト可能。
"""
from __future__ import annotations

import hashlib
import subprocess
import time
from pathlib import Path

from .config import GlobalConfig
from .state import State
from .tmux import Tmux
from .xkit import XKit


class FreezeDetector:
    """連続する同一スクリーンショットからゲームのフリーズを検知する。

    `feed()` に毎周期のスクリーンショット digest (例: sha256 hexdigest) を渡す。

    - digest が None (撮影失敗など)、または直前の digest と異なる場合は
      「フリーズしていない」とみなしてカウンタをリセットする。
    - 同一の digest が threshold 回「連続」した、その瞬間の feed() 呼び出しだけ
      True を返す。
    - True を返した直後にカウンタは 0 に戻る。remedy (ゲーム切替) が効かず
      同じ digest が続いても、次に True が返るのはそこから更に threshold 回
      同一 digest が連続した時点であり、True を連発することはない
      (= 「remedy 後の再判定には新たに threshold 回必要」)。digest が変化した
      場合も同様に、新しい digest で threshold 回連続するまで True は返らない。
    """

    def __init__(self, threshold: int):
        self._threshold = threshold
        self._last_digest: str | None = None
        self._count = 0

    def feed(self, digest: str | None) -> bool:
        if digest is None or digest != self._last_digest:
            # 変化 (None を含む) はフリーズしていない証拠なのでカウンタをリセットする。
            self._last_digest = digest
            self._count = 1 if digest is not None else 0
            return False
        self._count += 1
        if self._count >= self._threshold:
            # 1回だけ True を返し、次の判定のためにカウンタを 0 から数え直す。
            self._count = 0
            return True
        return False


def next_rotation_game(games: list[str], current: str | None) -> str:
    """`rotation.games` の中で `current` の次のゲーム名を返す (末尾は先頭へ wrap)。

    current が None、または games に含まれない場合は games[0] を返す
    (未起動状態からの `docich rotate` や、rotation.games の変更で current が
    リストから外れた場合の復帰動作)。games が空リストの場合は ValueError。
    """
    if not games:
        raise ValueError("games リストが空です")
    if current is None or current not in games:
        return games[0]
    idx = games.index(current)
    return games[(idx + 1) % len(games)]


def _docich_bin() -> str:
    # cli.py の _docich_bin()/_repo_root() と同じ導出方法。ここで cli を import
    # すると (cli -> watchdog -> cli の) 循環importになるため、独立に計算する。
    return str(Path(__file__).resolve().parents[2] / "bin" / "docich")


def _run_remedy(g: GlobalConfig, *args: str) -> int:
    """[bin/docich, --config, <config_path>, *args] を実行し returncode を返す。

    標準出力・標準エラーはリダイレクトせず親 (watchdog window) にそのまま
    継承させる。remedy コマンド自身の `docich: ...` メッセージがそのまま
    watchdog window のログとして残るため、returncode 以外の詳細を watchdog
    側で改めて捕捉・整形する必要がない。
    """
    argv = [_docich_bin(), "--config", str(g.config_path), *args]
    result = subprocess.run(argv)
    return result.returncode


def _check_freeze(
    g: GlobalConfig,
    state: State,
    tmux: Tmux,
    xkit: XKit,
    detector: FreezeDetector,
    frame_path: Path,
) -> None:
    """点検1: フリーズ検知。

    「game window があり、state.current_game() があり、かつ agent window も
    起動中」の3条件が揃ったときだけ実施する。agent が act していない (=
    agent.enabled=false のゲームや、agent 未起動の状態) では画面が長時間
    静止しているのが正常であり、これをフリーズと誤検知してしまうため。
    """
    current = state.current_game()
    if not (tmux.has_window("game") and current is not None and tmux.has_window("agent")):
        return

    try:
        # state.screenshots_dir (アダプタの observe() が使う latest.png) とは
        # 別ディレクトリに撮ることで、agent ループの screenshot と衝突しない。
        xkit.screenshot(frame_path, g.display.width, g.display.height)
        digest: str | None = hashlib.sha256(frame_path.read_bytes()).hexdigest()
    except subprocess.CalledProcessError:
        # 撮影失敗はフリーズと無関係な一時的な問題である可能性が高い。
        # digest=None として feed し (カウンタをリセットしつつ) 次周期へ続行する。
        digest = None

    if not detector.feed(digest):
        return

    print(
        f"[watchdog] フリーズを検知しました "
        f"(game={current}, 連続同一画面={g.watchdog.freeze_cycles}回)",
        flush=True,
    )
    rc = _run_remedy(g, "switch", current)
    print(f"[watchdog] remedy: switch {current} -> returncode={rc}", flush=True)


def _check_windows(g: GlobalConfig, tmux: Tmux) -> None:
    """点検2: window 消失復旧 (recover_windows=true のときのみ呼ばれる)。

    display window が無い / audio.enabled なのに audio window が無い /
    stream.mode != "null" なのに stream window が無い、のいずれかであれば
    `up` を実行する (up は既存実装で冪等: 既に起動している window には
    触れない)。watchdog 自身の window はここでは扱わない。
    """
    missing = []
    if not tmux.has_window("display"):
        missing.append("display")
    if g.audio.enabled and not tmux.has_window("audio"):
        missing.append("audio")
    if g.stream.mode != "null" and not tmux.has_window("stream"):
        missing.append("stream")
    if not missing:
        return

    print(f"[watchdog] window消失を検知しました: {', '.join(missing)} -> up を実行します", flush=True)
    rc = _run_remedy(g, "up")
    print(f"[watchdog] remedy: up -> returncode={rc}", flush=True)


def run_watchdog(g: GlobalConfig) -> None:
    """`docich run watchdog` の本体。interval_s ごとに点検1・点検2を繰り返す。

    supervise.run_callable_loop 配下で動くことを前提に、この関数自体は
    (シグナルによる sys.exit を除き) 戻らない while True ループを持つ。
    1周ごとの点検内容は try/except で囲み、一時的なエラーで watchdog 全体が
    再起動してカウンタ状態を失わないようにする (agent/loop.py の
    run_agent と同じ方針)。
    """
    state = State(g)
    state.ensure()
    tmux = Tmux()
    xkit = XKit(g.display.name)

    watchdog_dir = state.state_dir / "watchdog"
    watchdog_dir.mkdir(parents=True, exist_ok=True)
    frame_path = watchdog_dir / "frame.png"

    detector = FreezeDetector(g.watchdog.freeze_cycles)

    print(
        f"[watchdog] 監視を開始しました "
        f"(interval_s={g.watchdog.interval_s}, freeze_cycles={g.watchdog.freeze_cycles}, "
        f"recover_windows={g.watchdog.recover_windows})",
        flush=True,
    )

    while True:
        time.sleep(g.watchdog.interval_s)
        try:
            _check_freeze(g, state, tmux, xkit, detector, frame_path)
            if g.watchdog.recover_windows:
                _check_windows(g, tmux)
        except Exception as exc:
            # 静かな運用が方針 (異常時のみ出力)。ここでの例外は catch-log-continue
            # とし、run_callable_loop 側の backoff・再起動には委ねない
            # (委ねると FreezeDetector の連続カウントが毎回リセットされてしまう)。
            print(f"[watchdog] 警告: 点検中にエラーが発生しました: {exc}", flush=True)
