"""Windows sleep prevention via SetThreadExecutionState.

No-op on non-Windows platforms.
"""

from __future__ import annotations

import sys

if sys.platform == "win32":
    import ctypes

    _ES_CONTINUOUS: int = 0x80000000
    _ES_SYSTEM_REQUIRED: int = 0x00000001

    def prevent_sleep() -> None:
        ctypes.windll.kernel32.SetThreadExecutionState(  # type: ignore[attr-defined]
            _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED
        )

    def allow_sleep() -> None:
        ctypes.windll.kernel32.SetThreadExecutionState(  # type: ignore[attr-defined]
            _ES_CONTINUOUS
        )

else:

    def prevent_sleep() -> None:
        pass

    def allow_sleep() -> None:
        pass
