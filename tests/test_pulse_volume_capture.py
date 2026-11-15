"""reset_monitor_capture normalizes stale capture boosts at game launch.

2026-11-16 g1207: an operator pactl boost on ffmpeg's source-output survived
the corner switch — PA stream-restore resurrects a saved capture volume by
application name — and would have blown out the next game's audio. Every
launch now normalizes source-outputs recording the game sink's monitor.
"""

import unittest
from unittest.mock import patch

from docich import pulse_volume as pv


SOURCE_OUTPUTS = '''
Source Output #1
	Source: 1
	Volume: front-left: 491520 / 750% / 52.50 dB,   front-right: 491520 / 750% / 52.50 dB
	Mute: no
	media.name = "record"
	application.name = "Lavf60.16.100"
	application.process.id = "36571"

Source Output #9
	Source: 4
	Volume: front-left: 65536 / 100% / 0.00 dB,   front-right: 65536 / 100% / 0.00 dB
	media.name = "record"
	application.name = "otherapp"
'''


class _Result:
    def __init__(self, stdout='', returncode=0):
        self.stdout = stdout
        self.returncode = returncode


SOURCES_LISTING = '1\tsoren_null.monitor\tmodule-null-sink.c\ts16le 2ch 48000Hz\n4\tother.monitor\tmodule-null-sink.c\ts16le 2ch 48000Hz\n'


class ResetMonitorCaptureTests(unittest.TestCase):
    def test_boosted_capture_is_normalized_and_other_streams_untouched(self):
        calls = []

        normalized = SOURCE_OUTPUTS.replace('491520 / 750% / 52.50 dB', '65536 / 100% / 0.00 dB')

        def run(*args, env=None):
            calls.append(args)
            if args[:3] == ('list', 'short', 'sources'):
                return _Result(stdout=SOURCES_LISTING)
            boosted = not any(c[0] == 'set-source-output-volume' for c in calls)
            return _Result(stdout=SOURCE_OUTPUTS if boosted else normalized)

        result = pv.reset_monitor_capture('soren_null', env=None, run=run)
        volume_calls = [c for c in calls if c[0] == 'set-source-output-volume']
        self.assertEqual(volume_calls, [('set-source-output-volume', '1', '100%')])
        self.assertEqual(result['status'], 'applied')
        self.assertEqual(result['target_percent'], 100)
        # A later listing shows the normalized volume.
        after = SOURCE_OUTPUTS.replace('491520 / 750% / 52.50 dB', '65536 / 100% / 0.00 dB')

        calls.clear()

        def run_after(*args, env=None):
            calls.append(args)
            if args[:3] == ('list', 'short', 'sources'):
                return _Result(stdout=SOURCES_LISTING)
            return _Result(stdout=after)

        result2 = pv.reset_monitor_capture('soren_null', env=None, run=run_after)
        self.assertFalse([c for c in calls if c[0] == 'set-source-output-volume'])
        self.assertEqual(result2['status'], 'applied')

    def test_no_capture_reports_no_stream(self):
        def run(*args, env=None):
            return _Result(stdout='')

        result = pv.reset_monitor_capture('soren_null', env=None, run=run)
        self.assertEqual(result['status'], 'no_stream')

    def test_pactl_failure_is_reported_not_raised(self):
        def run(*args, env=None):
            return _Result(returncode=1)

        result = pv.reset_monitor_capture('soren_null', env=None, run=run)
        self.assertEqual(result['status'], 'pactl_failed')


if __name__ == '__main__':
    unittest.main()
