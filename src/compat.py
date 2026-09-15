"""Compatibility utilities and platform patches for Python and OS runtimes.

Centralizes platform-specific workarounds, such as bypassing the Windows WMI
deadlock in platform.uname() under Python 3.13+ / botocore subprocesses.
"""

import os
import platform
from collections import namedtuple

_patches_applied = False


def apply_platform_patches() -> None:
    """Apply platform workarounds safely and idempotently."""
    global _patches_applied
    if _patches_applied:
        return

    if os.name == "nt":
        # Bypass WMI hang in botocore/platform.uname on Windows
        try:
            uname_tuple = namedtuple(
                "uname_result",
                ["system", "node", "release", "version", "machine", "processor"]
            )
            platform.uname = lambda: uname_tuple("Windows", "", "10", "10.0.0", "AMD64", "")
        except Exception:
            pass

    _patches_applied = True


# Apply immediately upon module import
apply_platform_patches()
