import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GATEWAY = ROOT / 'ops/vm_actions/gateway.py'
INSTALLER = ROOT / 'ops/vm_actions/install_vm_gateway.sh'


class PreviewSandboxPolicyTests(unittest.TestCase):
    def test_gateway_uses_dedicated_bwrap_and_keeps_network_isolation(self):
        text = GATEWAY.read_text()
        self.assertIn("BWRAP=Path('/usr/local/libexec/azumag-vm-ops/bwrap')", text)
        self.assertIn("str(BWRAP),'--unshare-all'", text)
        self.assertNotIn("'--share-net'", text)

    def test_installer_scopes_userns_permission_to_gateway_bwrap(self):
        text = INSTALLER.read_text()
        self.assertIn('/usr/local/libexec/azumag-vm-ops/bwrap', text)
        self.assertIn('/etc/apparmor.d/usr.local.libexec.azumag-vm-ops.bwrap', text)
        self.assertIn('/usr/local/libexec/azumag-vm-ops/bwrap flags=(unconfined)', text)
        self.assertIn('userns,', text)
        self.assertIn('apparmor_parser -r', text)
        self.assertNotIn('apparmor_restrict_unprivileged_userns=0', text)
        self.assertNotIn('sysctl ', text)


if __name__ == '__main__':
    unittest.main()
