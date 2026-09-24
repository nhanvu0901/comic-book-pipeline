"""Copy text to the viewer's clipboard — and report honestly whether it worked.

The UI is normally opened as http://<lan-ip>:8550. Browsers only expose the clipboard
API to secure origins (https or localhost), so on that address every copy fails with
PlatformException(copy_fail). The screens used to announce "Copied!" before the copy had
even run; they now await the result and point at the manual route when it fails.
"""
from __future__ import annotations

import flet as ft

BLOCKED_HINT = (
    "the browser blocks the clipboard on this http:// address — select the text and "
    "press Ctrl/Cmd+C instead"
)


async def copy_text(clipboard: ft.Clipboard, text: str) -> bool:
    """True only when the browser actually took the text."""
    try:
        await clipboard.set(text)
    except Exception:  # noqa: BLE001 - PlatformException from the client; any failure is "not copied"
        return False
    return True


def ensure_attached(page: ft.Page, clipboard: ft.Clipboard) -> None:
    """Register the Clipboard service on the page once (it is a service, not a control)."""
    try:
        services = page.services
        if clipboard not in services:
            services.append(clipboard)
    except Exception:  # noqa: BLE001 - a page double without services: copy then just fails
        pass
