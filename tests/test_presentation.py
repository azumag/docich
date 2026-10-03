import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich.presentation import _parser, cell_aspect_scale, contain_filter, tv_filter, measured_contain_size


def _content_bbox(raw, width, height, threshold=200):
    """Return (left, top, right, bottom) of bright pixels in an rgb24 frame."""
    left, top, right, bottom = width, height, -1, -1
    for y in range(height):
        row = raw[y * width * 3:(y + 1) * width * 3]
        for x in range(width):
            if row[x * 3] > threshold:
                left = min(left, x)
                right = max(right, x)
                top = min(top, y)
                bottom = max(bottom, y)
    return left, top, right, bottom


@unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg required')
class PresentationPixels(unittest.TestCase):
    def test_measured_left_projection_removes_only_black_padding(self):
        for w, h in [(299, 224), (300, 300), (1200, 400)]:
            with self.subTest(native=(w,h)):
                cw, ch = measured_contain_size(w, h, 960, 540)
                self.assertEqual((cw, ch), ((721, 540) if w == 299 else (540, 540) if w == 300 else (960, 320)))
                # Mark all four source borders; compare full left containment
                # against the shorter projection window byte-for-byte.
                raw = bytearray(bytes((255, 255, 255, 255)) * w * h)
                for y in range(h):
                    for x in range(w):
                        if x < 3 or x >= w-3 or y < 3 or y >= h-3:
                            offset=(y*w+x)*4;raw[offset:offset+4]=bytes((0,0,255,255))
                outputs=[]
                for crop in ('', f',crop={cw}:540:0:0:exact=1'):
                    out=subprocess.run(['ffmpeg','-v','error','-f','rawvideo','-pix_fmt','bgra',
                        '-s',f'{w}x{h}','-i','pipe:0','-vf',contain_filter(960,540,nearest=True,align='left')+crop,
                        '-frames:v','1','-pix_fmt','rgb24','-f','rawvideo','-'],
                        input=raw,capture_output=True,check=True,timeout=20).stdout
                    outputs.append(out)
                self.assertEqual(outputs[1], b''.join(outputs[0][y*960*3:y*960*3+cw*3] for y in range(540)))
                top=(540-ch)//2
                for x,y in [(0,top+ch//2),(cw-1,top+ch//2),(cw//2,top),(cw//2,top+ch-1)]:
                    self.assertEqual(outputs[1][(y*cw+x)*3:(y*cw+x)*3+3],bytes((255,0,0)))

    def test_nearest_contain_preserves_small_source_aspect_and_all_edges(self):
        # Odd native width must not be rounded down by a YUV test generator.
        # Include a non-4:3 source to catch accidental fixed-TV stretching.
        for width, height in [(299, 224), (300, 300)]:
            with self.subTest(size=(width, height)):
                raw = bytes((255, 255, 255)) * width * height
                result = subprocess.run([
                    'ffmpeg', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
                    '-s', f'{width}x{height}', '-i', 'pipe:0',
                    '-vf', contain_filter(960, 540, nearest=True), '-frames:v', '1',
                    '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-',
                ], input=raw, capture_output=True, check=True, timeout=20)
                left, top, right, bottom = _content_bbox(result.stdout, 960, 540)
                self.assertEqual((top, bottom), (0, 539))
                self.assertAlmostEqual(right - left + 1, 540 * width / height, delta=1)
                self.assertLessEqual(abs(left - (959 - right)), 1)
                for x in (left - 1, right + 1):
                    offset = (270 * 960 + x) * 3
                    self.assertEqual(result.stdout[offset:offset + 3], b'\0\0\0')

    def test_all_four_edges_survive_and_padding_is_centered(self):
        for dimensions, left, top, width, height in [
            ('600x600', 210, 0, 540, 540), ('800x600', 120, 0, 720, 540),
        ]:
            with self.subTest(dimensions=dimensions):
                result = subprocess.run([
                    'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                    f'color=white:s={dimensions},drawbox=x=0:y=0:w=iw:h=ih:color=red:t=8',
                    '-vf', contain_filter(960, 540), '-frames:v', '1',
                    '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-',
                ], capture_output=True, check=True, timeout=20)
                for x, y in [(left+1, top+height//2), (left+width-2, top+height//2),
                             (left+width//2, top+1), (left+width//2, top+height-2)]:
                    offset = (y * 960 + x) * 3
                    r, g, b = result.stdout[offset:offset+3]
                    self.assertGreater(r, 200)
                    self.assertLess(g, 40)
                    self.assertLess(b, 40)
                for x in (left-1, left+width):
                    offset = (270 * 960 + x) * 3
                    self.assertTrue(all(v < 3 for v in result.stdout[offset:offset+3]))

    def test_square_and_wide_sources_are_contained_with_black_padding(self):
        for dimensions, black, content in [
            ('600x600', (100, 270), (480, 270)),
            ('800x600', (50, 270), (480, 270)),
            ('1200x400', (480, 20), (480, 270)),
        ]:
            with self.subTest(dimensions=dimensions):
                result = subprocess.run([
                    'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                    f'color=white:s={dimensions}', '-vf', contain_filter(960, 540),
                    '-frames:v', '1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-',
                ], capture_output=True, check=True, timeout=20)
                self.assertEqual(len(result.stdout), 960 * 540 * 3)
                for (x, y), expected in [(black, 0), (content, 255)]:
                    offset = (y * 960 + x) * 3
                    self.assertTrue(all(abs(v - expected) <= 2
                                        for v in result.stdout[offset:offset+3]))

    def test_tv_fit_keeps_the_previous_4_3_picture_centred(self):
        # Hanjuku: native 256x224 -> 720x540 (4:3, as the old 897x672 was),
        # centred in the 960x540 viewport; never stretched to 16:9.
        result = subprocess.run([
            'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=white:s=256x224',
            '-vf', tv_filter(960, 540),
            '-frames:v', '1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-',
        ], capture_output=True, check=True, timeout=20)
        self.assertEqual(len(result.stdout), 960 * 540 * 3)
        self.assertEqual(_content_bbox(result.stdout, 960, 540), (120, 0, 839, 539))

    def test_cell_stretch_squares_tiles_without_changing_window_fit(self):
        # pacman4console の実寸: 29x32 cells (cell 11x21 px)、maze は (1,1) の 28x29 cells。
        source = ('color=black:s=319x672,'
                  'drawbox=x=11:y=21:w=308:h=609:color=white:t=fill')
        seen = {}
        for cell_stretch, expected in ((1.0, (247, 489)), (2.0, (495, 489))):
            with self.subTest(cell_stretch=cell_stretch):
                result = subprocess.run([
                    'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', source,
                    '-vf', contain_filter(960, 540, cell_stretch=cell_stretch),
                    '-frames:v', '1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-',
                ], capture_output=True, check=True, timeout=20)
                self.assertEqual(len(result.stdout), 960 * 540 * 3)
                left, top, right, bottom = _content_bbox(result.stdout, 960, 540)
                seen[cell_stretch] = (left, top, right, bottom)
                self.assertAlmostEqual(right - left + 1, expected[0], delta=10)
                self.assertAlmostEqual(bottom - top + 1, expected[1], delta=10)
        # 補正後はタイル (幅308, 高609) が正方形相当になり、縦位置は変わらない。
        square_left, square_top, square_right, square_bottom = seen[2.0]
        measured_height = square_bottom - square_top + 1
        self.assertAlmostEqual((square_right - square_left + 1) / measured_height,
                               1.0, delta=0.03)
        self.assertAlmostEqual(square_top, seen[1.0][1], delta=2)

    def test_filter_strings_pin_default_and_pacman_pipelines(self):
        # 既定は origin/main 時点と完全同一。pacman の補正は水平2倍のみを前置する。
        self.assertEqual(
            contain_filter(960, 540),
            'scale=960:540:force_original_aspect_ratio=decrease,'
            'pad=960:540:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1',
        )
        self.assertEqual(
            contain_filter(960, 540, cell_stretch=2.0),
            'scale=iw*2:ih:flags=neighbor,'
            'scale=960:540:force_original_aspect_ratio=decrease,'
            'pad=960:540:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1',
        )


class CellAspectScaleTests(unittest.TestCase):
    def test_scale_is_height_over_width(self):
        self.assertEqual(cell_aspect_scale('1:2'), 2.0)
        self.assertEqual(cell_aspect_scale('  2 : 1 '), 0.5)
        self.assertEqual(cell_aspect_scale('1:1'), 1.0)

    def test_malformed_values_rejected(self):
        for value in ('', '1', '1:', ':2', '1:2:3', 'one:two', '0:2', '1:0', '1:5', '  '):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    cell_aspect_scale(value)

    def test_non_string_rejected(self):
        with self.assertRaises(ValueError):
            cell_aspect_scale(2)


class CellAspectOptionTests(unittest.TestCase):
    def test_framerate_and_fit_default_to_the_existing_projection(self):
        args = self._parse([])
        self.assertEqual((args.framerate, args.fit), (15, 'contain'))
        self.assertFalse(args.nearest)
        self.assertTrue(self._parse(['--nearest']).nearest)
        args = self._parse(['--framerate', '30', '--fit', 'tv'])
        self.assertEqual((args.framerate, args.fit), (30, 'tv'))

    def _parse(self, extra):
        return _parser().parse_args([
            '--display', ':97', '--title', 't',
            '--x', '0', '--y', '90', '--width', '960', '--height', '540',
            *extra, '--', 'true',
        ])

    def test_option_parses_to_stretch(self):
        self.assertEqual(self._parse(['--cell-aspect', '1:2']).cell_stretch, 2.0)

    def test_default_keeps_native_filter(self):
        self.assertIsNone(self._parse([]).cell_stretch)

    def test_invalid_value_rejected(self):
        with self.assertRaises(SystemExit):
            self._parse(['--cell-aspect', '1:0'])
