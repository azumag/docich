import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "scripts" / "systemd" / "docich-webui.service"


class WebuiSystemdTemplateTests(unittest.TestCase):
    """雛形 unit は本番 (production VM) と同契約であること。

    2026-09-23 の二重unit復旧で、reviewed 契約の user unit が
    `EnvironmentFile=-/home/ubuntu/.config/docich/webui.env` を持ち、
    mutation gate (#42) の `DOCICH_WEBUI_ALLOWED_ORIGINS` を読んでいた。
    テンプレートから同じ行が消えると、導入手順だけで Tailscale 経由の
    mutation が 403 `invalid_origin` に戻るため固定する。
    """

    def setUp(self):
        self.text = TEMPLATE.read_text(encoding="utf-8")

    def test_template_reads_optional_user_env_file(self):
        self.assertIn("EnvironmentFile=-%h/.config/docich/webui.env", self.text)

    def test_env_file_line_is_optional_and_inside_service_section(self):
        # 先頭の `-` は任意読込 (ファイル無しでも起動できる)。
        line = next(
            line for line in self.text.splitlines()
            if line.startswith("EnvironmentFile=")
        )
        self.assertTrue(line.startswith("EnvironmentFile=-"), line)
        service_at = self.text.index("[Service]")
        install_at = self.text.index("[Install]")
        self.assertLess(service_at, self.text.index(line), "[Service] に入っていない")
        self.assertLess(self.text.index(line), install_at, "[Install] 以降に出ていく")

    def test_readme_install_sed_still_matches_placeholders(self):
        # README / wiki の sed 導入手順が壊れないよう placeholder を固定する。
        self.assertIn("WorkingDirectory=__DOCICH_ROOT__", self.text)
        self.assertIn("ExecStart=__DOCICH_ROOT__/bin/docich ", self.text)
        self.assertEqual(self.text.count("__DOCICH_ROOT__"), 3)

    def test_exec_start_uses_production_profile(self):
        # 既定 config (state_dir=run) で起動すると Corners タブが本番の
        # run-soren-live を読まず、ローテーションが「動いていない」ように見える
        # (2026-09-23 実測: rotation present=false / catalog 空 / game_switch 9/15 のまま)。
        line = next(l for l in self.text.splitlines() if l.startswith("ExecStart="))
        self.assertIn("--config __DOCICH_ROOT__/config/docich.soren-live.toml", line)
        self.assertLess(line.index("--config"), line.index(" webui"))


if __name__ == "__main__":
    unittest.main()
