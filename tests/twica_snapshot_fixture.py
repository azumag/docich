"""Offline synthetic RGBA producer for the real X11/FFmpeg integration test.

This is test code, not a runtime fallback and not an upstream-page substitute.
"""
import signal
import threading
import time
from PIL import Image, ImageDraw
from docich.twica_overlay import SnapshotPublisher
from docich.twica_state import control, state_directory, frame_directory, heartbeat
stop = threading.Event()
for sig in (signal.SIGTERM, signal.SIGINT):
    signal.signal(sig, lambda *_: stop.set())
image = Image.new('RGBA', (320, 180))
ImageDraw.Draw(image).rectangle((120, 60, 199, 119), fill=(255, 255, 255, 255))
with SnapshotPublisher(frame_directory(), 320, 180) as publisher:
    while not stop.wait(.05):
        common = control(state_directory())['owner'] == 'common'
        if common:
            publisher.publish(image.tobytes(), time.monotonic_ns())
        heartbeat(state_directory(), 'renderer.json', state='active' if common else 'standby')
