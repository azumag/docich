"""Castle corpus integrity and independent native-render comparisons, without a ROM."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

from PIL import Image
import pytest

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / 'evals' / 'hanjuku' / 'castles'
spec = importlib.util.spec_from_file_location('hanjuku_collect_castles', ROOT / 'tools' / 'hanjuku_collect_castles.py')
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


def test_thirty_labelled_reference_images_have_valid_pixels_and_hashes():
    manifest = json.loads((CORPUS / 'manifest.json').read_text())
    assert manifest['sprite_size'] == [48, 56]
    assert manifest['source']['rom_body_sha256'] == collector.ROM_SHA256
    assert manifest['source']['chapter_themes'] == [1] * 5 + [2] * 4 + [3] * 3
    assert {(c['owner'], c['level']) for c in manifest['classes']} == {
        (owner, level) for owner in ('own', 'enemy') for level in range(1, 6)}
    assert len(manifest['classes']) == 10
    files, labels = set(), set()
    for record in manifest['classes']:
        assert record['id'] == f'{record["owner"]}-lv{record["level"]}'
        assert len(record['samples']) == 3
        for sample in record['samples']:
            label = (record['owner'], record['level'], sample['theme'])
            assert label not in labels
            labels.add(label)
            assert sample['chapters'] == {1: list(range(1, 6)), 2: list(range(6, 10)),
                                          3: list(range(10, 13))}[sample['theme']]
            path = CORPUS / sample['file']
            files.add(path.name)
            assert hashlib.sha256(path.read_bytes()).hexdigest() == sample['png_sha256']
            with Image.open(path) as image:
                assert image.mode == 'RGBA' and image.size == (48, 56)
                assert hashlib.sha256(image.tobytes()).hexdigest() == sample['rgba_sha256']
                alpha = image.getchannel('A')
                alpha_bytes = alpha.tobytes()
                assert set(alpha_bytes) == {0, 255}
                assert alpha_bytes.count(255) == sample['opaque_pixels']
                x0, y0, x1, y1 = alpha.getbbox()
                assert [x0, y0, x1 - 1, y1 - 1] == sample['opaque_box_xyxy_inclusive']
                for cx, cy in sample['terrain_cells_xy']:
                    assert image.crop((cx * 8, cy * 8, cx * 8 + 8, cy * 8 + 8)).getchannel('A').getbbox() is None
    assert len(files) == len(labels) == 30
    hashes = {s['rgba_sha256'] for c in manifest['classes'] for s in c['samples']}
    assert manifest['image_count'] == 30
    assert manifest['unique_rgba_count'] == len(hashes) == 25
    assert files == {p.name for p in CORPUS.glob('ch*.png')}
    # Accidentally repeating a level must not satisfy a count-only check.
    for theme in (1, 2, 3):
        for owner in ('own', 'enemy'):
            hashes = [s['rgba_sha256'] for c in manifest['classes'] if c['owner'] == owner
                      for s in c['samples'] if s['theme'] == theme]
            assert len(set(hashes)) == 5


@pytest.mark.parametrize('case_index', range(3))
def test_static_castle_pixels_match_independent_native_capture(case_index):
    verification = CORPUS / 'verification'
    case = json.loads((verification / 'checks.json').read_text())['checks'][case_index]
    manifest = json.loads((CORPUS / 'manifest.json').read_text())
    record = next(c for c in manifest['classes'] if c['id'] == case['class_id'])
    sample = next(s for s in record['samples'] if s['theme'] == case['theme'])
    capture_path = verification / case['file']
    assert hashlib.sha256(capture_path.read_bytes()).hexdigest() == case['png_sha256']
    with Image.open(CORPUS / sample['file']) as ref, Image.open(capture_path) as raw:
        capture = raw.convert('RGB')
        assert capture.size == (256, 224)
        excluded = {tuple(cell) for cell in sample['animated_flag_cells_xy']}
        ox, oy = case['origin_xy']
        compared = 0
        for y in range(56):
            for x in range(48):
                pixel = ref.getpixel((x, y))
                if pixel[3] and (x // 8, y // 8) not in excluded:
                    assert capture.getpixel((ox + x, oy + y)) == pixel[:3]
                    compared += 1
        assert compared == case['static_opaque_pixels'] > 600


def test_rom_reader_accepts_copier_header_but_rejects_wrong_revision(tmp_path, monkeypatch):
    # Synthetic content only; CI never needs a ROM or emulator installation.
    body = bytes(collector.ROM_SIZE)
    monkeypatch.setattr(collector, 'ROM_SHA256', hashlib.sha256(body).hexdigest())
    rom = tmp_path / 'synthetic.sfc'
    for header in (b'', b'\x55' * 512):
        rom.write_bytes(header + body)
        assert collector.read_rom(rom) == body
    rom.write_bytes(body[:-1] + b'\x01')
    with pytest.raises(ValueError, match='unsupported ROM'):
        collector.read_rom(rom)


def test_unsupported_rom_does_not_create_output(tmp_path, monkeypatch):
    rom, output = tmp_path / 'unsupported.sfc', tmp_path / 'output'
    rom.write_bytes(b'synthetic invalid input')
    monkeypatch.setattr(sys, 'argv', ['collector', '--rom', str(rom), '--output', str(output)])
    with pytest.raises(SystemExit) as error:
        collector.main()
    assert error.value.code == 1
    assert not output.exists()
