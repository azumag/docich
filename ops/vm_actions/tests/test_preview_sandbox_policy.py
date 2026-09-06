import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GATEWAY = ROOT / 'ops/vm_actions/gateway.py'
INSTALLER = ROOT / 'ops/vm_actions/install_vm_gateway.sh'


class PreviewSandboxPolicyTests(unittest.TestCase):
    def test_gateway_keeps_network_isolation_and_prefers_local_bwrap(self):
        text = GATEWAY.read_text()
        self.assertIn("args=['bwrap','--unshare-all'", text)
        self.assertIn("'PATH':'/usr/local/bin:/usr/bin:/bin'", text)
        self.assertNotIn("'--share-net'", text)

    def test_installer_scopes_userns_permission_to_operator_bwrap(self):
        text = INSTALLER.read_text()
        self.assertIn('/usr/bin/bwrap', text)
        self.assertIn('/usr/local/bin/bwrap', text)
        self.assertIn('-m 0750', text)
        self.assertIn('/etc/apparmor.d/usr.local.bin.bwrap', text)
        self.assertIn('/usr/local/bin/bwrap flags=(unconfined)', text)
        self.assertIn('userns,', text)
        self.assertIn('apparmor_parser -r', text)
        self.assertNotIn('apparmor_restrict_unprivileged_userns=0', text)
        self.assertNotIn('sysctl ', text)


if __name__ == '__main__':
    unittest.main()
