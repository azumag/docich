from pathlib import Path

def sub(path, old, new, count=0):
    p = Path(path)
    t = p.read_text()
    if old not in t:
        print('MISS', path)
        return
    t = t.replace(old, new) if count == 0 else t.replace(old, new, count)
    p.write_text(t)
    print('ok', path)

sub('src/docich/corner_rotation.py',
    '{"game_over", "screen_stalled", "input_stalled"}',
    '{"game_over", "screen_stalled", "input_stalled", "game_clear"}')
sub('src/docich/retro_corner.py',
    "{'game_over', 'screen_stalled', 'input_stalled'}",
    "{'game_over', 'screen_stalled', 'input_stalled', 'game_clear'}")
sub('src/docich/retro_corner.py',
    '{"game_over", "screen_stalled", "input_stalled"}',
    '{"game_over", "screen_stalled", "input_stalled", "game_clear"}')
sub('src/docich/hanjuku_log_retention.py',
    "frozenset({'game_over', 'screen_stalled', 'input_stalled'})",
    "frozenset({'game_over', 'screen_stalled', 'input_stalled', 'game_clear'})")
