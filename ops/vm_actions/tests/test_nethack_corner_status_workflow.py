import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
WF = ROOT / '.github/workflows/nethack-corner-operator.yml'


class NetHackCornerStatusWorkflowTests(unittest.TestCase):
    def test_status_query_does_not_swallow_transport_or_unknown_exit(self):
        text = WF.read_text(encoding='utf-8')
        self.assertIn('255) reason=transport_error; status_ok=0 ;;', text)
        self.assertIn('*) reason=unclassified; status_ok=0 ;;', text)
        self.assertIn('if (( status_ok == 0 )); then', text)
        self.assertIn("if: ${{ steps.status.outcome == 'failure' }}", text)

    def test_known_state_categories_remain_report_only(self):
        text = WF.read_text(encoding='utf-8')
        for snippet in (
            '0) reason=idle_or_terminal ;;',
            '10) reason=starting ;;',
            '11) reason=active ;;',
            '12) reason=failed ;;',
            '13) reason=unreadable ;;',
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, text)


if __name__ == '__main__':
    unittest.main()
