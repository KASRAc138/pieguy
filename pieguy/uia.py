"""UI Automation: describe the element under a point, and find it again later.

Recordings are "semantic": we store *which* button / field / menu item you
used (name, type, automation id, owning app + window) plus fallbacks, instead
of raw screen coordinates. Replay searches every window of the app, including
untitled popups (dropdown lists, context menus, tray menus).
"""
from __future__ import annotations

import os
import re
import threading
import time

from . import win32 as W

try:
    import uiautomation as auto  # type: ignore
    HAVE_UIA = True
except Exception:  # not installed or not Windows
    auto = None
    HAVE_UIA = False

PROP_NAME, PROP_TYPE, PROP_AUTOID = 30005, 30003, 30011
SCOPE_DESCENDANTS = 4
TYPE_WINDOW, TYPE_PANE = 50032, 50033

_tls = threading.local()


def _ensure_com():
    """Init UIA/COM once per thread and never uninit (avoids releasing objects after CoUninitialize)."""
    if HAVE_UIA and not getattr(_tls, "ok", False):
        auto.InitializeUIAutomationInCurrentThread()
        _tls.ok = True


def _rect(c):
    r = c.BoundingRectangle
    return [r.left, r.top, r.right, r.bottom]


def _ratio(x, y, rect):
    l, t, r, b = rect
    return round((x - l) / max(1, r - l), 3), round((y - t) / max(1, b - t), 3)


# ------------------------------------------------------------------ record
_OWN_PID = os.getpid()


def _deepest_at(hwnd, x, y):
    """Walk down the UIA tree of `hwnd` to the smallest element containing (x, y)."""
    c = auto.ControlFromHandle(hwnd)
    for _ in range(30):
        nxt = None
        for ch in c.GetChildren()[:300]:
            r = ch.BoundingRectangle
            if r.left <= x < r.right and r.top <= y < r.bottom:
                nxt = ch
                break
        if nxt is None:
            break
        c = nxt
    return c


def describe_point(x: int, y: int) -> dict:
    """Snapshot of whatever is under (x, y). Always returns window info + ratios.
    PieGuy's own overlays are looked *through*, never recorded."""
    hwnd = W.top_level(W.user32.WindowFromPoint(W.wt.POINT(int(x), int(y))))
    if hwnd and W.window_pid(hwnd) == _OWN_PID:
        hwnd = W.window_at(int(x), int(y), skip_pid=_OWN_PID) or hwnd
    wrect = list(W.window_rect(hwnd))
    title = W.window_title(hwnd)
    d = {
        "window": {"title": title[:120], "process": W.window_process(hwnd), "cls": W.window_class(hwnd),
                   "popup": bool(W.is_popup(hwnd)) and not title},
        "rx": _ratio(x, y, wrect)[0], "ry": _ratio(x, y, wrect)[1],
        "x": x, "y": y, "wrect": wrect, "element": None,
        "blocked": W.uipi_blocked(hwnd), "hwnd": int(hwnd or 0),
    }
    if not HAVE_UIA or d["blocked"]:
        d["label"] = element_label(d)
        return d
    try:
        _ensure_com()
        c = auto.ControlFromPoint(int(x), int(y))
        if c and c.ProcessId == _OWN_PID and W.window_pid(hwnd) != _OWN_PID:
            c = _deepest_at(hwnd, int(x), int(y))
        if c and c.ProcessId != _OWN_PID:
            rect = _rect(c)
            ex, ey = _ratio(x, y, rect)
            el = {"name": (c.Name or "").strip()[:200], "type": c.ControlType, "type_name": c.ControlTypeName,
                  "auto_id": c.AutomationId or "", "cls": c.ClassName or "", "ex": ex, "ey": ey, "rect": rect}
            if not el["name"] and not el["auto_id"]:
                # icon-only button etc: remember the nearest named parent as an anchor
                p, depth = c, 0
                while depth < 4:
                    p = p.GetParentControl()
                    depth += 1
                    if not p or p.ControlType in (TYPE_WINDOW,):
                        break
                    if (p.Name or "").strip() or p.AutomationId:
                        prect = _rect(p)
                        ax, ay = _ratio(x, y, prect)
                        el["anchor"] = {"name": (p.Name or "").strip()[:200], "type": p.ControlType,
                                        "type_name": p.ControlTypeName, "auto_id": p.AutomationId or "",
                                        "ex": ax, "ey": ay}
                        break
            d["element"] = el
    except Exception:
        pass
    d["label"] = element_label(d)
    return d


def element_label(desc: dict) -> str:
    el = desc.get("element") or {}
    win = desc.get("window") or {}
    app = os.path.splitext(win.get("process") or "")[0] or "app"
    kind = (el.get("type_name") or "").replace("Control", "")
    name = el.get("name") or el.get("auto_id") or ""
    if name:
        return f"{kind or 'Element'} “{name[:40]}” · {app}"
    anc = el.get("anchor") or {}
    if anc.get("name") or anc.get("auto_id"):
        return f"{kind or 'Spot'} in “{(anc.get('name') or anc.get('auto_id'))[:32]}” · {app}"
    where = "popup" if win.get("popup") else "window"
    return f"{kind or 'Spot'} at {int(desc.get('rx', 0) * 100)}%,{int(desc.get('ry', 0) * 100)}% of {app} {where}"


def slim(desc: dict) -> dict:
    """What we store in a step (drop per-session handles)."""
    return {k: v for k, v in desc.items() if k not in ("hwnd", "wrect", "blocked")}


# ------------------------------------------------------------------ replay
def _norm(s: str) -> str:
    return re.sub(r"[\d\W_]+", " ", s or "").strip().lower()


def _find_in(hwnd, el: dict, fuzzy: bool):
    """FindFirst (native, fast) by id/name/type; optionally a fuzzy name match."""
    uia = auto._AutomationClient.instance().IUIAutomation
    root = auto.ControlFromHandle(hwnd)
    if not root:
        return None
    conds = []
    if el.get("auto_id"):
        conds.append(uia.CreatePropertyCondition(PROP_AUTOID, el["auto_id"]))
    if el.get("name"):
        conds.append(uia.CreatePropertyCondition(PROP_NAME, el["name"]))
    if conds:
        if el.get("type"):
            conds.append(uia.CreatePropertyCondition(PROP_TYPE, int(el["type"])))
        cond = conds[0]
        for c in conds[1:]:
            cond = uia.CreateAndCondition(cond, c)
        found = root.Element.FindFirst(SCOPE_DESCENDANTS, cond)
        if found:
            return auto.Control.CreateControlFromElement(found)
    if not fuzzy or not el.get("name"):
        return None
    # names with counters/speeds ("v2rayN - 12 KB/s") or small wording changes
    want = _norm(el["name"])
    if len(want) < 3:
        return None
    cond = uia.CreatePropertyCondition(PROP_TYPE, int(el["type"])) if el.get("type") else uia.CreateTrueCondition()
    arr = root.Element.FindAll(SCOPE_DESCENDANTS, cond)
    for i in range(min(arr.Length, 600)):
        e = arr.GetElement(i)
        got = _norm(e.CurrentName)
        if got and (got == want or got.startswith(want) or want.startswith(got) and len(got) >= 3):
            return auto.Control.CreateControlFromElement(e)
    return None


def _point_in(c, el):
    r = c.BoundingRectangle
    if r.right <= r.left or r.bottom <= r.top:
        return None
    return (int(r.left + (r.right - r.left) * float(el.get("ex", 0.5))),
            int(r.top + (r.bottom - r.top) * float(el.get("ey", 0.5))))


def candidate_windows(win: dict) -> list[int]:
    """All visible windows of the recorded app, best match first."""
    hs = W.windows_of(win.get("process", "")) if win.get("process") else []

    def score(h):
        s = 0
        if win.get("cls") and W.window_class(h) == win["cls"]:
            s += 2
        t = W.window_title(h)
        if win.get("title") and t and (win["title"].lower() in t.lower() or t.lower() in win["title"].lower()):
            s += 3
        if win.get("popup") and not t:
            s += 1
        return -s
    return sorted(hs, key=score)[:10]  # stable: keeps z-order within equal scores


def locate(desc: dict, timeout: float = 8.0, cancel=None, use_element=True, fallback=True):
    """Return (x, y, how, hwnd) for a recorded target, waiting for it to appear."""
    win = desc.get("window") or {}
    el = desc.get("element") or {}
    anchor = el.get("anchor") or {}
    has_id = el.get("name") or el.get("auto_id") or anchor.get("name") or anchor.get("auto_id")
    end = time.time() + timeout
    wins, tries = [], 0
    while True:
        if cancel is not None and cancel.is_set():
            return None
        wins = candidate_windows(win)
        if wins and not (use_element and HAVE_UIA and has_id):
            break
        if wins:
            try:
                _ensure_com()
                fuzzy = tries >= 2  # exact first; loosen after a couple of rounds
                for h in wins:
                    for target in (el, anchor):
                        if not (target.get("name") or target.get("auto_id")):
                            continue
                        c = _find_in(h, target, fuzzy)
                        if c:
                            pt = _point_in(c, target)
                            if pt:
                                return pt[0], pt[1], "element", h
            except Exception:
                pass
        tries += 1
        if time.time() >= end:
            break
        time.sleep(0.25)
    if wins and fallback:
        h = wins[0]
        if win.get("popup") and W.window_title(h):
            return None  # the popup never opened - don't click a random spot in the main window
        l, t, r, b = W.window_rect(h)
        return int(l + (r - l) * desc.get("rx", 0.5)), int(t + (b - t) * desc.get("ry", 0.5)), "position", h
    return None
