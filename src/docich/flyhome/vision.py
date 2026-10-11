"""ネイティブ解像度 (320x180) の画面から観測を取り出す。

色は Steam ストアのスクリーンショットを 320x180 グリッドで実測した値:

- プレイヤー/コイン/爆発片/押下中の矢印 HUD: オレンジ (255,127,0)
- 顔・コインの縁・文字: 白
- 棘ブロックの縁 (217,0,0) とジェット炎 (251,0,2) は同じ赤、炎の先は黄 (249,255,0)
- 家の屋根: 暗赤 (128,0,0)
- 地形: 草 (32,129,0) / 土 (128,64,0)。夜ステージは暗く沈むので比率で判定する

プレイヤーは 8x14 px のオレンジ (白い顔、灰色のブーツ)。コインは白縁付きのオレンジなので
縁の白さで除外する。スキンを変えると色が変わるため、既定スキンで運用する前提。
"""

from __future__ import annotations

import math
from array import array
from dataclasses import dataclass, field

from .image import Image, game_area
from .settings import Rect, Settings

OTHER, ORANGE, WHITE, RED, YELLOW, GRASS, DIRT, ROOF, DARK = range(9)
LABEL_NAMES = ("other", "orange", "white", "red", "yellow", "grass", "dirt", "roof", "dark")
LABEL_COLORS = (
    (40, 40, 60),
    (255, 127, 0),
    (255, 255, 255),
    (230, 0, 0),
    (250, 250, 0),
    (40, 160, 40),
    (130, 70, 10),
    (120, 0, 60),
    (0, 0, 0),
)


# 既定スキンのプレイヤーはオレンジ 92 px (8x14 から顔と股を除く)。回転してもほぼ一定。
# 爆発片 (<40 px) やメニュー文字を拾わないよう幅を持たせて絞る。
PLAYER_AREA = (60, 150)

# プレイヤーが他の橙 (看板の見本など) と融合したときの塊の面積の上限と、窓内に最低限必要な橙画素数。
MERGED_AREA_MAX = 700
MERGED_MIN_PIXELS = 40

# 追跡中に前フレームからこれ以上離れた候補は別物 (看板の見本など) とみなす (native px)。
TRACK_GATE_PX = 30.0


def classify_rgb(r: int, g: int, b: int) -> int:
    if r > 200 and 90 <= g <= 170 and b < 80:
        return ORANGE
    if r > 200 and g > 200 and b > 200:
        return WHITE
    if r > 170 and g < 60 and b < 60:
        return RED
    if r > 200 and g > 200 and b < 90:
        return YELLOW
    if 95 <= r <= 165 and g < 35 and b < 35:
        return ROOF
    if r < 30 and g < 30 and b < 30:
        return DARK
    if g > 30 and g > 1.4 * r and g > 2.0 * b:
        return GRASS
    if 40 < r < 200 and 0.3 * r < g < 0.7 * r and b < 0.25 * r:
        return DIRT
    return OTHER


def _terrain_mask(labels: bytearray, w: int, h: int) -> bytearray:
    """草花の細い茎や花弁を除き、厚みのある草と土を地形として残す。

    実機Lv49の花は地面の上に描かれる飾りで、茎を壁として膨張させない。
    3x3の厚みを残す処理を整数の1byte/画素の行で行い、毎フレームの遅延を抑える。
    土は細い縁でも実地形なので保持する。
    """
    solid = bytearray(lab in (GRASS, DIRT) for lab in labels)
    rows = [int.from_bytes(solid[y * w:(y + 1) * w], "little") for y in range(h)]
    cores = [0] * h
    for y in range(1, h - 1):
        a, b, c = rows[y - 1:y + 2]
        cores[y] = a & (a << 8) & (a >> 8) & b & (b << 8) & (b >> 8) & c & (c << 8) & (c >> 8)
    keep = bytearray(w * h)
    row_mask = (1 << (8 * w)) - 1
    for y in range(h):
        a = cores[y] | (cores[y - 1] if y else 0) | (cores[y + 1] if y + 1 < h else 0)
        keep[y * w:(y + 1) * w] = ((a | (a << 8) | (a >> 8)) & row_mask & rows[y]).to_bytes(w, "little")
    for i, lab in enumerate(labels):
        if lab == DIRT:
            keep[i] = 1
    return keep


class _LabelCache(dict):
    """24bit 色 → ラベル。ピクセルアートは色数が少ないのでほぼ dict 参照だけで済む。"""

    def __missing__(self, key: int) -> int:
        label = classify_rgb((key >> 16) & 0xFF, (key >> 8) & 0xFF, key & 0xFF)
        if len(self) < 200_000:
            self[key] = label
        return label


_CACHE = _LabelCache()


def label_image(img: Image) -> bytearray:
    n = img.width * img.height
    packed = bytearray(n * 4)
    packed[0::4] = img.data[2::3]
    packed[1::4] = img.data[1::3]
    packed[2::4] = img.data[0::3]
    keys = array("I")
    keys.frombytes(bytes(packed))
    if keys.itemsize != 4:  # pragma: no cover - 32bit unsigned int が無い環境
        raise RuntimeError("array('I') must be 32-bit")
    import sys

    if sys.byteorder == "big":  # pragma: no cover
        keys.byteswap()
    return bytearray(map(_CACHE.__getitem__, keys))


@dataclass
class Blob:
    label: int
    pixels: list[tuple[int, int]]

    @property
    def area(self) -> int:
        return len(self.pixels)

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        xs = [p[0] for p in self.pixels]
        ys = [p[1] for p in self.pixels]
        return min(xs), min(ys), max(xs) + 1, max(ys) + 1

    @property
    def center(self) -> tuple[float, float]:
        n = len(self.pixels)
        return sum(p[0] for p in self.pixels) / n + 0.5, sum(p[1] for p in self.pixels) / n + 0.5


def blobs(labels: bytearray, width: int, height: int, wanted: set[int], *, mask=None) -> list[Blob]:
    """8 近傍の連結成分。mask(x, y) が True の画素は無視する (HUD 等)。"""
    seen = bytearray(width * height)
    out: list[Blob] = []
    for idx, lab in enumerate(labels):
        if lab not in wanted or seen[idx]:
            continue
        x0, y0 = idx % width, idx // width
        if mask is not None and mask(x0, y0):
            seen[idx] = 1
            continue
        stack = [idx]
        seen[idx] = 1
        pix = []
        while stack:
            i = stack.pop()
            x, y = i % width, i // width
            pix.append((x, y))
            for dy in (-1, 0, 1):
                ny = y + dy
                if ny < 0 or ny >= height:
                    continue
                for dx in (-1, 0, 1):
                    nx = x + dx
                    if nx < 0 or nx >= width:
                        continue
                    j = ny * width + nx
                    if not seen[j] and labels[j] == lab and not (mask is not None and mask(nx, ny)):
                        seen[j] = 1
                        stack.append(j)
        out.append(Blob(lab, pix))
    return out


@dataclass
class Player:
    x: float
    y: float
    angle: float | None  # 頭の向き。上=0、時計回り正 (rad)。推定できなければ None
    bbox: tuple[int, int, int, int]
    area: int
    flame_left: bool = False  # 体の左足側に炎がある (体基準)
    flame_right: bool = False


@dataclass
class Observation:
    width: int
    height: int
    player: Player | None
    home: tuple[float, float] | None
    home_bbox: tuple[int, int, int, int] | None
    hazards: list[tuple[int, int, int, int]]
    coins: list[tuple[float, float]]
    hud_left: bool
    hud_right: bool
    timer_sig: int
    level_sig: int
    solid: bytearray = field(repr=False)  # width*height, 1 = 地形 (草/土)
    labels: bytearray = field(repr=False)
    wipe: bool = False  # クリア画面 (上下端に橙のプレイヤー像の列) が出ている
    control_prompt: bool = False  # 実機の左上の操作案内（開始時/死亡後）
    home_text_overlap: bool = False  # HOME重なり中に頭側の連続性を使った
    home_exit_up: tuple[float, float] | None = None  # Actual off-screen HOME arrow; a viewport waypoint only.

    def summary(self) -> dict:
        p = self.player
        return {
            "player": None
            if p is None
            else {
                "x": round(p.x, 2),
                "y": round(p.y, 2),
                "angle_deg": None if p.angle is None else round(math.degrees(p.angle), 1),
                "flame": [p.flame_left, p.flame_right],
            },
            "home": None if self.home is None else [round(v, 1) for v in self.home],
            "up_waypoint": self.home_exit_up,
            "hazards": len(self.hazards),
            "coins": [[round(c[0], 1), round(c[1], 1)] for c in self.coins],
            "hud": [self.hud_left, self.hud_right],
            "timer_sig": self.timer_sig, "control_prompt": self.control_prompt,
            "level_sig": self.level_sig,
        }


def _rect_px(r: Rect, w: int, h: int) -> tuple[int, int, int, int]:
    return int(r[0] * w), int(r[1] * h), int(math.ceil(r[2] * w)), int(math.ceil(r[3] * h))


def _region_sig(labels: bytearray, w: int, rect: tuple[int, int, int, int]) -> int:
    """矩形内の白画素パターンのハッシュ (タイマーの進行検出・レベル番号の識別用)。"""
    x0, y0, x1, y1 = rect
    acc = 0
    for y in range(y0, y1):
        row = y * w
        for x in range(x0, x1):
            acc = (acc * 131 + (1 if labels[row + x] == WHITE else 0)) & 0xFFFFFFFFFFFF
    return acc


def _lit(labels: bytearray, w: int, rect: tuple[int, int, int, int]) -> bool:
    x0, y0, x1, y1 = rect
    total = hit = 0
    for y in range(y0, y1):
        row = y * w
        for x in range(x0, x1):
            total += 1
            if labels[row + x] in (ORANGE, YELLOW):
                hit += 1
    return total > 0 and hit / total > 0.12


def _ring_white_ratio(labels: bytearray, w: int, h: int, bbox: tuple[int, int, int, int]) -> float:
    x0, y0, x1, y1 = bbox
    total = white = 0
    for x in range(x0 - 1, x1 + 1):
        for y in (y0 - 1, y1):
            if 0 <= x < w and 0 <= y < h:
                total += 1
                white += labels[y * w + x] == WHITE
    for y in range(y0, y1):
        for x in (x0 - 1, x1):
            if 0 <= x < w and 0 <= y < h:
                total += 1
                white += labels[y * w + x] == WHITE
    return white / total if total else 0.0


PLANK_RGB = (153, 128, 101)  # 操作説明の看板の板 (実機の体験版 Level 1 で実測)
WIPE_RGB = (217, 235, 244)  # レベル開始/やり直し時のレベル番号の形の白い面。クリアの合図ではない (実機 Lv8 で誤判定)
WIPE_MIN = 400  # 2px おきの標本でこの数以上ならクリア演出とみなす


PARTY_MIN = 800  # クリア画面の上下端の帯に並ぶプレイヤー像の色 (濃い橙) の画素数の下限 (実機 Lv3: 約 3200 / 7680)


def _party_present(img: Image) -> bool:
    """クリア画面か: 上下端 12 行の帯が濃い橙 (≈186–191, 95–104, 0–16) のプレイヤー像で埋まっている。
    通常のプレイ中は空・草・土 (115,64,0) でこの色は出ない。"""
    w, h, data = img.width, img.height, img.data
    n = 0
    for y in list(range(0, min(12, h))) + list(range(max(0, h - 12), h)):
        row = y * w * 3
        for x in range(w):
            i = row + x * 3
            if 170 <= data[i] <= 205 and 85 <= data[i + 1] <= 115 and data[i + 2] <= 30:
                n += 1
    return n >= PARTY_MIN


def _wipe_present(img: Image) -> bool:
    """クリア演出の白い面 (217,235,244) が画面を覆っているか。通常の空・雲とは色が違う。"""
    n = 0
    data, w = img.data, img.width
    for y in range(0, img.height, 2):
        row = y * w * 3
        for x in range(0, w, 2):
            i = row + x * 3
            if abs(data[i] - WIPE_RGB[0]) <= 6 and abs(data[i + 1] - WIPE_RGB[1]) <= 6 and abs(data[i + 2] - WIPE_RGB[2]) <= 6:
                n += 1
    return n >= WIPE_MIN


def _plank_ratio(img: Image, bbox: tuple[int, int, int, int]) -> float:
    """bbox の外側 3 px の枠のうち、看板の板の色が占める割合。"""
    x0, y0, x1, y1 = bbox
    total = plank = 0
    for y in range(max(0, y0 - 3), min(img.height, y1 + 3)):
        for x in range(max(0, x0 - 3), min(img.width, x1 + 3)):
            if x0 <= x < x1 and y0 <= y < y1:
                continue
            i = (y * img.width + x) * 3
            total += 1
            if all(abs(img.data[i + k] - PLANK_RGB[k]) <= 10 for k in range(3)):
                plank += 1
    return plank / total if total else 0.0


def _orientation(blob: Blob, labels: bytearray, w: int, h: int, prev_angle: float | None,
                 *, home_text_overlap: bool = False):
    """主軸 (PCA) で体の向きを、顔 (白) の偏りで頭側を決める。"""
    x0, y0, x1, y1 = blob.bbox
    body = list(blob.pixels)
    body_set = set(body)
    # 実機Lv16: 足元に重なるHOMEの白文字を顔と数えると、直立を180度反転する。
    # 顔は体の橙に囲まれた白画素。背景/文字の端は顔の重心へ混ぜない。
    face = [(x, y) for y in range(max(0, y0), min(h, y1)) for x in range(max(0, x0), min(w, x1))
            if labels[y * w + x] == WHITE and
            sum((x + dx, y + dy) in body_set for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1))) >= 2]
    pts = body + face
    n = len(pts)
    if n < 6:
        return None
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    sxx = sum((p[0] - mx) ** 2 for p in pts) / n
    syy = sum((p[1] - my) ** 2 for p in pts) / n
    sxy = sum((p[0] - mx) * (p[1] - my) for p in pts) / n
    # 長軸方向 (単位ベクトル)
    theta = 0.5 * math.atan2(2 * sxy, sxx - syy)
    ax, ay = math.cos(theta), math.sin(theta)
    elong = (sxx + syy + math.hypot(sxx - syy, 2 * sxy)) / max(1e-6, (sxx + syy - math.hypot(sxx - syy, 2 * sxy)))
    sign = 0.0
    if face:
        fx = sum(p[0] for p in face) / len(face) - mx
        fy = sum(p[1] for p in face) / len(face) - my
        sign = fx * ax + fy * ay
    if (home_text_overlap or abs(sign) < 0.3) and prev_angle is not None:
        # HOME重なりでは文字が体を切り、橙に隣接した白文字も顔と誤認する。
        # PCAの軸は追うが、頭側は前フレームに近い側を採る（実Lv38の180度反転）。
        # 顔が見えない/中央付近の場合も同じ。
        hx, hy = math.sin(prev_angle), -math.cos(prev_angle)
        sign = hx * ax + hy * ay
    if sign == 0.0:
        sign = -ay or 1.0  # 情報が無ければ上向き側
    if sign < 0:
        ax, ay = -ax, -ay
    if elong < 1.3 and prev_angle is not None:
        return prev_angle  # ほぼ円形で軸が決まらない
    return math.atan2(ax, -ay)


def _home_exit_up(labels: bytearray, w: int, h: int) -> tuple[float, float] | None:
    """Match the actual stage 50 upward HOME glyph; never substitute a house."""
    if w != 320 or h != 180:
        return None
    import zlib
    for left in range(8, w - 24):
        center = left + 12
        if not all(labels[y * w + center] == WHITE for y in range(2, 8)):
            continue
        bits = bytes(int(labels[y * w + x] == WHITE)
                     for y in range(1, 17) for x in range(left, left + 24))
        if sum(bits) == 79 and zlib.crc32(bits) == 715799730:
            return float(center), 24.0
    return None


def _control_prompt_present(native: Image) -> bool:
    """実機で採取した操作案内の白いキー/文字の配置。開始時にも出る。"""
    if native.width != 320 or native.height != 180:
        return False
    import zlib
    bits = bytes(int(min(native.get(x, y)) > 200)
                 for y in range(17, 65) for x in range(64))
    return sum(bits) == 352 and zlib.crc32(bits) == 1242029274


def analyze(
    native: Image,
    settings: Settings | None = None,
    *,
    prev_player: tuple[float, float] | None = None,
    prev_angle: float | None = None,
    known_home_bbox: tuple[int, int, int, int] | None = None,
) -> Observation:
    s = settings or Settings()
    w, h = native.width, native.height
    labels = label_image(native)
    hud_rects = [_rect_px(r, w, h) for r in (s.hud.level_text, s.hud.timer, s.hud.arrow_left, s.hud.arrow_right)]

    def in_hud(x: int, y: int) -> bool:
        for x0, y0, x1, y1 in hud_rects:
            if x0 <= x < x1 and y0 <= y < y1:
                return True
        return False

    oranges = [b for b in blobs(labels, w, h, {ORANGE}, mask=in_hud) if b.area >= 4]
    coins: list[tuple[float, float]] = []
    candidates: list[Blob] = []
    oversized: list[Blob] = []
    fragments: list[Blob] = []
    for b in oranges:
        if b.area >= 12 and _ring_white_ratio(labels, w, h, b.bbox) >= 0.35:
            coins.append(b.center)
        elif PLAYER_AREA[0] <= b.area <= PLAYER_AREA[1]:
            candidates.append(b)
        elif PLAYER_AREA[1] < b.area <= MERGED_AREA_MAX:
            oversized.append(b)
        elif b.area < PLAYER_AREA[0]:
            fragments.append(b)  # 花の茎などで体が分断された断片

    # HOMEは屋根の上に固定表示される。姿勢判定の前に屋根を特定する。
    roofs = [b for b in blobs(labels, w, h, {ROOF}, mask=in_hud) if b.area >= 15]
    home_text_rect = None
    text_anchor = known_home_bbox
    if text_anchor is None and roofs:
        roof_box = max(roofs, key=lambda b: b.area).bbox
        if roof_box[2] - roof_box[0] >= 24:
            text_anchor = roof_box
    if text_anchor is not None:
        # 屋根の実観測が途切れても、追跡側で確定した家は動かない。
        rx0, ry0, rx1, _ = text_anchor
        home_text_rect = (rx0 + 6, ry0 - 13, rx1 - 6, ry0 - 4)

    player = None
    overlaps_text = False
    if prev_player is not None and (oversized or fragments) and not any(math.hypot(b.center[0] - prev_player[0], b.center[1] - prev_player[1]) <= TRACK_GATE_PX for b in candidates):
        # 看板の見本 (同じ橙) と重なって大きな塊になった、または花の茎で体が分断された:
        # 前フレームの位置の窓にある橙の画素だけを本体とみなす。
        px, py = prev_player
        pix = [(x, y) for b in oversized + fragments for (x, y) in b.pixels if abs(x + 0.5 - px) <= 10 and abs(y + 0.5 - py) <= 11]
        if len(pix) >= MERGED_MIN_PIXELS:
            candidates.append(Blob(ORANGE, pix))
    if candidates:
        if prev_player is not None:
            # 追跡中は前フレームの近くだけを見る。遠い候補 (看板の見本など) へは飛び移らず、見失った扱いにする。
            near = [b for b in candidates if math.hypot(b.center[0] - prev_player[0], b.center[1] - prev_player[1]) <= TRACK_GATE_PX]
            best = min(near, key=lambda b: (b.center[0] - prev_player[0]) ** 2 + (b.center[1] - prev_player[1]) ** 2 - 4 * b.area) if near else None
        else:
            # レベル 1 の操作説明の看板には、オレンジの見本プレイヤーが描かれている (開始直後は本物より先に現れる)。
            # 周囲が板の色の候補は、唯一の候補でも採らない。
            fresh = [b for b in candidates if _plank_ratio(native, b.bbox) < 0.5]
            best = max(fresh, key=lambda b: b.area) if fresh else None
        if best is not None:
            cx, cy = best.center
            overlaps_text = False
            if home_text_rect is not None:
                tx0, ty0, tx1, ty1 = home_text_rect
                bx0, by0, bx1, by1 = best.bbox
                overlaps_text = bx0 < tx1 and bx1 > tx0 and by0 < ty1 and by1 > ty0
            angle = _orientation(best, labels, w, h, prev_angle,
                                 home_text_overlap=overlaps_text)
            bx0, by0, bx1, by1 = best.bbox
            # 実機Lv49: HUDから1px離れた全身まで姿勢を固定すると、実際の逆回転を見失う。
            clipped_by_hud = any(bx0 < hx1 + 1 and bx1 > hx0 - 1 and by0 < hy1 + 1 and by1 > hy0 - 1
                                 for hx0, hy0, hx1, hy1 in hud_rects)
            if clipped_by_hud and prev_angle is not None:
                # Lv48実機: 下の矢印HUDが体を切り、直立→90度と誤認して強い回転を出した。
                # 隠れた断片のPCAでは向きを決めず、完全な体が再び見えるまで直前の向きを保つ。
                angle = prev_angle
            player = Player(cx, cy, angle, best.bbox, best.area)

    reds = blobs(labels, w, h, {RED, YELLOW}, mask=in_hud)
    hazards: list[tuple[int, int, int, int]] = []
    flames: list[Blob] = []
    for b in reds:
        # 実機Lv41: 炎の255赤が217赤の棘に接続し、棘の箱を上へ広げていた。
        # 棘固有の赤だけで箱を取る。接続した高輝度の炎は別の断片として扱う。
        spike_pixels = [(xx, yy) for xx, yy in b.pixels
                        if 190 <= native.get(xx, yy)[0] <= 230
                        and native.get(xx, yy)[1] < 25 and native.get(xx, yy)[2] < 25]
        if len(spike_pixels) >= 3:
            hazards.append(Blob(RED, spike_pixels).bbox)
            if player is not None:
                hot = [(xx, yy) for xx, yy in b.pixels if native.get(xx, yy)[0] > 230]
                if 0 < len(hot) <= 40:
                    flame = Blob(b.label, hot)
                    bx0, by0, bx1, by1 = flame.bbox
                    px0, py0, px1, py1 = player.bbox
                    if bx0 <= px1 + 6 and bx1 >= px0 - 6 and by0 <= py1 + 6 and by1 >= py0 - 6:
                        flames.append(flame)
            continue
        if player is not None:
            px0, py0, px1, py1 = player.bbox
            bx0, by0, bx1, by1 = b.bbox
            near = bx0 <= px1 + 6 and bx1 >= px0 - 6 and by0 <= py1 + 6 and by1 >= py0 - 6
            spike_red = any(190 <= native.get(xx, yy)[0] <= 230
                            and native.get(xx, yy)[1] < 25 and native.get(xx, yy)[2] < 25
                            for xx, yy in b.pixels) if near and b.area <= 40 else False
            if near and b.area <= 40 and not spike_red:
                flames.append(b)
                continue
        if b.label == RED and b.area >= 3:
            hazards.append(b.bbox)

    if player is not None and flames and player.angle is not None:
        # 体の右方向ベクトル (頭が上のとき画面右)。炎の重心を体の左右に振り分ける。
        rx, ry = math.cos(player.angle), math.sin(player.angle)
        for f in flames:
            fx, fy = f.center
            side = (fx - player.x) * rx + (fy - player.y) * ry
            if side < 0:
                player.flame_left = True
            else:
                player.flame_right = True

    home = home_bbox = None
    if roofs:
        roof = max(roofs, key=lambda b: b.area)
        # 夕焼けの配色のレベル (実機 Lv26) では棘の縁が暗い赤 (140,0,0) で ROOF に分類される。
        # 家の屋根 (一番大きい塊) 以外の、棘ブロック大 (縁の画素 ≤ 60、箱 ≤ 14px) の ROOF の塊は棘とみなす。
        # 実機Lv49: タイマーの下にある暗赤の棘も残った画素から検出する。
        # 文字のHUDマスクはプレイヤーに使い、棘の小さな塊には適用しない。
        for b in blobs(labels, w, h, {ROOF}):
            if b is roof or b.area < 12 or b.area > 60:
                continue
            bx0, by0, bx1, by1 = b.bbox
            if bx1 - bx0 > 14 or by1 - by0 > 14:
                continue
            rx0, ry0, rx1, ry1 = roof.bbox
            if bx0 <= rx1 + 2 and bx1 >= rx0 - 2 and by0 <= ry1 + 2 and by1 >= ry0 - 2:
                continue  # 屋根の一部
            hazards.append(b.bbox)
        x0, y0, x1, y1 = roof.bbox
        rh = y1 - y0
        home_bbox = (x0, y0, x1, min(h, y1 + int(rh * 1.6)))
        home = ((x0 + x1) / 2, y1 + rh * 0.8)

    solid = _terrain_mask(labels, w, h)
    # 家の窓枠/壁の茶色も土の色条件に入るが、通過できる絵である。
    # 確定した家を使い、家の箱の外にある実地形は残す。
    facade = known_home_bbox or home_bbox
    if facade is not None:
        fx0, fy0, fx1, fy1 = facade
        for yy in range(max(0, fy0), min(h, fy1)):
            for xx in range(max(0, fx0), min(w, fx1)):
                if labels[yy * w + xx] == DIRT:
                    solid[yy * w + xx] = 0
    for x0, y0, x1, y1 in hud_rects:
        for y in range(y0, min(h, y1)):
            for x in range(x0, min(w, x1)):
                solid[y * w + x] = 0

    return Observation(
        width=w,
        height=h,
        player=player,
        home=home,
        home_bbox=home_bbox,
        hazards=hazards,
        coins=coins,
        hud_left=_lit(labels, w, _rect_px(s.hud.arrow_left, w, h)),
        hud_right=_lit(labels, w, _rect_px(s.hud.arrow_right, w, h)),
        timer_sig=_region_sig(labels, w, _rect_px(s.hud.timer, w, h)),
        level_sig=_region_sig(labels, w, _rect_px(s.hud.level_text, w, h)),
        solid=solid,
        labels=labels,
        wipe=_party_present(native),
        control_prompt=_control_prompt_present(native),
        home_text_overlap=overlaps_text,
        home_exit_up=_home_exit_up(labels, w, h) if home is None else None,
    )


def to_native(img: Image, settings: Settings | None = None) -> Image:
    """任意解像度のキャプチャ/スクリーンショットを 320x180 のネイティブ画面へ戻す。"""
    s = settings or Settings()
    if (img.width, img.height) == (s.native_width, s.native_height):
        return img
    x0, y0, x1, y1 = game_area(img.width, img.height, s.aspect)
    return img.crop(x0, y0, x1, y1).sample(s.native_width, s.native_height)


def annotate(native: Image, obs: Observation, *, labels_view: bool = False, scale: int = 3) -> Image:
    """解析結果を重ねた確認用画像 (ラベル色 or 元画像 + 枠)。"""
    w, h = native.width, native.height
    if labels_view:
        out = Image(w, h, bytearray(b"".join(bytes(LABEL_COLORS[lab]) for lab in obs.labels)))
    else:
        out = Image(w, h, bytearray(native.data))

    def box(b, rgb):
        x0, y0, x1, y1 = b
        for x in range(x0 - 1, x1 + 1):
            out.put(x, y0 - 1, rgb)
            out.put(x, y1, rgb)
        for y in range(y0 - 1, y1 + 1):
            out.put(x0 - 1, y, rgb)
            out.put(x1, y, rgb)

    for hz in obs.hazards:
        box(hz, (255, 0, 255))
    if obs.home_bbox:
        box(obs.home_bbox, (0, 255, 255))
    for cx, cy in obs.coins:
        box((int(cx) - 2, int(cy) - 2, int(cx) + 2, int(cy) + 2), (255, 255, 0))
    if obs.player:
        p = obs.player
        box(p.bbox, (0, 255, 0))
        if p.angle is not None:
            for t in range(1, 14):
                out.put(int(p.x + math.sin(p.angle) * t), int(p.y - math.cos(p.angle) * t), (0, 255, 0))
    return out.scaled(scale)
