from pathlib import Path

def sub(path, old, new):
    p = Path(path)
    t = p.read_text()
    if old not in t:
        print('MISS', path); return
    p.write_text(t.replace(old, new, 1))
    print('ok', path)

sub('src/docich/soren_corner_stats.py',
    '"game_over", "screen_stalled", "manual_saved_stop"',
    '"game_over", "game_clear", "screen_stalled", "manual_saved_stop"')
sub('src/docich/soren_corner_text.py',
    '"game_over": "game over", "screen_stalled": "screen stalled",',
    '"game_over": "game over", "game_clear": "cleared", "screen_stalled": "screen stalled",')
sub('ops/vm_actions/collect_diagnostics.py',
    '"switch-terminal-before-corner-active", "game-completed",',
    '"switch-terminal-before-corner-active", "game-completed", "game_clear",')
