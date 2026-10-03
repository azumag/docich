"""RGB 画像の最小表現と PNG 読み書き (標準ライブラリのみ)。

Windows 側では Pillow/numpy を前提にしない。PNG は 8bit の RGB/RGBA/グレースケール、
非インターレースのみ対応する (自前で書き出したものと一般的なスクリーンショットを読めれば十分)。
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

_PNG_SIG = b"\x89PNG\r\n\x1a\n"


@dataclass
class Image:
    width: int
    height: int
    data: bytearray  # RGB, row-major, 3 bytes/pixel

    @classmethod
    def blank(cls, width: int, height: int, rgb: tuple[int, int, int] = (0, 0, 0)) -> "Image":
        return cls(width, height, bytearray(bytes(rgb) * (width * height)))

    @classmethod
    def from_bgra(cls, width: int, height: int, buf: bytes, *, bottom_up: bool = False) -> "Image":
        """GDI の 32bit DIB (BGRA) から変換する。"""
        out = bytearray(width * height * 3)
        # チャネルの並べ替えはスライス代入で行う (Python ループより大幅に速い)。
        if bottom_up:
            rows = [buf[(height - 1 - y) * width * 4 : (height - y) * width * 4] for y in range(height)]
            buf = b"".join(rows)
        out[0::3] = buf[2::4]
        out[1::3] = buf[1::4]
        out[2::3] = buf[0::4]
        return cls(width, height, out)

    def get(self, x: int, y: int) -> tuple[int, int, int]:
        i = (y * self.width + x) * 3
        d = self.data
        return d[i], d[i + 1], d[i + 2]

    def put(self, x: int, y: int, rgb: tuple[int, int, int]) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            i = (y * self.width + x) * 3
            self.data[i : i + 3] = bytes(rgb)

    def fill_rect(self, x0: int, y0: int, x1: int, y1: int, rgb: tuple[int, int, int]) -> None:
        for y in range(max(0, y0), min(self.height, y1)):
            for x in range(max(0, x0), min(self.width, x1)):
                self.put(x, y, rgb)

    def crop(self, x0: int, y0: int, x1: int, y1: int) -> "Image":
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(self.width, x1), min(self.height, y1)
        w, h = x1 - x0, y1 - y0
        out = bytearray()
        for y in range(y0, y1):
            i = (y * self.width + x0) * 3
            out += self.data[i : i + w * 3]
        return Image(w, h, out)

    def sample(self, out_w: int, out_h: int) -> "Image":
        """最近傍 (各セル中心) で縮小する。ピクセルアートの拡大表示を元解像度へ戻す用途。"""
        out = bytearray(out_w * out_h * 3)
        sx = self.width / out_w
        sy = self.height / out_h
        xs = [min(self.width - 1, int((x + 0.5) * sx)) * 3 for x in range(out_w)]
        o = 0
        for y in range(out_h):
            row = min(self.height - 1, int((y + 0.5) * sy)) * self.width * 3
            d = self.data
            for xi in xs:
                i = row + xi
                out[o] = d[i]
                out[o + 1] = d[i + 1]
                out[o + 2] = d[i + 2]
                o += 3
        return Image(out_w, out_h, out)

    def scaled(self, factor: int) -> "Image":
        """整数倍の最近傍拡大 (注釈画像を見やすくする用途)。"""
        w, h = self.width * factor, self.height * factor
        out = bytearray()
        for y in range(self.height):
            row = bytearray()
            for x in range(self.width):
                i = (y * self.width + x) * 3
                row += self.data[i : i + 3] * factor
            out += bytes(row) * factor
        return Image(w, h, out)


def game_area(width: int, height: int, aspect: tuple[int, int] = (16, 9)) -> tuple[int, int, int, int]:
    """黒帯 (レターボックス/ピラーボックス) を除いたゲーム描画領域 (x0, y0, x1, y1)。

    フルスクリーンで 16:10 等のモニタに出すと上下に黒帯が付く (Steam のスクリーンショットは
    1728x1080 中の 1728x972)。描画は中央・縦横比維持なので、比率だけで決定的に求める。
    """
    aw, ah = aspect
    if width * ah > height * aw:  # 横長 → 左右に帯
        gw = round(height * aw / ah)
        x0 = (width - gw) // 2
        return x0, 0, x0 + gw, height
    gh = round(width * ah / aw)
    y0 = (height - gh) // 2
    return 0, y0, width, y0 + gh


def _chunk(tag: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)


def encode_png(img: Image) -> bytes:
    raw = bytearray()
    stride = img.width * 3
    for y in range(img.height):
        raw.append(0)
        raw += img.data[y * stride : (y + 1) * stride]
    ihdr = struct.pack(">IIBBBBB", img.width, img.height, 8, 2, 0, 0, 0)
    return _PNG_SIG + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + _chunk(b"IEND", b"")


def write_png(path: str | Path, img: Image) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(encode_png(img))


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def decode_png(blob: bytes) -> Image:
    if not blob.startswith(_PNG_SIG):
        raise ValueError("not a PNG file")
    pos = len(_PNG_SIG)
    idat = bytearray()
    width = height = depth = ctype = interlace = None
    palette = None
    while pos < len(blob):
        (length,) = struct.unpack(">I", blob[pos : pos + 4])
        tag = blob[pos + 4 : pos + 8]
        payload = blob[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if tag == b"IHDR":
            width, height, depth, ctype, _comp, _filt, interlace = struct.unpack(">IIBBBBB", payload)
        elif tag == b"PLTE":
            palette = payload
        elif tag == b"IDAT":
            idat += payload
        elif tag == b"IEND":
            break
    if width is None:
        raise ValueError("PNG without IHDR")
    if depth != 8 or interlace != 0:
        raise ValueError(f"unsupported PNG (bit depth {depth}, interlace {interlace})")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(ctype)
    if channels is None:
        raise ValueError(f"unsupported PNG color type {ctype}")
    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    prev = bytearray(stride)
    out = bytearray(width * height * 3)
    p = 0
    for y in range(height):
        ftype = raw[p]
        line = bytearray(raw[p + 1 : p + 1 + stride])
        p += 1 + stride
        for i in range(stride):
            a = line[i - channels] if i >= channels else 0
            b = prev[i]
            c = prev[i - channels] if i >= channels else 0
            if ftype == 1:
                line[i] = (line[i] + a) & 0xFF
            elif ftype == 2:
                line[i] = (line[i] + b) & 0xFF
            elif ftype == 3:
                line[i] = (line[i] + ((a + b) >> 1)) & 0xFF
            elif ftype == 4:
                line[i] = (line[i] + _paeth(a, b, c)) & 0xFF
        prev = line
        o = y * width * 3
        for x in range(width):
            j = x * channels
            if ctype in (2, 6):
                out[o : o + 3] = line[j : j + 3]
            elif ctype == 3:
                k = line[j] * 3
                out[o : o + 3] = palette[k : k + 3] if palette else bytes(3)
            else:  # grayscale (+alpha)
                out[o : o + 3] = bytes((line[j],)) * 3
            o += 3
    return Image(width, height, out)


def read_png(path: str | Path) -> Image:
    return decode_png(Path(path).read_bytes())
