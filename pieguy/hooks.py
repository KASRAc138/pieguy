"""Global low-level keyboard + mouse hook running on its own thread.

The callback must stay tiny (Windows drops slow LL hooks), so it only
decides "swallow or pass" and forwards events through `emit(kind, data)`.
"""
from __future__ import annotations

import ctypes
import threading
import time

from . import win32 as W

WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
WM_LBUTTONDOWN, WM_LBUTTONUP, WM_RBUTTONDOWN, WM_RBUTTONUP = 0x201, 0x202, 0x204, 0x205
WM_MBUTTONDOWN, WM_MBUTTONUP, WM_MOUSEWHEEL, WM_XBUTTONDOWN, WM_XBUTTONUP = 0x207, 0x208, 0x20A, 0x20B, 0x20C
PIE_KEYS = {0x1B: "esc", 0x08: "back", 0x0D: "enter", 0x25: "left", 0x27: "right", 0x26: "up", 0x28: "down"}
PIE_KEYS.update({0x31 + i: str(i + 1) for i in range(9)})
PIE_KEYS.update({0x61 + i: str(i + 1) for i in range(9)})
STOP_REC_VK = 0x13  # Pause/Break


class InputHook(threading.Thread):
    def __init__(self, emit):
        super().__init__(daemon=True, name="PieGuyHook")
        self.emit = emit
        self.triggers: list[dict] = []     # [{pie, kind, vk, button, mods:set, apps:set}]
        self.excluded: set[str] = set()
        self.paused = False
        self.pie_open = False
        self.capture = False
        self.recording = False
        self.picking = False
        self._held = None                  # ('key', vk) | ('mouse', button)
        self._held_pie = None
        self._held_t = 0.0
        self._swallow_up = set()
        self._tid = 0
        self._procs = []

    # ------------------------------------------------------------ lifecycle
    def run(self):
        self._tid = W.kernel32.GetCurrentThreadId()
        kb = W.HOOKPROC(self._kb)
        ms = W.HOOKPROC(self._ms)
        self._procs = [kb, ms]
        hmod = W.kernel32.GetModuleHandleW(None)
        self._hk = W.user32.SetWindowsHookExW(13, kb, hmod, 0)  # WH_KEYBOARD_LL
        self._hm = W.user32.SetWindowsHookExW(14, ms, hmod, 0)  # WH_MOUSE_LL
        msg = W.wt.MSG()
        while W.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass
        W.user32.UnhookWindowsHookEx(self._hk)
        W.user32.UnhookWindowsHookEx(self._hm)

    def stop(self):
        if self._tid:
            W.user32.PostThreadMessageW(self._tid, 0x12, 0, 0)  # WM_QUIT

    # -------------------------------------------------------------- helpers
    def _fg_proc(self) -> str:
        try:
            return W.window_process(W.user32.GetForegroundWindow())
        except Exception:
            return ""

    def _match(self, kind, code):
        if self.paused or not self.triggers:
            return None
        if not any(t["kind"] == kind and (t["vk"] if kind == "key" else t["button"]) == code for t in self.triggers):
            return None  # fast path: this key/button isn't a trigger at all
        need_proc = self.excluded or any(t["apps"] for t in self.triggers)
        proc = self._fg_proc() if need_proc else ""
        if proc and proc in self.excluded:
            return None
        mods = W.mods_down() if kind == "key" else set()
        best = None
        for t in self.triggers:
            if t["kind"] != kind:
                continue
            if kind == "key" and t["vk"] != code:
                continue
            if kind == "mouse" and t["button"] != code:
                continue
            if kind == "key" and not t["mods"] <= mods:
                continue
            if t["apps"]:
                if proc in t["apps"]:
                    return t["pie"]  # app-specific pie wins
                continue
            best = best or t["pie"]
        return best

    # ------------------------------------------------------------ keyboard
    def _kb(self, code, wparam, lparam):
        try:
            if code == 0:
                k = ctypes.cast(lparam, ctypes.POINTER(W.KBDLLHOOKSTRUCT)).contents
                if k.dwExtraInfo != W.MAGIC and self._on_key(k.vkCode, k.scanCode, k.flags,
                                                             wparam in (WM_KEYDOWN, WM_SYSKEYDOWN)):
                    return 1
        except Exception:
            pass
        return W.user32.CallNextHookEx(None, code, wparam, lparam)

    def _on_key(self, vk, scan, flags, down) -> bool:
        if not down and vk in self._swallow_up:
            self._swallow_up.discard(vk)
            return True
        if self.capture and down:
            if vk in W.MODIFIER_VKS and vk not in (0xA3, 0xA5):
                return False  # wait for the real key; mods are read with it
            self.capture = False
            self._swallow_up.add(vk)
            if vk == 0x1B:
                self.emit("captured", None)
            else:
                mods = sorted(W.mods_down() - {"ctrl" if vk == 0xA3 else "", "alt" if vk == 0xA5 else ""})
                self.emit("captured", {"kind": "key", "vk": vk, "mods": mods})
            return True
        if self.picking and down and vk == 0x1B:
            self.picking = False
            self._swallow_up.add(vk)
            self.emit("pick", None)
            return True
        if self.recording:
            if down and vk == STOP_REC_VK:
                self._swallow_up.add(vk)
                self.emit("rec_stop", None)
                return True
            if down:
                ch = "" if vk in W.MODIFIER_VKS else W.vk_to_char(vk, scan)
                self.emit("rec_key", {"vk": vk, "char": ch, "mods": sorted(W.mods_down()), "t": time.time()})
            return False
        # held trigger key
        if self._held == ("key", vk):
            if down:
                return True  # autorepeat
            self._held = None
            self.emit("trigger_up", {"pie": self._held_pie, "ms": (time.time() - self._held_t) * 1000})
            return True
        if self.pie_open and down and vk in PIE_KEYS:
            self._swallow_up.add(vk)
            self.emit("pie_key", PIE_KEYS[vk])
            return True
        if down and self._held is None:
            pie = self._match("key", vk)
            if pie:
                self._held, self._held_pie, self._held_t = ("key", vk), pie, time.time()
                self.emit("trigger_down", {"pie": pie, "kind": "key", "vk": vk})
                return True
        return False

    # --------------------------------------------------------------- mouse
    def _ms(self, code, wparam, lparam):
        try:
            if code == 0:
                m = ctypes.cast(lparam, ctypes.POINTER(W.MSLLHOOKSTRUCT)).contents
                if m.dwExtraInfo != W.MAGIC and self._on_mouse(wparam, m.pt.x, m.pt.y, m.mouseData):
                    return 1
        except Exception:
            pass
        return W.user32.CallNextHookEx(None, code, wparam, lparam)

    def _on_mouse(self, msg, x, y, data) -> bool:
        if msg == 0x200:  # move: never touch
            return False
        button, down = None, None
        if msg in (WM_LBUTTONDOWN, WM_LBUTTONUP):
            button, down = "left", msg == WM_LBUTTONDOWN
        elif msg in (WM_RBUTTONDOWN, WM_RBUTTONUP):
            button, down = "right", msg == WM_RBUTTONDOWN
        elif msg in (WM_MBUTTONDOWN, WM_MBUTTONUP):
            button, down = "middle", msg == WM_MBUTTONDOWN
        elif msg in (WM_XBUTTONDOWN, WM_XBUTTONUP):
            button, down = ("x1" if (data >> 16) == 1 else "x2"), msg == WM_XBUTTONDOWN
        if self.recording:
            if msg == WM_MOUSEWHEEL:
                delta = ctypes.c_short(data >> 16).value
                self.emit("rec_wheel", {"x": x, "y": y, "delta": delta, "t": time.time()})
            elif button:
                self.emit("rec_mouse", {"x": x, "y": y, "button": button, "down": down, "t": time.time()})
            return False
        if button is None:
            return False
        key = "m:" + button
        if not down and key in self._swallow_up:
            self._swallow_up.discard(key)
            return True
        if self.picking and down:
            self.picking = False
            self._swallow_up.add(key)
            self.emit("pick", {"x": x, "y": y} if button == "left" else None)
            return True
        if self.capture and down and button in ("middle", "x1", "x2"):
            self.capture = False
            self._swallow_up.add(key)
            self.emit("captured", {"kind": "mouse", "button": button, "mods": []})
            return True
        if self._held == ("mouse", button):
            if not down:
                self._held = None
                self.emit("trigger_up", {"pie": self._held_pie, "ms": (time.time() - self._held_t) * 1000})
            return True
        if down and self._held is None and button in ("middle", "x1", "x2"):
            pie = self._match("mouse", button)
            if pie:
                self._held, self._held_pie, self._held_t = ("mouse", button), pie, time.time()
                self.emit("trigger_down", {"pie": pie, "kind": "mouse", "button": button})
                return True
        return False
