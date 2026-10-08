"""Validate local audio limits without downloading/importing a model."""
import importlib.util
import io
from pathlib import Path
import unittest
import wave

spec = importlib.util.spec_from_file_location('local_stt_server', Path(__file__).with_name('local-stt-server.py'))
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def wav_bytes(channels=1, width=2, rate=48000, frames=960):
    data = io.BytesIO()
    with wave.open(data, 'wb') as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(rate)
        wav.writeframes(bytes(frames * channels * width))
    return data.getvalue()


class AudioValidation(unittest.TestCase):
    def test_admits_bounded_mono_pcm(self):
        self.assertTrue(server.valid_wav(wav_bytes()))
        self.assertTrue(server.valid_wav(wav_bytes(frames=480000)))

    def test_rejects_wrong_format_oversized_and_truncated_audio(self):
        for body in [b'not a wav', wav_bytes(channels=2), wav_bytes(width=1),
                     wav_bytes(rate=16000), wav_bytes(frames=0),
                     wav_bytes(frames=480001), wav_bytes()[:-1]]:
            with self.subTest(length=len(body)):
                self.assertFalse(server.valid_wav(body))


class ProgressRecognition(unittest.TestCase):
    def test_known_addressed_progress_request_is_corrected(self):
        for text, expected in [
            ("同志 真直を教えて", "同志 進捗を教えて"),
            ("同志、今の真直を教えてください。", "同志、今の進捗を教えてください。"),
            ("どうし、真直を教えて。", "どうし、進捗を教えて。"),
        ]:
            with self.subTest(text=text):
                self.assertEqual(server.normalize_transcript(text), expected)

    def test_literal_lookup_and_ambient_text_are_not_rewritten(self):
        for text in [
            "同志、真直という漢字の意味を教えて", "同志、「真直」を教えて", "真直を教えて",
            "同志、真直って何？", "同志、真直は人の名前です", "同志、進捗を教えて", "",
        ]:
            with self.subTest(text=text):
                self.assertEqual(server.normalize_transcript(text), text)


if __name__ == '__main__':
    unittest.main()
