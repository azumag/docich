import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich.presentation import contain_filter


@unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg required')
class PresentationPixels(unittest.TestCase):
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
