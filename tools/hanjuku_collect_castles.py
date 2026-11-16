#!/usr/bin/env python3
"""Extract 30 labelled castle references from a supported, user-supplied ROM.

Read-only and offline: does not start an emulator or touch saves. Pillow is used
only for the human-readable overview; the individual PNGs use a fixed encoder.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct
import zlib

ROM_SIZE = 0x100000
ROM_SHA256 = '59027382154fc0aefdb2fa84251b171cbf490bc5e013205a24e677122b446a24'
THEME_TABLE = 0x7E45
LAYOUT_BASE = 0xD93F
LAYOUT_SIZE = 42
OWN_TILES = (0xC0000, 21)
ENEMY_TILES = {1: (0xC0380, 28), 2: (0xC0700, 30), 3: (0xC0AC0, 30)}
FLAG_BASE = 0xC0300
PALETTE_BASE = 0xC38E0
WIDTH, HEIGHT = 48, 56


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_rom(path: Path) -> bytes:
    """Accept the known ROM body, optionally preceded by a copier header."""
    # Bound the read even when an unrelated large file was passed by mistake.
    with path.open('rb') as stream:
        data = stream.read(ROM_SIZE + 513)
    if len(data) == ROM_SIZE + 512:
        data = data[512:]
    if len(data) != ROM_SIZE or sha256(data) != ROM_SHA256:
        raise ValueError('unsupported ROM: expected the documented Japanese revision')
    return data


def rgb565_capture(colour: int) -> tuple[int, int, int]:
    """BGR555 -> Snes9x RGB565 -> the local capture's truncated RGB888."""
    r, g, b = colour & 31, (colour >> 5) & 31, (colour >> 10) & 31
    return r << 3, (g << 3) | ((g >> 4) << 2), b << 3


def render(rom: bytes, theme: int, owner: str, level: int) -> tuple[bytes, dict]:
    if theme not in ENEMY_TILES or owner not in ('own', 'enemy') or level not in range(1, 6):
        raise ValueError('invalid castle label')
    style = 0 if owner == 'own' else theme
    layout_offset = LAYOUT_BASE + (style * 5 + level - 1) * LAYOUT_SIZE
    layout = rom[layout_offset:layout_offset + LAYOUT_SIZE]
    palette_offset = PALETTE_BASE + (theme - 1) * 0x400
    palette = [rgb565_capture(c) for c in struct.unpack_from('<16H', rom, palette_offset)]
    tiles = {}
    for start_index, (offset, count) in ((0, OWN_TILES), (21, ENEMY_TILES[theme]),
                                        (54, (FLAG_BASE, 2))):
        for i in range(count):
            tiles[start_index + i] = rom[offset + i * 32:offset + (i + 1) * 32]
    rgba = bytearray(WIDTH * HEIGHT * 4)
    opaque = []
    flag_cells, terrain_cells = [], []
    for cell_index, cell in enumerate(layout):
        cx, cy = cell_index % 6, cell_index // 6
        if cell == 0xFF:
            terrain_cells.append([cx, cy])
            continue
        if cell == 0x7F:
            continue
        tile_index = cell & 0x7F
        tile = tiles[tile_index]  # Unknown mappings fail instead of fabricating pixels.
        if tile_index in (54, 55):
            flag_cells.append([cx, cy])
        for y in range(8):
            for x in range(8):
                sx = 7 - x if cell & 0x80 else x
                colour = sum(((tile[y * 2 + p % 2 + (p // 2) * 16] >> (7 - sx)) & 1) << p
                             for p in range(4))
                if colour == 0:
                    continue
                px, py = cx * 8 + x, cy * 8 + y
                offset = (py * WIDTH + px) * 4
                rgba[offset:offset + 4] = bytes((*palette[colour], 255))
                opaque.append((px, py))
    metadata = {
        'layout_offset': f'0x{layout_offset:05x}',
        'palette_offset': f'0x{palette_offset:05x}',
        'opaque_pixels': len(opaque),
        'opaque_box_xyxy_inclusive': [min(x for x, _ in opaque), min(y for _, y in opaque),
                                      max(x for x, _ in opaque), max(y for _, y in opaque)],
        'terrain_cells_xy': terrain_cells,
        'animated_flag_cells_xy': flag_cells,
        'rgba_sha256': sha256(rgba),
    }
    return bytes(rgba), metadata


def encode_png(rgba: bytes) -> bytes:
    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack('>I', len(payload)) + tag + payload
                + struct.pack('>I', zlib.crc32(tag + payload)))
    rows = b''.join(b'\0' + rgba[y * WIDTH * 4:(y + 1) * WIDTH * 4] for y in range(HEIGHT))
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', WIDTH, HEIGHT, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows, 9)) + chunk(b'IEND', b''))


def collect(rom: bytes) -> tuple[dict, dict[str, bytes]]:
    chapter_themes = list(rom[THEME_TABLE:THEME_TABLE + 12])
    if chapter_themes != [1] * 5 + [2] * 4 + [3] * 3:
        raise ValueError('unexpected chapter-theme table')
    manifest = {
        'schema_version': 1,
        'source': {'kind': 'local_rom_graphics', 'rom_body_size': ROM_SIZE,
                   'rom_body_sha256': ROM_SHA256, 'chapter_theme_table_offset': hex(THEME_TABLE),
                   'chapter_themes': chapter_themes,
                   'own_tiles_offset': hex(OWN_TILES[0]),
                   'enemy_tiles_offsets': {str(k): hex(v[0]) for k, v in ENEMY_TILES.items()},
                   'flag_pose_offsets': [hex(FLAG_BASE), hex(FLAG_BASE + 64)],
                   'selected_flag_pose': 0},
        'sprite_size': [WIDTH, HEIGHT],
        'colour_projection': 'snes-bgr555-to-rgb565-truncated-rgb888',
        'alpha': '0 for palette index 0, layout 0x7f and terrain cell 0xff; 255 otherwise',
        'classes': [],
    }
    images = {}
    for owner in ('own', 'enemy'):
        for level in range(1, 6):
            record = {'id': f'{owner}-lv{level}', 'owner': owner, 'level': level, 'samples': []}
            for theme in (1, 2, 3):
                chapters = [n for n, value in enumerate(chapter_themes, 1) if value == theme]
                filename = f'ch{chapters[0]:02}-{chapters[-1]:02}-{owner}-lv{level}.png'
                rgba, metadata = render(rom, theme, owner, level)
                png = encode_png(rgba)
                images[filename] = png
                record['samples'].append({'theme': theme, 'chapters': chapters, 'file': filename,
                                           'png_sha256': sha256(png), **metadata})
            manifest['classes'].append(record)
    manifest['image_count'] = len(images)
    manifest['unique_rgba_count'] = len({s['rgba_sha256'] for c in manifest['classes'] for s in c['samples']})
    return manifest, images


def write_overview(directory: Path, manifest: dict) -> None:
    from PIL import Image, ImageDraw

    sheet = Image.new('RGB', (800, 6 * 196 + 28), (28, 34, 40))
    draw = ImageDraw.Draw(sheet)
    draw.text((8, 6), '30 references: 3 chapter groups x (own/enemy x Lv1-5)', fill='white')
    for record in manifest['classes']:
        for sample in record['samples']:
            row = (sample['theme'] - 1) * 2 + (record['owner'] == 'enemy')
            x, y = (record['level'] - 1) * 160, 28 + row * 196
            chapters = sample['chapters']
            draw.text((x + 5, y + 3), f'Ch{chapters[0]}-{chapters[-1]} {record["id"]}', fill='white')
            image = Image.open(directory / sample['file']).convert('RGBA')
            image = image.resize((144, 168), Image.Resampling.NEAREST)
            sheet.paste(image, (x + 8, y + 22), image)
    sheet.save(directory / 'overview.png')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rom', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='new directory, or existing with --check')
    parser.add_argument('--check', action='store_true', help='compare 30 PNGs and manifest without writing')
    args = parser.parse_args()
    try:
        manifest, images = collect(read_rom(args.rom))
        files = {**images, 'manifest.json': (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode()}
        if args.check:
            for filename, expected in files.items():
                if (args.output / filename).read_bytes() != expected:
                    raise ValueError(f'mismatch: {filename}')
            actual = {p.name for p in args.output.glob('ch*.png')}
            if actual != set(images):
                raise ValueError('unexpected or missing reference PNG')
            print('verified: 10 classes, 30 reference PNGs and manifest')
        else:
            # Fail before writes when Pillow is unavailable; never replace an existing directory.
            import PIL.Image  # noqa: F401
            args.output.mkdir(parents=True, exist_ok=False)
            for filename, data in files.items():
                (args.output / filename).write_bytes(data)
            write_overview(args.output, manifest)
            print('collected: 10 classes, 30 reference PNGs, manifest and overview')
    except (OSError, ValueError) as exc:
        parser.exit(1, f'{exc}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
