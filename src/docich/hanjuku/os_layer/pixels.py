"""Bounded RGB PNG reader for the token-free Hanjuku bot (stdlib only)."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
from pathlib import Path
import struct
import zlib


@dataclass(frozen=True)
class Frame:
    width: int
    height: int
    rgb: bytes

    def pixel(self, x: int, y: int) -> tuple[int, int, int]:
        i = (y * self.width + x) * 3
        return tuple(self.rgb[i:i+3])

    def resized(self, width=256, height=224):
        if self.width == width and self.height == height:
            return self
        # 面積重み付きの2クラスタ平均（コントラスト保持型ボックスフィルタ）で
        # リサンプリングする。
        # 旧実装の最近傍サンプリングは 299x224 -> 256x224 のような非整数倍の縮小で
        # ソース列を等間隔に間引くため、フォントの縦線が間引き位置に落ちると
        # 文字が潰れて OCR が誤読する（チコリ HP 18 -> 8、ハードロボ 610 -> 0）。
        # 単純なボックス平均は列欠落を防げるが、明と暗が混ざる画素に
        # ソースに存在しない中間調を作り、ビットマップ照合型のフォントOCRを
        # 壊す（後述）。そこで各出力画素では、覆うソース画素を輝度で2群に
        # 分け、重み多数決側だけを面積重み付きで平均する。少数側のにじみを
        # 排して文字エッジのコントラストを保ちつつ、間引きはしないため
        # 列欠落も起きない。平坦領域では通常の面積平均と同じ結果になる。
        # 座標計算は浮動小数点を使わず、分母を出力解像度とした整数の分子で扱い、
        # 丸め誤差を完全に排除する（stdlib オンリー・決定論的であることが要件）。
        # 拡大時も同じ式で、各出力画素が覆う範囲がほぼ1ソース画素に収まるため
        # 実質最近傍相当の結果になる。
        x_spans = _resize_axis(self.width, width)
        y_spans = _resize_axis(self.height, height)
        data = bytearray(width * height * 3)
        o = 0
        for y_pairs in y_spans:
            for x_pairs in x_spans:
                # At native capture height (299x224 -> 256x224), each
                # output pixel covers one or two source pixels. For two
                # different luminances the weighted mean lies between them,
                # so the majority cluster is simply the heavier sample;
                # equal weights choose the brighter one. Preserve the exact
                # weighted colour average when luminances are equal.
                if len(y_pairs) == 1 and len(x_pairs) <= 2:
                    base = y_pairs[0][0] * self.width * 3
                    sx, w0 = x_pairs[0]
                    i = base + sx * 3
                    if len(x_pairs) == 1:
                        data[o:o+3] = self.rgb[i:i+3]
                    else:
                        sx, w1 = x_pairs[1]
                        j = base + sx * 3
                        l0 = self.rgb[i] + self.rgb[i+1] + self.rgb[i+2]
                        l1 = self.rgb[j] + self.rgb[j+1] + self.rgb[j+2]
                        if l0 == l1:
                            total = w0 + w1
                            half = total // 2
                            for channel in range(3):
                                data[o+channel] = (self.rgb[i+channel] * w0
                                                   + self.rgb[j+channel] * w1 + half) // total
                        else:
                            chosen = i if (w0 > w1 or w0 == w1 and l0 > l1) else j
                            data[o:o+3] = self.rgb[chosen:chosen+3]
                    o += 3
                    continue
                # 寄与するソース画素と重みを集める。
                contribs = []
                for sy, wy in y_pairs:
                    base = sy * self.width * 3
                    for sx, wx in x_pairs:
                        i = base + sx * 3
                        contribs.append((i, wx * wy))
                # まず全面積平均を計算し、その輝度を境に寄与画素を
                # 明側・暗側の2クラスタに分ける。重みの大きい側の
                # クラスタだけで加重平均を取り直す。
                # 理由: ボックス平均をそのまま出力すると、文字の縁のように
                # 明と暗が混ざる画素で「ソースに存在しない中間調」が生まれ、
                # ビットマップ照合型のフォントOCRや色帯判定が壊れる
                # （g698-resume-starter.png のstarter一覧、g1097-fort-menu
                # のメニュー文字とドーム色で実測）。多数決側だけで平均すれば
                # 少数側（背景または細い輪郭）のにじみを排しつつ、クラスタ内は
                # 平滑化されるため、最近傍より列欠落しにくい。
                # 1クラスタしかない平坦領域では全面積平均と同じ結果になる。
                tot = sum(w for _, w in contribs)
                mlum = sum((self.rgb[i] + self.rgb[i+1] + self.rgb[i+2]) * w
                           for i, w in contribs)
                hw = lw = 0
                for i, w in contribs:
                    if (self.rgb[i] + self.rgb[i+1] + self.rgb[i+2]) * tot >= mlum:
                        hw += w
                    else:
                        lw += w
                use_hi = hw >= lw
                r = g = b = total = 0
                for i, w in contribs:
                    lum = self.rgb[i] + self.rgb[i+1] + self.rgb[i+2]
                    if (lum * tot >= mlum) != use_hi:
                        continue
                    r += self.rgb[i] * w
                    g += self.rgb[i+1] * w
                    b += self.rgb[i+2] * w
                    total += w
                # 重み総和は必ず正（多数決側は重み総和の半分以上を持つ）。
                # +total//2 で四捨五入し、輝度の系統的な暗転を防ぐ。
                half = total // 2
                data[o] = (r + half) // total
                data[o+1] = (g + half) // total
                data[o+2] = (b + half) // total
                o += 3
        return Frame(width,height,bytes(data))

    def digest(self) -> str:
        return hashlib.sha256(self.rgb).hexdigest()

    def fraction(self, rect, predicate) -> float:
        x0,y0,x1,y1 = rect
        samples = [self.pixel(x,y) for y in range(y0,y1,2) for x in range(x0,x1,2)]
        return sum(predicate(*p) for p in samples)/max(1,len(samples))

    def png_bytes(self) -> bytes:
        def chunk(kind,payload):
            return (struct.pack('>I',len(payload))+kind+payload
                    +struct.pack('>I',zlib.crc32(kind+payload)&0xffffffff))
        stride=self.width*3
        raw=b''.join(b'\0'+self.rgb[y*stride:(y+1)*stride] for y in range(self.height))
        return (b'\x89PNG\r\n\x1a\n'
                +chunk(b'IHDR',struct.pack('>IIBBBBB',self.width,self.height,8,2,0,0,0))
                +chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b''))


@lru_cache(maxsize=16)
def _resize_axis(src_len: int, dst_len: int):
    """Immutable geometry only; never cache observations or recognition."""
    spans = []
    for d in range(dst_len):
        lo, hi = d * src_len, (d + 1) * src_len
        pairs = []
        i = lo // dst_len
        while i * dst_len < hi and i < src_len:
            overlap = min(hi, (i + 1) * dst_len) - max(lo, i * dst_len)
            pairs.append((i, overlap))
            i += 1
        spans.append(tuple(pairs))
    return tuple(spans)


@lru_cache(maxsize=128)
def _band_table(low: int, high: int) -> bytes:
    return bytes(0x31 if low <= value <= high else 0x30 for value in range(256))


def band_bits(frame: Frame, red, green, blue) -> bytes:
    """One ASCII '1'/'0' per pixel: '1' when every channel is in its (low, high) band.

    Scanning a frame with a Python predicate costs millions of calls per
    parsed screen. ``bytes.translate`` plus one big-integer AND does the same
    comparison in C, and the ASCII digits convert straight into row masks with
    ``int(row, 2)``. Scan order matches a y-then-x loop over the frame.
    """
    pixels = frame.width * frame.height
    if len(frame.rgb) < pixels * 3:
        raise IndexError('frame has fewer bytes than its dimensions')
    rgb = bytes(frame.rgb[:pixels * 3])
    channels = [int.from_bytes(rgb[start::3].translate(_band_table(*band)), 'big')
                for start, band in enumerate((red, green, blue))]
    return (channels[0] & channels[1] & channels[2]).to_bytes(pixels, 'big')


def near_bits(frame: Frame, colour, tolerance: int = 0) -> bytes:
    """``band_bits`` for ``abs(channel - colour[i]) <= tolerance`` on every channel."""
    return band_bits(frame, *((value - tolerance, value + tolerance) for value in colour))


def union_bits(first: bytes, *rest: bytes) -> bytes:
    """Per-pixel OR of equally long ``band_bits`` results."""
    value = int.from_bytes(first, 'big')
    for other in rest:
        value |= int.from_bytes(other, 'big')
    return value.to_bytes(len(first), 'big')


def bit_points(frame: Frame, bits: bytes) -> list[tuple[int, int]]:
    """(x, y) of every set pixel of a ``band_bits`` result, in scan order."""
    points, width = [], frame.width
    index = bits.find(0x31)
    while index >= 0:
        points.append((index % width, index // width))
        index = bits.find(0x31, index + 1)
    return points


def read_png(path: Path) -> Frame:
    """Decode only non-interlaced 8-bit RGB/RGBA produced by our capture path."""
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16*1024*1024:
        raise ValueError('invalid screenshot file')
    data = path.read_bytes()
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('invalid PNG signature')
    offset=8
    compressed=bytearray()
    width=height=channels=0
    while offset+12 <= len(data):
        size,kind=struct.unpack('>I4s',data[offset:offset+8])
        end=offset+8+size
        if size>16*1024*1024 or end+4>len(data):
            raise ValueError('invalid PNG chunk')
        payload=data[offset+8:end]
        if zlib.crc32(kind+payload) & 0xffffffff != struct.unpack('>I',data[end:end+4])[0]:
            raise ValueError('PNG CRC mismatch')
        if kind==b'IHDR':
            width,height,depth,color,compression,filtering,interlace=struct.unpack('>IIBBBBB',payload)
            if not (0<width<=4096 and 0<height<=2160 and depth==8 and color in (2,6)
                    and compression==filtering==interlace==0):
                raise ValueError('unsupported PNG format')
            channels=3 if color==2 else 4
        elif kind==b'IDAT':
            compressed.extend(payload)
        elif kind==b'IEND':
            break
        offset=end+4
    if not channels:
        raise ValueError('missing PNG header')
    stride=width*channels
    expected=(stride+1)*height
    decoder=zlib.decompressobj()
    raw=decoder.decompress(compressed, expected+1)
    if len(raw)!=expected or not decoder.eof:
        raise ValueError('invalid PNG pixel size')
    rgb=bytearray()
    previous=bytearray(stride)
    for y in range(height):
        start=y*(stride+1)
        mode=raw[start]
        row=bytearray(raw[start+1:start+1+stride])
        if mode not in range(5):
            raise ValueError('invalid PNG filter')
        # FFmpeg screenshots use filter 0: the row already contains decoded
        # bytes. Avoid millions of Python iterations per native screenshot.
        for i in range(stride) if mode else ():
            left=row[i-channels] if i>=channels else 0
            up=previous[i]
            upper_left=previous[i-channels] if i>=channels else 0
            if mode==1: predictor=left
            elif mode==2: predictor=up
            elif mode==3: predictor=(left+up)//2
            elif mode==4:
                p=left+up-upper_left
                distances=(abs(p-left),abs(p-up),abs(p-upper_left))
                predictor=(left,up,upper_left)[distances.index(min(distances))]
            else: predictor=0
            row[i]=(row[i]+predictor)&255
        if channels==3:
            rgb.extend(row)
        else:
            for i in range(0,stride,4): rgb.extend(row[i:i+3])
        previous=row
    return Frame(width,height,bytes(rgb))
