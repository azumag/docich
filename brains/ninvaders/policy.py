"""nInvaders baseline policy: the seed the LLM improvement loop rewrites.

Contract (docich.ninvaders.sandbox): ``decide(obs, state) -> list[str]`` with keys
"Left" / "Right" / "Space".  ``state`` is a dict that persists for one match.
``obs`` comes from the trusted parser (docich.ninvaders.frame.parse):
  player [x, y] (x = cannon centre) | None, player_hit, bombs / missiles /
  aliens (centres) / barriers as [x, y] lists, ufo, score, level, lives, tick, text.

Measured on the real game (2026-09-19):
  * the cannon moves ~2 columns per Left/Right key; one missile at a time;
  * ``:`` is an alien bomb, falling ~8 rows/s (0.8 rows per 0.1 s tick);
  * ``!`` is our own missile (never a threat);
  * a missile fired from under a barrier is wasted and erodes the barrier.
"""
import math

CANNON_HALF = 2          # '/-^-\' spans centre-2 .. centre+2
STEP = 2                 # columns per key press
LEFT_EDGE, RIGHT_EDGE = 2, 77
TICK_S = 0.1
BOMB_ROWS_PER_TICK = 8.2 * TICK_S
REACT_TICKS = 16         # only care about bombs arriving within this many ticks
SAFE_GAP = CANNON_HALF + 1


def _ticks_to_go(bomb_y, cannon_y):
    return max(0.0, (cannon_y - bomb_y) / BOMB_ROWS_PER_TICK)


def _steps(px, nx):
    return int(math.ceil(abs(nx - px) / STEP))


def _dodge(px, py, bombs):
    """(move, active).  Nearest column every incoming bomb misses, reachable in time."""
    threats = []
    for bx, by in bombs:
        if by > py:
            continue
        t = _ticks_to_go(by, py)
        if t <= REACT_TICKS:
            threats.append((bx, t))
    if not threats or all(abs(bx - px) > SAFE_GAP for bx, _ in threats):
        return None, False
    best = None
    for nx in range(LEFT_EDGE, RIGHT_EDGE + 1):
        if any(abs(bx - nx) <= SAFE_GAP for bx, _ in threats):
            continue
        s = _steps(px, nx)
        if any(s + 1 > t for bx, t in threats if abs(bx - px) <= SAFE_GAP):
            continue
        if best is None or s < best[0]:
            best = (s, nx)
    if best is None:  # nowhere safe in time: run from the closest bomb
        bx = min(threats, key=lambda b: abs(b[0] - px))[0]
        if px >= bx and px < RIGHT_EDGE:
            return "Right", True
        return ("Left" if px > LEFT_EDGE else "Right"), True
    _, nx = best
    if nx == px:
        return None, True
    return ("Right" if nx > px else "Left"), True


def _aim(aliens, px):
    if not aliens:
        return None
    # lower aliens are the ones that end the game; near columns are cheaper to reach
    tx, _ = min(aliens, key=lambda a: abs(a[0] - px) - 0.75 * a[1])
    if abs(tx - px) <= 1:
        return None
    return "Right" if tx > px else "Left"


def _blocked_by_barrier(barriers, px, py):
    return any(bx == px and by < py for bx, by in barriers)


def decide(obs, state):
    player = obs.get("player")
    if not player or obs.get("player_hit"):
        return []
    px, py = player
    state["ticks"] = state.get("ticks", 0) + 1
    move, dodging = _dodge(px, py, obs["bombs"])
    if not dodging:
        move = _aim(obs["aliens"], px)
    keys = []
    if move:
        keys.append(move)
    if not obs["missiles"] and not _blocked_by_barrier(obs["barriers"], px, py):
        keys.append("Space")
    return keys
