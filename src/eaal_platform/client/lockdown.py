"""Keeping a student inside the lab window while a lab is being taken.

When a lab starts the window goes full screen and the operating system's
ways of switching away are switched off where an ordinary app is allowed to
do that:

* **macOS**: the app asks for "kiosk" presentation options (no Dock, no menu
  bar, no app switching with Cmd+Tab, no Force Quit panel). No permission
  prompt is involved.
* **Windows**: a low-level keyboard hook swallows Alt+Tab, Alt+Esc, Alt+F4,
  Ctrl+Esc and the Windows keys. No administrator rights are involved.

Neither system lets an app block everything (Ctrl+Alt+Del on Windows and
some trackpad gestures on macOS still work), so the lab screen also notices
when it loses focus and counts that; see ``CavyApi.record_focus_lost``.
Everything here undoes itself when the lab ends or the app quits.

``CAVY_NO_LOCKDOWN=1`` turns the whole thing off (for development).
"""

from __future__ import annotations

import contextlib
import os
import sys
import threading
from collections.abc import Callable
from typing import Any, Protocol

# Windows virtual-key codes and the low-level hook's "Alt is held" flag.
VK_TAB = 0x09
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_ESCAPE = 0x1B
VK_F4 = 0x73
VK_LWIN = 0x5B
VK_RWIN = 0x5C
LLKHF_ALTDOWN = 0x20


def should_block(vk_code: int, flags: int, ctrl_down: bool, shift_down: bool) -> bool:
    """Whether a key press is one that would take the student out of the lab (Windows)."""
    if vk_code in (VK_LWIN, VK_RWIN):
        return True
    if flags & LLKHF_ALTDOWN and vk_code in (VK_TAB, VK_ESCAPE, VK_F4):
        return True
    # Ctrl+Shift+Esc (Task Manager) is left alone: it is the way out if the app ever hangs.
    return vk_code == VK_ESCAPE and ctrl_down and not shift_down


class KeyLock(Protocol):
    def engage(self) -> bool: ...

    def release(self) -> None: ...


class _MacKeyLock:
    def __init__(self) -> None:
        self._on = False

    def _set(self, options: int) -> None:
        from AppKit import NSApplication
        from PyObjCTools import AppHelper

        # AppKit must be driven from the main thread; the JS bridge calls us from another.
        AppHelper.callAfter(NSApplication.sharedApplication().setPresentationOptions_, options)

    def engage(self) -> bool:
        from AppKit import (
            NSApplicationPresentationDisableForceQuit,
            NSApplicationPresentationDisableHideApplication,
            NSApplicationPresentationDisableProcessSwitching,
            NSApplicationPresentationHideDock,
            NSApplicationPresentationHideMenuBar,
        )

        try:
            self._set(
                NSApplicationPresentationHideDock
                | NSApplicationPresentationHideMenuBar
                | NSApplicationPresentationDisableProcessSwitching
                | NSApplicationPresentationDisableForceQuit
                | NSApplicationPresentationDisableHideApplication
            )
        except Exception:  # nosec B110 -- best effort; the focus check still applies
            return False
        self._on = True
        return True

    def release(self) -> None:
        if not self._on:
            return
        self._on = False
        with contextlib.suppress(Exception):
            self._set(0)


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _WH_KEYBOARD_LL = 13
    _WM_KEYDOWN = 0x0100
    _WM_SYSKEYDOWN = 0x0104
    _WM_QUIT = 0x0012
    _HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

    class _KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = (
            ("vkCode", wintypes.DWORD),
            ("scanCode", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        )

    class _WindowsKeyLock:
        """Runs the keyboard hook on its own thread (a hook needs a message loop)."""

        def __init__(self) -> None:
            self._thread: threading.Thread | None = None
            self._thread_id = 0
            self._ready = threading.Event()
            self._installed = False

        def _callback(self, code: int, w_param: int, l_param: int) -> int:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            if code >= 0 and w_param in (_WM_KEYDOWN, _WM_SYSKEYDOWN):
                key = ctypes.cast(l_param, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
                ctrl = bool(user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
                shift = bool(user32.GetAsyncKeyState(VK_SHIFT) & 0x8000)
                if should_block(key.vkCode, key.flags, ctrl, shift):
                    return 1
            return int(user32.CallNextHookEx(None, code, w_param, l_param))

        def _run(self) -> None:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            user32.SetWindowsHookExW.argtypes = (
                ctypes.c_int,
                _HOOKPROC,
                wintypes.HINSTANCE,
                wintypes.DWORD,
            )
            user32.SetWindowsHookExW.restype = ctypes.c_void_p
            user32.CallNextHookEx.argtypes = (
                ctypes.c_void_p,
                ctypes.c_int,
                wintypes.WPARAM,
                wintypes.LPARAM,
            )
            user32.CallNextHookEx.restype = ctypes.c_ssize_t
            user32.UnhookWindowsHookEx.argtypes = (ctypes.c_void_p,)
            kernel32.GetModuleHandleW.restype = wintypes.HMODULE
            self._thread_id = kernel32.GetCurrentThreadId()
            procedure = _HOOKPROC(self._callback)  # must stay referenced while hooked
            hook = user32.SetWindowsHookExW(
                _WH_KEYBOARD_LL, procedure, kernel32.GetModuleHandleW(None), 0
            )
            self._installed = bool(hook)
            self._ready.set()
            if not hook:
                return
            message = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
            user32.UnhookWindowsHookEx(hook)

        def engage(self) -> bool:
            if self._thread is not None:
                return self._installed
            self._ready.clear()
            self._thread = threading.Thread(target=self._run, name="cavy-key-lock", daemon=True)
            self._thread.start()
            self._ready.wait(3)
            return self._installed

        def release(self) -> None:
            thread, self._thread = self._thread, None
            if thread is None:
                return
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.PostThreadMessageW(self._thread_id, _WM_QUIT, 0, 0)
            thread.join(2)
            self._installed = False


def platform_key_lock() -> KeyLock | None:
    if os.environ.get("CAVY_NO_LOCKDOWN"):
        return None
    if sys.platform == "darwin":
        return _MacKeyLock()
    if sys.platform == "win32":
        return _WindowsKeyLock()
    return None


class LabLock:
    """Full screen plus the key lock, switched on and off as a pair."""

    def __init__(
        self,
        window: Callable[[], Any] | None = None,
        key_lock: KeyLock | None = None,
        enabled: bool | None = None,
    ) -> None:
        self._window = window or _first_window
        self._keys = key_lock if key_lock is not None else platform_key_lock()
        self._enabled = enabled if enabled is not None else not os.environ.get("CAVY_NO_LOCKDOWN")
        self._fullscreen = False
        self._engaged = False

    def engage(self) -> dict[str, bool]:
        if not self._enabled:
            return {"fullscreen": False, "keys_blocked": False}
        keys_blocked = self._keys.engage() if self._keys is not None else False
        if not self._fullscreen:
            window = self._window()
            if window is not None:
                try:
                    window.toggle_fullscreen()
                    self._fullscreen = True
                except Exception:  # nosec B110 -- the focus check is the safety net
                    pass
        self._engaged = True
        return {"fullscreen": self._fullscreen, "keys_blocked": keys_blocked}

    def release(self) -> None:
        if not self._engaged:
            return
        self._engaged = False
        if self._keys is not None:
            self._keys.release()
        if self._fullscreen:
            window = self._window()
            if window is not None:
                with contextlib.suppress(Exception):
                    window.toggle_fullscreen()
            self._fullscreen = False

    @property
    def engaged(self) -> bool:
        return self._engaged


def _first_window() -> Any:
    try:
        import webview

        return webview.windows[0] if webview.windows else None
    except Exception:
        return None
