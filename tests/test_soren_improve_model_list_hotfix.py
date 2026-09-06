import hashlib
import importlib.util
import stat
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "ops" / "hotfixes" / "apply_soren_improve_model_list_hotfix.py"


def load_hotfix():
    if not SCRIPT.is_file():
        raise AssertionError("production hotfix script is missing")
    spec = importlib.util.spec_from_file_location("improve_model_list_hotfix", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class SorenImproveModelListHotfixTests(unittest.TestCase):
    def test_cli_targets_only_production_config(self):
        hotfix = load_hotfix()
        self.assertEqual(hotfix.TARGET, Path("/home/ubuntu/soren/core/config.sh"))
        self.assertEqual(hotfix.OLD_SHA256, "19b8728b5ebaa8819ef65cefbe991d4bb3e0dafad5beb2ecbed77737da9182b4")
        self.assertEqual(hotfix.NEW_SHA256, "46e5d2a90f05c62755665bcda121403c1610bce2e5dd07405b0c4038d775fd6a")

    def test_transform_replaces_only_reviewed_model_list_hunk(self):
        hotfix = load_hotfix()
        text = "prefix\n" + hotfix.OLD_FRAGMENT + "suffix\n"
        transformed = hotfix.transform(text)
        self.assertNotIn(hotfix.OLD_FRAGMENT, transformed)
        self.assertIn(hotfix.NEW_FRAGMENT, transformed)
        self.assertTrue(transformed.startswith("prefix\n") and transformed.endswith("suffix\n"))

    def test_transform_refuses_missing_or_duplicate_preimage(self):
        hotfix = load_hotfix()
        for text in ("unreviewed\n", hotfix.OLD_FRAGMENT + hotfix.OLD_FRAGMENT):
            with self.subTest(text=text):
                with self.assertRaisesRegex(ValueError, "preimage fragment mismatch"):
                    hotfix.transform(text)

    def test_apply_is_atomic_idempotent_and_preserves_mode(self):
        hotfix = load_hotfix()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "config.sh"
            old = ("prefix\n" + hotfix.OLD_FRAGMENT + "suffix\n").encode()
            new = hotfix.transform(old.decode()).encode()
            target.write_bytes(old)
            target.chmod(0o640)
            hotfix.OLD_SHA256 = hashlib.sha256(old).hexdigest()
            hotfix.NEW_SHA256 = hashlib.sha256(new).hexdigest()
            self.assertEqual(hotfix.apply(target), "applied")
            self.assertEqual(target.read_bytes(), new)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o640)
            self.assertEqual(hotfix.apply(target), "already_applied")

    def test_apply_refuses_unreviewed_full_file_hash(self):
        hotfix = load_hotfix()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "config.sh"
            target.write_text("unreviewed live drift\n")
            before = target.read_bytes()
            with self.assertRaisesRegex(ValueError, "refusing unreviewed live drift"):
                hotfix.apply(target)
            self.assertEqual(target.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
