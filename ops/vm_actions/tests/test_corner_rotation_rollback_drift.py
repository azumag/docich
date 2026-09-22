"""Regression coverage for rollback idempotency drift handling."""

from test_corner_rotation_migration import (
    LEGACY_SERVICE,
    LEGACY_TIMER,
    ROLLBACK,
    UnitScriptFixture,
)


class RollbackLegacyDriftTests(UnitScriptFixture):
    def test_already_legacy_state_rejects_unreviewed_service_content(self):
        self.write_legacy_units()
        with (self.unit_dir / LEGACY_SERVICE).open("a", encoding="utf-8") as handle:
            handle.write("\n# unexpected local edit\n")

        result = self.run_helper(ROLLBACK)

        self.assertEqual(result.returncode, 32, result.stderr)
        self.assertIn("reviewed template", result.stderr)
        self.assertTrue(self.enabled_fake(LEGACY_TIMER))
        self.assertEqual(self.wants_timers(), {LEGACY_TIMER})
        self.assert_corner_state_untouched()
