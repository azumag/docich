"""OpenSSL capability gate for the local crypto evidence tests.

``hanjuku_evidence._openssl`` deliberately pins ``/usr/bin/openssl`` (the
production contract on the VM), but stock macOS ships LibreSSL there.  LibreSSL
can produce the required RSA-OAEP CMS envelope yet cannot decrypt it again
(``Error decrypting CMS structure`` / exit 4), so a *local macOS* run cannot
exercise the evidence roundtrip at all.  Linux CI has OpenSSL 3.x and keeps
running every crypto test unchanged.

Only an explicitly detected LibreSSL is skipped.  A missing or otherwise broken
openssl still fails the tests, because that is a real environment problem the
``crypto_unavailable`` / ``crypto_failed`` contracts rely on.
"""

from __future__ import annotations

import subprocess

LIBRESSL_SKIP_REASON = (
    "system /usr/bin/openssl is LibreSSL, which cannot decrypt CMS RSA-OAEP "
    "evidence; the roundtrip runs on OpenSSL 3.x (Linux CI) - docich#2136"
)


def system_openssl_is_libressl() -> bool:
    """True only when ``/usr/bin/openssl version`` reports LibreSSL."""
    try:
        result = subprocess.run(
            ["/usr/bin/openssl", "version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.stdout.strip().startswith("LibreSSL")
