"""Loopback-only local STT. Model and audio stay on this machine at runtime."""
import argparse
import io
import json
import logging
import re
import sys
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

MAX_WAV = 44 + 48000 * 10 * 2
STT_PROFILE = "game-progress-v1"
STT_HOTWORDS = "同志、進捗、戦略、攻略、クリア、着地地点"
STT_INITIAL_PROMPT = "日本語のゲームの会話です。同志、進捗、戦略調整、攻略、クリア、着地地点。"
# Correct only the known addressed progress request, never arbitrary prose or word lookup.
_PROGRESS_REQUEST = re.compile(
    r"^(?P<before>[「『（(]*(?:同志|同士|どうし|ドウシ)[\s、,。!！?？:：]*(?:(?:今|現在)の)?)"
    r"真直(?P<after>を教えて(?:ください|下さい)?[\s。.!！?？」』）)]*)$"
)


def normalize_transcript(text):
    text = text.strip()
    match = _PROGRESS_REQUEST.fullmatch(text)
    if match:
        return match.group("before") + "進捗" + match.group("after")
    return text


def valid_wav(audio):
    try:
        with wave.open(io.BytesIO(audio), 'rb') as wav:
            return (wav.getnchannels() == 1 and wav.getsampwidth() == 2 and
                    wav.getframerate() == 48000 and 0 < wav.getnframes() <= 480000 and
                    len(wav.readframes(wav.getnframes())) == wav.getnframes() * 2)
    except Exception:
        return False


def serve(model, port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def setup(self):
            super().setup()
            self.connection.settimeout(3)

        def reply(self, status, payload):
            body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.reply(200 if self.path == '/healthz' else 404, {'ready': self.path == '/healthz', 'sttProfile': STT_PROFILE})

        def do_POST(self):
            audio = None
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if self.path != '/transcribe':
                    self.reply(404, {'error': 'not_found'}); return
                if self.headers.get('Content-Type') != 'audio/wav' or not 44 < length <= MAX_WAV or self.headers.get('Transfer-Encoding'):
                    self.reply(400, {'error': 'invalid_audio'}); return
                audio = self.rfile.read(length)
                if len(audio) != length or not valid_wav(audio):
                    self.reply(400, {'error': 'invalid_audio'}); return
                segments, _ = model.transcribe(io.BytesIO(audio), language='ja', task='transcribe',
                    beam_size=1, temperature=0.0, condition_on_previous_text=False, vad_filter=True,
                    without_timestamps=True, hotwords=STT_HOTWORDS,
                    initial_prompt=STT_INITIAL_PROMPT)
                text = normalize_transcript(''.join(segment.text for segment in segments))
                if len(text) > 2000:
                    self.reply(422, {'error': 'text_too_long'}); return
                self.reply(200, {'text': text})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as error:
                print(json.dumps({'event':'local_stt_inference_failed','kind':type(error).__name__}), file=sys.stderr, flush=True)
                try: self.reply(503, {'error': 'local_stt_failed'})
                except Exception: pass
            finally:
                audio = None

    class Server(HTTPServer):
        request_queue_size = 4
        def handle_error(self, request, client_address):
            pass  # Never print request/audio/exception contents.

    with Server(('127.0.0.1', port), Handler) as server:
        print(json.dumps({'event': 'local_stt_ready', 'device': 'cpu', 'computeType': 'int8'}), flush=True)
        server.serve_forever()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model-dir', required=True)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--threads', type=int, default=4)
    args = parser.parse_args()
    if not Path(args.model_dir).is_dir() or not 1024 <= args.port <= 65535 or not 1 <= args.threads <= 8:
        raise ValueError('invalid_config')
    logging.disable(logging.CRITICAL)
    from faster_whisper import WhisperModel
    model = WhisperModel(args.model_dir, device='cpu', compute_type='int8',
                         cpu_threads=args.threads, num_workers=1, local_files_only=True)
    serve(model, args.port)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception:
        print(json.dumps({'event': 'local_stt_start_failed'}), flush=True)
        sys.exit(2)
