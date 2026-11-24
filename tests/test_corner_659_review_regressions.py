from dataclasses import replace
from types import SimpleNamespace

from docich.corner_659 import (
    AnimationPlayer, ChartCandle, ChartCornerManager, ChartSnapshot, Execution,
    SignalSide, build_animation_frames, build_audio_script, compute_signals,
)


def snapshot(closes, executions=()):
    candles = tuple(ChartCandle(i, p, p, p, p, 1) for i, p in enumerate(closes))
    return ChartSnapshot("USD/JPY", 100, candles, (), tuple(executions), "fixture")


def test_latest_candle_independent_of_order():
    data = snapshot([100, 101, 98.25])
    reverse = replace(data, candles=data.candles[::-1])
    assert build_audio_script(data, ()) == build_audio_script(reverse, ())
    assert build_animation_frames(reverse, ())[0].price == 98.25


def test_reversal_not_suppressed():
    data = snapshot([100 + i / 100 for i in range(20)] + [99.5], [
        Execution("a", "a", 19.1, 100.20, 1, "buy"),
        Execution("b", "b", 19.2, 100.21, 1, "buy"),
        Execution("c", "c", 20.1, 99.50, 1, "sell"),
        Execution("d", "d", 20.2, 99.49, 1, "sell"),
    ])
    assert [s.side for s in compute_signals(data)] == [
        SignalSide.BUY, SignalSide.HOLD, SignalSide.SELL, SignalSide.HOLD,
    ]
    mirrored = replace(data,
        candles=tuple(replace(c, close=200-c.close) for c in data.candles),
        executions=tuple(replace(e, price=200-e.price) for e in data.executions))
    assert [s.side for s in compute_signals(mirrored)] == [
        SignalSide.SELL, SignalSide.HOLD, SignalSide.BUY, SignalSide.HOLD,
    ]


def test_playback_rebases_epoch_and_limits_fps(tmp_path):
    manager = ChartCornerManager(SimpleNamespace(state_dir=tmp_path), fps=60)
    frames = manager.run("test").animation_frames
    assert len(frames) >= 3
    now = [10.0]
    drawn = []

    def sleep(seconds):
        assert 0 <= seconds <= 1.01
        now[0] += seconds

    player = AnimationPlayer(fps=1, clock=lambda: now[0], sleep=sleep)
    assert player.play(frames[:3], lambda _: drawn.append(now[0])) == 3
    assert drawn == [10., 11., 12.]
    assert player.play(frames[:3], lambda _: None, deadline=12.5) == 1


def test_default_clock_pair_does_not_wait_for_epoch():
    frames = build_animation_frames(snapshot([100]), ())
    def unexpected_sleep(seconds):
        raise AssertionError(seconds)
    assert AnimationPlayer(sleep=unexpected_sleep).play(frames, lambda _: None) == 1
