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

    _user32_cache: Any = None

    def _user32() -> Any:
        """One shared ``user32`` with every function's argument types set (64-bit pointers)."""
        global _user32_cache
        if _user32_cache is None:
            lib = ctypes.WinDLL("user32", use_last_error=True)
            lib.SetWindowsHookExW.argtypes = (
                ctypes.c_int,
                _HOOKPROC,
                wintypes.HINSTANCE,
                wintypes.DWORD,
            )
            lib.SetWindowsHookExW.restype = ctypes.c_void_p
            lib.CallNextHookEx.argtypes = (
                ctypes.c_void_p,
                ctypes.c_int,
                wintypes.WPARAM,
                wintypes.LPARAM,
            )
            lib.CallNextHookEx.restype = ctypes.c_ssize_t
            lib.UnhookWindowsHookEx.argtypes = (ctypes.c_void_p,)
            lib.GetAsyncKeyState.argtypes = (ctypes.c_int,)
            lib.GetAsyncKeyState.restype = ctypes.c_short
            lib.GetMessageW.argtypes = (
                ctypes.POINTER(wintypes.MSG),
                wintypes.HWND,
                wintypes.UINT,
                wintypes.UINT,
            )
            lib.PostThreadMessageW.argtypes = (
                wintypes.DWORD,
                wintypes.UINT,
                wintypes.WPARAM,
                wintypes.LPARAM,
            )
            _user32_cache = lib
        return _user32_cache

    class _WindowsKeyLock:
        """Runs the keyboard hook on its own thread (a hook needs a message loop)."""

        def __init__(self) -> None:
            self._thread: threading.Thread | None = None
            self._thread_id = 0
            self._ready = threading.Event()
            self._installed = False

        def _callback(self, code: int, w_param: int, l_param: int) -> int:
            # A hook sits in front of every key press on the computer, so this must never
            # raise or hold anything up: whatever goes wrong, the key is let through.
            lib = _user32()
            try:
                if code >= 0 and w_param in (_WM_KEYDOWN, _WM_SYSKEYDOWN):
                    key = ctypes.cast(l_param, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
                    ctrl = bool(lib.GetAsyncKeyState(VK_CONTROL) & 0x8000)
                    shift = bool(lib.GetAsyncKeyState(VK_SHIFT) & 0x8000)
                    if should_block(key.vkCode, key.flags, ctrl, shift):
                        return 1
            except Exception:  # nosec B110 - see above
                pass
            return int(lib.CallNextHookEx(None, code, w_param, l_param))

        def _run(self) -> None:
            lib = _user32()
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.GetModuleHandleW.restype = wintypes.HMODULE
            self._thread_id = kernel32.GetCurrentThreadId()
            procedure = _HOOKPROC(self._callback)  # must stay referenced while hooked
            hook = lib.SetWindowsHookExW(
                _WH_KEYBOARD_LL, procedure, kernel32.GetModuleHandleW(None), 0
            )
            self._installed = bool(hook)
            self._ready.set()
            if not hook:
                return
            message = wintypes.MSG()
            while lib.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                lib.TranslateMessage(ctypes.byref(message))
                lib.DispatchMessageW(ctypes.byref(message))
            lib.UnhookWindowsHookEx(hook)

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
            _user32().PostThreadMessageW(self._thread_id, _WM_QUIT, 0, 0)
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


def give_keyboard_to_web_view(window: Any) -> None:
    """Hand the keyboard back to the page.

    Going full screen changes the native window underneath the web view, and the
    keyboard can be left with the window frame instead of the page, so keys do
    nothing until this is done. Safe to call at any time; it never raises.
    """
    native = getattr(window, "native", None)
    if native is None:
        return
    try:
        if sys.platform == "win32":
            from System import Action  # type: ignore[import-not-found]  # via pythonnet

            def focus_windows() -> None:
                native.Activate()
                native.browser.webview.Focus()

            native.Invoke(Action(focus_windows))
        elif sys.platform == "darwin":
            from AppKit import NSApplication
            from PyObjCTools import AppHelper

            def focus_mac() -> None:
                NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
                native.makeKeyAndOrderFront_(None)
                view = native.contentView()
                if view is not None:
                    native.makeFirstResponder_(view)

            AppHelper.callAfter(focus_mac)
    except Exception:  # nosec B110 - best effort
        pass


def _later(seconds: float, work: Callable[[], None]) -> None:
    timer = threading.Timer(seconds, work)
    timer.daemon = True
    timer.start()


class LabLock:
    """Full screen plus the key lock, switched on and off as a pair."""

    # After the window changes size the keyboard is handed back to the page a few times,
    # because the change finishes at different moments on different computers.
    _REFOCUS_AT = (0.3, 1.0, 2.5)

    def __init__(
        self,
        window: Callable[[], Any] | None = None,
        key_lock: KeyLock | None = None,
        enabled: bool | None = None,
        schedule: Callable[[float, Callable[[], None]], None] | None = None,
        refocus: Callable[[Any], None] | None = None,
        keys_delay: float | None = None,
    ) -> None:
        self._window = window or _first_window
        self._keys = key_lock if key_lock is not None else platform_key_lock()
        self._enabled = enabled if enabled is not None else not os.environ.get("CAVY_NO_LOCKDOWN")
        self._schedule = schedule or _later
        self._refocus = refocus or give_keyboard_to_web_view
        # On a Mac the kiosk options wait until the full-screen move has finished:
        # asking for both at once can leave the window without the keyboard.
        self._keys_delay = (
            keys_delay if keys_delay is not None else (1.5 if sys.platform == "darwin" else 0.0)
        )
        self._fullscreen = False
        self._engaged = False

    def _give_keyboard_back(self) -> None:
        for delay in self._REFOCUS_AT:
            self._schedule(delay, lambda: self._refocus(self._window()))

    def refocus(self) -> None:
        """Called by the page when the person clicks: make sure keys reach it."""
        if self._engaged:
            self._refocus(self._window())

    def engage(self) -> dict[str, bool]:
        if not self._enabled:
            return {"fullscreen": False, "keys_blocked": False}
        if not self._fullscreen:
            window = self._window()
            if window is not None:
                try:
                    window.toggle_fullscreen()
                    self._fullscreen = True
                except Exception:  # nosec B110 -- the focus check is the safety net
                    pass
        self._engaged = True
        keys_blocked = False
        if self._keys is not None:
            if self._keys_delay > 0:
                self._schedule(self._keys_delay, self._start_keys)
                keys_blocked = True  # on its way
            else:
                keys_blocked = self._keys.engage()
        self._give_keyboard_back()
        return {"fullscreen": self._fullscreen, "keys_blocked": keys_blocked}

    def _start_keys(self) -> None:
        if self._engaged and self._keys is not None:
            self._keys.engage()
            self._give_keyboard_back()

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
            self._give_keyboard_back_after_release()

    def _give_keyboard_back_after_release(self) -> None:
        for delay in self._REFOCUS_AT:
            self._schedule(delay, lambda: self._refocus(self._window()))

    @property
    def engaged(self) -> bool:
        return self._engaged


def _first_window() -> Any:
    try:
        import webview

        return webview.windows[0] if webview.windows else None
    except Exception:
        return None
