"""Turns raw input events into editable, *semantic* steps.

- typing becomes "Type text" (backspaces edit the buffer instead of being replayed)
- shortcuts become "Press keys"
- clicks become "Click element" (UI Automation name/type/id + app window, incl.
  dropdown lists / menus / tray menus, with named-parent + position fallbacks)
- starting a new program becomes "Launch app" (Start-menu / taskbar clicks that led
  to it are dropped), switching windows becomes "Focus window"
- pauses become editable "Wait" steps
"""
from __future__ import annotations

import threading
import time

from . import win32 as W
from . import uia
from .config import uid

SHELL_PROCS = {"explorer.exe", "startmenuexperiencehost.exe", "searchhost.exe", "searchapp.exe",
               "shellexperiencehost.exe", "searchui.exe", "textinputhost.exe"}
_TOKEN: dict[int, str] = {}
for _k, _v in W.VK.items():
    _TOKEN.setdefault(_v, _k)  # first name listed is the canonical one


def token(vk: int) -> str:
    return _TOKEN.get(vk, "")


def step_label(s: dict) -> str:
    t = s.get("type")
    if t == "click_element":
        tg = s.get("target") or {}
        who = (tg.get("label") or uia.element_label(tg)) if tg else "…"
        return ("Double-click " if s.get("double") else "Click ") + who
    if t == "type_text":
        return f"Type “{s.get('text', '')[:40]}”"
    if t == "hotkey":
        return f"Keys {s.get('keys')}"
    if t == "launch":
        return f"Launch {s.get('path', '').split(chr(92))[-1]}"
    if t == "focus_window":
        return f"Switch to {s.get('process')}"
    if t == "scroll":
        return f"Scroll {s.get('amount')}"
    if t == "drag":
        return "Drag"
    if t == "hover":
        return "Hover " + ((s.get("target") or {}).get("label") or "menu item")
    return t or ""


class Recorder:
    def __init__(self, ignore_rect=None, probe_lookup=None, on_step=None, probe_hover=None):
        self.steps: list[dict] = []
        self.ignore_rect = ignore_rect    # callable -> (l, t, r, b) of our REC HUD
        self.probe_lookup = probe_lookup  # callable(x, y) -> desc snapshot or None
        self.on_step = on_step            # callable(step, label) for the HUD / ripples
        self.probe_hover = probe_hover    # callable(desc) -> parent menu item hovered to open a submenu
        self._text = None
        self._last_t = time.time()
        self._down = None
        self._threads: list[threading.Thread] = []
        self._initial = W.running_processes()
        self._launched = set()
        self._last_fg = W.fg_window()
        self._last_click_t = 0.0
        self._wheel = None
        self.active = True

    # ------------------------------------------------------------- helpers
    def _add(self, step, t=None, proc=None):
        t = t or time.time()
        gap = t - self._last_t
        if gap > 1.5 and self.steps:
            self.steps.append({"id": uid("s"), "type": "wait", "enabled": True,
                               "ms": int(min(gap, 3.0) * 10) * 100, "_proc": proc})
        self._last_t = t
        step.setdefault("id", uid("s"))
        step.setdefault("enabled", True)
        step["_proc"] = proc if proc is not None else W.window_process(W.fg_window())
        self.steps.append(step)
        self._notify(step)
        return step

    def _notify(self, step):
        if self.on_step:
            try:
                self.on_step(step, step_label(step), sum(1 for s in self.steps if s["type"] != "wait"))
            except Exception:
                pass

    def _flush_text(self):
        if self._text is not None and not self._text["text"] and self._text in self.steps:
            self.steps.remove(self._text)
        self._text = None

    def _describe_async(self, x, y) -> dict:
        """Snapshot for (x, y): the probe's pre-click view if fresh, else describe now."""
        snap = self.probe_lookup(x, y) if self.probe_lookup else None
        if snap:
            return snap
        box = {}
        th = threading.Thread(target=lambda: box.update(uia.describe_point(x, y)), daemon=True)
        th.start()
        self._threads.append(th)
        return box

    # ---------------------------------------------------------------- keys
    def on_key(self, e):
        vk, ch, mods = e["vk"], e["char"], set(e["mods"])
        if vk in W.MODIFIER_VKS:
            return
        self._wheel = None
        strong = mods & {"ctrl", "alt", "win"}
        altgr = {"ctrl", "alt"} <= mods and ch and ch.isprintable()
        if (not strong or altgr) and ch and ch.isprintable() and vk not in (0x0D, 0x09):
            if self._text is None:
                self._text = self._add({"type": "type_text", "text": "", "paste": False}, e["t"])
            self._text["text"] += ch
            self._last_t = e["t"]
            self._notify(self._text)
            return
        if vk == 0x08 and not strong and self._text is not None and self._text["text"]:
            self._text["text"] = self._text["text"][:-1]
            self._notify(self._text)
            return
        self._flush_text()
        tok = token(vk)
        if not tok:
            return
        combo = "+".join([m for m in ("ctrl", "shift", "alt", "win") if m in mods] + [tok])
        last = self.steps[-1] if self.steps else None
        if last and last["type"] == "hotkey" and e["t"] - self._last_t < 1.0 and \
                last["keys"].split(" ")[-1] == combo:
            last["keys"] += " " + combo
            self._last_t = e["t"]
            self._notify(last)
            return
        self._add({"type": "hotkey", "keys": combo}, e["t"])

    # --------------------------------------------------------------- mouse
    def _ignored(self, x, y):
        if not self.ignore_rect:
            return False
        l, t, r, b = self.ignore_rect()
        return l <= x <= r and t <= y <= b

    def on_mouse(self, e):
        x, y = e["x"], e["y"]
        if self._ignored(x, y):
            self._down = None
            return
        if e["down"]:
            self._flush_text()
            self._wheel = None
            self._down = (x, y, e["button"], e["t"], self._describe_async(x, y))
            self._last_click_t = e["t"]
            return
        if not self._down or self._down[2] != e["button"]:
            return
        x0, y0, button, t0, box = self._down
        self._down = None
        if abs(x - x0) + abs(y - y0) > 14 and button == "left":
            self._add({"type": "drag", "from": box, "to": self._describe_async(x, y)}, t0)
            return
        last = self.steps[-1] if self.steps else None
        if last and last["type"] == "click_element" and not last.get("double") and \
                t0 - last.get("_t", 0) < 0.45 and abs(last.get("_x", -99) - x0) < 6 and abs(last.get("_y", -99) - y0) < 6:
            last["double"] = True
            self._notify(last)
            return
        parent = self.probe_hover(box) if self.probe_hover and box else None
        if parent and not (last and last["type"] == "hover" and
                           (last["target"].get("element") or {}).get("name") == parent["element"].get("name")):
            self._add({"type": "hover", "target": parent, "ms": 450, "timeout": 5}, t0)
        self._add({"type": "click_element", "target": box, "button": button, "double": False,
                   "method": "element", "timeout": 8, "_t": t0, "_x": x0, "_y": y0}, t0)

    def on_wheel(self, e):
        if self._ignored(e["x"], e["y"]):
            return
        self._flush_text()
        notches = int(round(e["delta"] / 120)) or (1 if e["delta"] > 0 else -1)
        if self._wheel is not None and e["t"] - self._last_t < 1.0:
            self._wheel["amount"] += notches
            self._last_t = e["t"]
            return
        desc = {"window": {}, "rx": 0.5, "ry": 0.5}
        try:
            hwnd = W.top_level(W.user32.WindowFromPoint(W.wt.POINT(e["x"], e["y"])))
            l, t, r, b = W.window_rect(hwnd)
            desc = {"window": {"title": W.window_title(hwnd), "process": W.window_process(hwnd),
                               "cls": W.window_class(hwnd)},
                    "rx": round((e["x"] - l) / max(1, r - l), 4), "ry": round((e["y"] - t) / max(1, b - t), 4)}
        except Exception:
            pass
        self._wheel = self._add({"type": "scroll", "amount": notches, "target": desc}, e["t"])

    # --------------------------------------------------------- focus poll
    def poll_foreground(self):
        h = W.fg_window()
        if not h or h == self._last_fg:
            return
        self._last_fg = h
        proc = W.window_process(h)
        if not proc or proc in ("python.exe", "pythonw.exe"):
            return
        if proc not in self._initial and proc not in self._launched and proc not in SHELL_PROCS:
            self._launched.add(proc)
            self._flush_text()
            # the Start-menu / taskbar / search steps that led here are replaced by one launch
            while self.steps and self.steps[-1].get("_proc") in SHELL_PROCS | {""}:
                self.steps.pop()
            while self.steps and self.steps[-1]["type"] == "wait":
                self.steps.pop()
            self._add({"type": "launch", "path": W.window_exe(h) or proc, "args": "",
                       "if_not_running": True, "wait_window": True, "admin": False}, proc=proc)
            return
        if time.time() - self._last_click_t < 0.6:
            return  # the click step already brings that window forward
        # menus / hidden tray windows / popups take focus briefly - not a real "switch"
        if not W.window_title(h) or W.is_popup(h) or not W.user32.IsWindowVisible(h):
            return
        self._flush_text()
        self._add({"type": "focus_window", "process": proc, "title": "", "timeout": 4,
                   "on_error": "continue"}, proc=proc)

    # ---------------------------------------------------------------- stop
    def finish(self) -> list[dict]:
        self.active = False
        self._flush_text()
        for th in self._threads:
            th.join(timeout=1.5)
        out = []
        for s in self.steps:
            s = {k: v for k, v in s.items() if not k.startswith("_")}
            if s["type"] == "type_text" and not s["text"]:
                continue
            if s["type"] == "click_element":
                s["target"] = uia.slim(s.get("target") or {})
                s["summary"] = uia.element_label(s["target"])
                if not s["target"].get("element"):
                    s["method"] = "position"  # nothing identifiable (e.g. admin app, canvas)
            if s["type"] == "hover":
                s["target"] = uia.slim(s.get("target") or {})
                s["summary"] = "Hover " + uia.element_label(s["target"])
            if s["type"] == "drag":
                s["from"], s["to"] = uia.slim(s.get("from") or {}), uia.slim(s.get("to") or {})
            out.append(s)
        while out and out[-1]["type"] == "wait":
            out.pop()
        return out
