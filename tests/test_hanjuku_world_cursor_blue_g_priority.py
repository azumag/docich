"""Blue G box cursor takes priority over unrelated false-gold unit clusters,
4-corner gold boxes beat 2-corner clusters, and min-step Y-jump oscillation
settles immediately within Y_JUMP_FINAL_TOL (#4859).
"""
from docich.hanjuku.os_layer import navigation as nav
from docich.hanjuku.os_layer import readers as R


class _Canvas:
    def __init__(self, width=256, height=224):
        self.width = width
        self.height = height
        self._px = {}

    def set(self, x, y, rgb):
        self._px[(x, y)] = rgb

    def fill_rect(self, x0, y0, w, h, rgb):
        for x in range(x0, x0 + w):
            for y in range(y0, y0 + h):
                self._px[(x, y)] = rgb

    def pixel(self, x, y):
        return self._px.get((x, y), (0, 0, 0))


GOLD = (255, 182, 0)
BLUE = (33, 66, 255)


def test_blue_g_cursor_beats_unrelated_two_corner_false_gold_cluster():
    canvas = _Canvas()
    # Two diagonal gold unit ▲ blobs at (124, 64) and (135, 76): 100% corner
    # fill across 2 of 4 corners, matching the live (131.5, 70.0) false gold.
    canvas.fill_rect(124, 64, 3, 3, GOLD)
    canvas.fill_rect(135, 75, 3, 3, GOLD)
    # Real blue "G" box cursor at (130..140, 95..105) -> centre (135.0, 100.0).
    canvas.fill_rect(130, 95, 11, 11, BLUE)
    assert R.world_cursor(canvas, chapter=9) == (135.0, 100.0)


def test_four_corner_gold_box_beats_two_corner_false_gold_without_blue_g():
    canvas = _Canvas()
    # High-pixel-count 2-corner false cluster (18 pixels, ratio 1.0, only 2 corners).
    canvas.fill_rect(142, 89, 3, 3, GOLD)
    canvas.fill_rect(157, 104, 3, 3, GOLD)
    # Real 4-corner gold bracket box (16 pixels, all 4 corners occupied) around (100, 100).
    for cx, cy in ((94, 94), (105, 94), (94, 105), (105, 105)):
        canvas.fill_rect(cx, cy, 2, 2, GOLD)
    assert R.world_cursor(canvas, chapter=9) == (100.0, 100.0)


def test_y_jump_min_step_straddle_confirms_without_waiting_64_moves(monkeypatch):
    goal_world = (568, 212)
    ox, oy = nav.Y_JUMP_OFFSET[9]
    goal_view = [goal_world[0] / 8 + ox, goal_world[1] / 8 + oy]
    # Cursor straddles goal by 5.0 px per minimum 1-frame tap (+2.5 <-> -2.5),
    # which can never enter Y_JUMP_TOL (1.0) but is within Y_JUMP_FINAL_TOL (3.0).
    pos = [goal_view[0] - 2.5, goal_view[1]]

    monkeypatch.setattr(nav, 'boss_probe_cell', lambda mem, goal, step=None: goal_world)
    monkeypatch.setattr(nav, 'world_cursor', lambda frame, chapter=None: tuple(pos))
    monkeypatch.setattr(nav, 'world_flags', lambda *a, **k: {})

    class DummyFrame:
        def __init__(self, idx):
            self._idx = idx

        def digest(self):
            return f'f-{self._idx}'

    mem = {
        'chapter': 9,
        'tick': 100,
        '_records': [],
        'y_jump': {'goal': 'ボス', 'mode': 'target', 'step': 'A:1:M1', 'wait': 0, 'moves': 0},
    }
    confirmed_at = None
    for idx in range(10):
        acts = nav._y_jump_step(mem, DummyFrame(idx))
        if any(r['decision'] == 'y_jump_confirm' for r in mem['_records']):
            confirmed_at = idx
            break
        for act in acts:
            step_px = max(1, round(act['hold_ms'] * 60 / 1000)) * 5.0
            btn = act['buttons'][0]
            if btn == 'right':
                pos[0] += step_px
            elif btn == 'left':
                pos[0] -= step_px

    assert confirmed_at is not None and confirmed_at <= 6
    assert not any(r['decision'] == 'y_jump_failed' for r in mem['_records'])
