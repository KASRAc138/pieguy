"""Continuously identifies the UI element under the mouse (while recording / picking).

- feeds the live highlight ("PieGuy sees: Button 'Set system proxy' · v2rayN")
- keeps a fresh snapshot so a click on a dropdown / menu item is recorded from
  what was under the mouse *before* the click (the popup may vanish right after)
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque

from . import win32 as W
from . import uia


class Probe(threading.Thread):
    def __init__(self, emit):
        super().__init__(daemon=True, name="PieGuyProbe")
        self.emit = emit                 # emit(desc | None), called on this thread
        self.active = threading.Event()
        self._halt = False
        self._lock = threading.Lock()
        self._cache = None               # (x, y, t, desc)
        self._hist = deque(maxlen=40)    # [t_start, t_end, key, desc] of distinct elements hovered
        self._pid = os.getpid()

    def run(self):
        last, last_t = None, 0.0
        while not self._halt:
            if not self.active.wait(0.5):
                last = None
                continue
            try:
                pos = W.cursor_pos()
                now = time.time()
                if pos != last or now - last_t > 0.7:
                    desc = uia.describe_point(*pos)
                    last, last_t = pos, now
                    own = desc.get("hwnd") and W.window_pid(desc["hwnd"]) == self._pid
                    with self._lock:
                        self._cache = None if own else (pos[0], pos[1], now, desc)
                        if not own:
                            el = desc.get("element") or {}
                            key = (desc.get("hwnd"), el.get("name"), el.get("type"), tuple(el.get("rect") or ()))
                            if self._hist and self._hist[-1][2] == key:
                                self._hist[-1][1] = now
                            else:
                                self._hist.append([now, now, key, desc])
                    self.emit(None if own else desc)
            except Exception:
                pass
            time.sleep(0.06)

    def start_probe(self):
        with self._lock:
            self._cache = None
            self._hist.clear()
        self.active.set()

    def stop_probe(self):
        self.active.clear()
        self.emit(None)

    def shutdown(self):
        self._halt = True
        self.active.set()

    def hover_parent(self, clicked: dict, window_s: float = 6.0, dwell: float = 0.25):
        """The menu item you rested on to open the submenu that `clicked` lives in."""
        el = clicked.get("element") or {}
        if el.get("type_name") != "MenuItemControl":
            return None
        now = time.time()
        with self._lock:
            hist = list(self._hist)
        for t0, t1, key, d in reversed(hist):
            if now - t1 > window_s:
                break
            de = d.get("element") or {}
            if (de.get("type_name") == "MenuItemControl" and d.get("hwnd") != clicked.get("hwnd")
                    and t1 - t0 >= dwell and de.get("name")):
                return dict(d)
        return None

    def lookup(self, x, y, max_age=1.5):
        """Snapshot taken at (about) this point just before now, or None."""
        with self._lock:
            c = self._cache
        if c and abs(c[0] - x) <= 4 and abs(c[1] - y) <= 4 and time.time() - c[2] <= max_age:
            return dict(c[3])
        return None
