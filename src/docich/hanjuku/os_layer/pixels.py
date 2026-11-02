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
        data = bytearray()
        for y in range(height):
            sy = min(self.height-1, (2*y+1)*self.height//(2*height))
            for x in range(width):
                sx = min(self.width-1, (2*x+1)*self.width//(2*width))
                i = (sy*self.width+sx)*3
                data.extend(self.rgb[i:i+3])
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
