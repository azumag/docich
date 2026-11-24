from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))


class NativeCaptionSourceTests(unittest.TestCase):
    def test_filter_attaches_a53_side_data_and_uses_cc_fifo(self) -> None:
        source = (REPO_ROOT / "native/ffmpeg/vf_docichcc.c").read_text(encoding="utf-8")
        self.assertIn("AV_FRAME_DATA_A53_CC", source)
        self.assertIn("ff_ccfifo_init", source)
        self.assertIn("ff_ccfifo_inject", source)
        self.assertIn("sei_from_caption_frame", source)

    def test_unsupported_frame_rate_keeps_captions_instead_of_disabling(self) -> None:
        # #2792: ccfifo only knows 15/24/30/60 fps. 20 fps (the production
        # operator value) used to disable the whole filter. The filter must
        # derive a per-frame triplet budget and still create the socket.
        source = (REPO_ROOT / "native/ffmpeg/vf_docichcc.c").read_text(encoding="utf-8")
        self.assertIn("per_frame = (int)(60.0 / fps);", source)
        self.assertIn("cc_count = (int)(600.0 / fps + 0.999);", source)
        self.assertIn("s->cc_fifo.expected_608 = per_frame;", source)
        self.assertIn("s->cc_fifo.expected_cc_count = cc_count;", source)
        self.assertIn("s->cc_fifo.passthrough = 0;", source)
        # A non-positive frame rate stays fail-open (no filter-graph failure).
        self.assertIn("docichcc disabled: frame rate", source)
        # The listener must not be gated on the frame-rate table.
        config_input = source.split("static int config_input(", 1)[1].split(
            "static av_cold int init(", 1
        )[0]
        self.assertIn("return setup_listener(ctx);", config_input)

    def test_socket_is_private_nonblocking_and_identity_guarded(self) -> None:
        source = (REPO_ROOT / "native/ffmpeg/vf_docichcc.c").read_text(encoding="utf-8")
        self.assertIn("chmod(s->socket_path, 0600)", source)
        self.assertIn("O_NONBLOCK", source)
        self.assertIn("STALE_EXECUTION", source)
        self.assertIn("DOCICHCC_MAX_MESSAGE", source)

    def test_build_is_pinned_and_checks_production_capabilities(self) -> None:
        build = (REPO_ROOT / "native/ffmpeg/build.sh").read_text(encoding="utf-8")
        self.assertIn('ffmpeg_commit="e38092ef9395d7049f871ef4d5411eb410e283e0"', build)
        self.assertIn('caption_commit="e8b6261090eb3f2012427cc6b151c923f82453db"', build)
        for capability in ("docichcc", "x11grab", "pulse", "libx264", "a53cc", "rtmp"):
            self.assertIn(capability, build)

    def test_local_poc_uses_the_public_docich_caption_cli(self) -> None:
        poc = (REPO_ROOT / "native/ffmpeg/poc.sh").read_text(encoding="utf-8")
        self.assertIn('"$repo_root/bin/docich" caption plan', poc)
        self.assertIn('"$repo_root/bin/docich" caption send prepare', poc)
        self.assertIn("verify_a53_sei.py", poc)

    def test_build_writes_a_versioned_manifest(self) -> None:
        build = (REPO_ROOT / "native/ffmpeg/build.sh").read_text(encoding="utf-8")
        for token in (
            "MANIFEST.json",
            "docich_commit",
            "vf_docichcc_sha256",
            "architecture",
            "build_date",
            "ffmpeg_bin",
        ):
            self.assertIn(token, build)
        self.assertIn('"artifact": "docich-ffmpeg"', build)


if __name__ == "__main__":
    unittest.main()
