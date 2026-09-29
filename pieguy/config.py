"""Config storage (portable: lives in <PieGuy>/data/config.json)."""
from __future__ import annotations

import copy
import json
import os
import time
import uuid

import sys

FROZEN = bool(getattr(sys, "frozen", False))
# portable: settings live next to PieGuy.exe (or next to PieGuy.pyw when run from source)
ROOT = os.path.dirname(sys.executable) if FROZEN else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = getattr(sys, "_MEIPASS", ROOT)  # bundled read-only files (ui/)
DATA = os.path.join(ROOT, "data")
FIRST_RUN = False
CONFIG_PATH = os.path.join(DATA, "config.json")
LOG_PATH = os.path.join(DATA, "pieguy.log")


def uid(prefix="i"):
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _item(label, icon, color, steps=None, **kw):
    d = {"id": uid(), "label": label, "icon": icon, "color": color, "kind": "action", "steps": steps or []}
    d.update(kw)
    return d


def _step(type_, **fields):
    return {"id": uid("s"), "type": type_, "enabled": True, **fields}


def default_config() -> dict:
    tg_flow = {
        "id": "flow_tg_send", "name": "Send file via Telegram Web", "icon": "📤",
        "steps": [
            _step("get_files", source="auto", var="files"),
            _step("v2rayn", action="proxy_on", exe="", port="", wait_port=True),
            _step("telegram_web", chat="{contact_url}", content="files", text="", caption="",
                  browser="firefox", reuse_tab=True, load_wait_ms=3500, send=True),
            _step("notify", text="Sent to {contact_name} ✓"),
        ],
    }
    media = [
        _item("Play / Pause", "⏯️", "#22d3ee", [_step("hotkey", keys="media_play_pause")]),
        _item("Next", "⏭️", "#22d3ee", [_step("hotkey", keys="media_next")]),
        _item("Volume +", "🔊", "#22d3ee", [_step("hotkey", keys="volume_up volume_up volume_up")]),
        _item("Mute", "🔇", "#22d3ee", [_step("hotkey", keys="volume_mute")]),
        _item("Volume −", "🔉", "#22d3ee", [_step("hotkey", keys="volume_down volume_down volume_down")]),
        _item("Previous", "⏮️", "#22d3ee", [_step("hotkey", keys="media_prev")]),
    ]
    proxy = [
        _item("Proxy ON", "🟢", "#34d399", [_step("v2rayn", action="proxy_on", exe="", port="", wait_port=True),
                                           _step("notify", text="System proxy ON")]),
        _item("TUN ON", "🛡️", "#a78bfa", [_step("v2rayn", action="tun_on", exe="", port="", wait_port=True),
                                          _step("notify", text="TUN mode ON")]),
        _item("All OFF", "⭕", "#f87171", [_step("v2rayn", action="proxy_off", exe="", port="", wait_port=False),
                                          _step("notify", text="Proxy OFF")]),
        _item("TUN OFF", "🔓", "#fb923c", [_step("v2rayn", action="tun_off", exe="", port="", wait_port=True),
                                          _step("notify", text="TUN mode OFF")]),
    ]
    items = [
        _item("Send to…", "📤", "#38bdf8", kind="contacts", flow="flow_tg_send", max=8),
        _item("Proxy", "🌐", "#34d399", kind="submenu", children=proxy),
        _item("Firefox", "🦊", "#fb923c", [_step("launch", path="firefox", args="", if_not_running=True,
                                                  wait_window=False, admin=False)]),
        _item("Media", "🎵", "#22d3ee", kind="submenu", children=media),
        _item("Copy path", "📋", "#facc15", [_step("get_files", source="auto", var="files"),
                                            _step("set_clipboard_text", text="{files}"),
                                            _step("notify", text="Path copied")]),
        _item("Screenshot", "📸", "#f472b6", [_step("hotkey", keys="win+shift+s")]),
        _item("Explorer", "🗂️", "#fbbf24", [_step("hotkey", keys="win+e")]),
        _item("Lock", "🔒", "#94a3b8", [_step("run_command", command="rundll32.exe user32.dll,LockWorkStation",
                                             shell="cmd", hidden=True, wait=False)]),
    ]
    return {
        "version": 1,
        "settings": {
            "startup": False,
            "radius": 170, "inner": 58, "gap": 3, "anim_ms": 170, "dim": 0.28,
            "accent": "#8b5cf6", "accent2": "#22d3ee", "font_size": 12, "show_labels": True,
            "submenu_dwell_ms": 420, "tap_ms": 220, "deadzone": 26,
            "excluded_apps": [], "telegram_version": "k", "firefox_path": "", "v2rayn_path": "",
            "run_as_admin": False,
        },
        "pies": [{
            "id": "pie_main", "name": "Main", "enabled": True, "apps": [], "tap": "sticky",
            "trigger": {"kind": "key", "vk": 0x14, "mods": [], "name": "CapsLock"},
            "items": items,
        }],
        "contacts": [
            {"id": uid("c"), "name": "Saved / example", "url": "@username", "emoji": "💬", "color": "#38bdf8"},
        ],
        "flows": [tg_flow],
        "history": {},
    }


def load() -> dict:
    os.makedirs(DATA, exist_ok=True)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg = json.load(f)
            base = default_config()
            for k, v in base["settings"].items():
                cfg.setdefault("settings", {}).setdefault(k, v)
            for k in ("pies", "contacts", "flows", "history"):
                cfg.setdefault(k, copy.deepcopy(base[k]) if k != "history" else {})
            return cfg
        except Exception:
            os.replace(CONFIG_PATH, CONFIG_PATH + f".broken-{int(time.time())}")
    global FIRST_RUN
    FIRST_RUN = True
    cfg = default_config()
    save(cfg)
    return cfg


def save(cfg: dict):
    os.makedirs(DATA, exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)


def find_item(cfg: dict, item_id: str):
    def walk(items):
        for it in items:
            if it.get("id") == item_id:
                return it
            r = walk(it.get("children") or [])
            if r:
                return r
        return None
    for pie in cfg.get("pies", []):
        r = walk(pie.get("items", []))
        if r:
            return r
    return None
