"""Action engine: runs a list of steps on a worker thread.

Each step is a dict {"type": ..., fields...}. String fields support
variables like {file}, {files}, {clipboard}, {contact_url}, {date}.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shlex
import subprocess
import threading
import time
from datetime import datetime

from . import win32 as W
from . import uia

log = logging.getLogger("pieguy")


class Abort(Exception):
    """Stop the flow quietly (user cancelled / nothing to do)."""


class StepError(Exception):
    pass


def admin_msg(hwnd) -> str:
    app = os.path.splitext(W.window_process(hwnd) or "That app")[0]
    return (f"{app} runs as administrator, so Windows blocks PieGuy from controlling it. "
            "Turn on Settings → Run PieGuy as administrator.")


def check_uipi(hwnd):
    if hwnd and W.uipi_blocked(hwnd):
        raise StepError(admin_msg(hwnd))


# ----------------------------------------------------------------- helpers
_VAR = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def interpolate(value, vars_: dict):
    if isinstance(value, str):
        def rep(m):
            k = m.group(1)
            if k not in vars_:
                if k == "clipboard":
                    try:
                        return W.get_clipboard_text()
                    except Exception:
                        return ""
                if k == "date":
                    return datetime.now().strftime("%Y-%m-%d")
                if k == "time":
                    return datetime.now().strftime("%H-%M-%S")
                return m.group(0)
            v = vars_[k]
            if isinstance(v, (list, tuple)):
                return "\n".join(map(str, v))
            return str(v)
        return _VAR.sub(rep, value)
    return value


def telegram_url(chat: str, version: str = "k") -> str:
    """'@name', 'name', 't.me/name', '+123..', numeric id or a full web.telegram.org URL."""
    chat = (chat or "").strip()
    if not chat:
        return f"https://web.telegram.org/{version}/"
    if "web.telegram.org" in chat:
        return chat if chat.startswith("http") else "https://" + chat
    m = re.match(r"^(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z0-9_+]+)", chat)
    if m:
        chat = m.group(1)
    chat = chat.lstrip("@")
    if chat.startswith("+") and chat[1:].isdigit():  # phone number
        return f"https://web.telegram.org/{version}/#?tgaddr=" + "tg%3A%2F%2Fresolve%3Fphone%3D" + chat[1:]
    if re.fullmatch(r"-?\d+", chat):
        return f"https://web.telegram.org/{version}/#{chat}"
    if version == "a":
        return f"https://web.telegram.org/a/#?tgaddr=tg%3A%2F%2Fresolve%3Fdomain%3D{chat}"
    return f"https://web.telegram.org/k/#@{chat}"


def _as_list(v) -> list[str]:
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v if str(x).strip()]
    return [l.strip().strip('"') for l in str(v or "").splitlines() if l.strip()]


def _resolve_exe(path: str, settings: dict) -> str:
    p = (path or "").strip().strip('"')
    low = p.lower()
    if low in ("firefox", "firefox.exe"):
        return settings.get("firefox_path") or W.find_app("firefox") or "firefox.exe"
    if low in ("chrome", "chrome.exe"):
        return W.find_app("chrome") or "chrome.exe"
    if low in ("edge", "msedge", "msedge.exe"):
        return W.find_app("msedge") or "msedge.exe"
    if p and not os.path.isabs(p) and not os.path.exists(p) and "\\" not in p:
        found = W.find_app(p)
        if found:
            return found
    return os.path.expandvars(p)


# ------------------------------------------------------------ v2rayN logic
def v2rayn_locate(settings: dict, exe_hint: str = "") -> str:
    for c in (exe_hint, settings.get("v2rayn_path", "")):
        if c and os.path.exists(c):
            return c
    running = W.process_path("v2rayN")
    if running:
        return running
    for base in (r"%ProgramFiles%\v2rayN", r"%LocalAppData%\v2rayN", r"C:\v2rayN", r"D:\v2rayN",
                 r"%UserProfile%\Desktop\v2rayN", r"%UserProfile%\Downloads\v2rayN"):
        p = os.path.join(os.path.expandvars(base), "v2rayN.exe")
        if os.path.exists(p):
            return p
    return ""


def v2rayn_config_path(exe: str) -> str:
    d = os.path.dirname(exe)
    for p in (os.path.join(d, "guiConfigs", "guiNConfig.json"), os.path.join(d, "guiNConfig.json")):
        if os.path.exists(p):
            return p
    return ""


def _jfind(obj, key_lower):
    """Yield (parent, key) for every key matching case-insensitively."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() == key_lower:
                yield obj, k
            yield from _jfind(v, key_lower)
    elif isinstance(obj, list):
        for v in obj:
            yield from _jfind(v, key_lower)


def v2rayn_proxy_port(exe: str, override: str = "") -> int:
    if str(override).strip().isdigit():
        return int(override)
    cfg_path = v2rayn_config_path(exe) if exe else ""
    if cfg_path:
        try:
            with open(cfg_path, encoding="utf-8-sig") as f:
                cfg = json.load(f)
            port = next((p[k] for p, k in _jfind(cfg, "localport") if isinstance(p[k], int)), 10808)
            new_style = any(True for _ in _jfind(cfg, "systemproxyitem"))
            return port if new_style else port + 1  # v2rayN < 6.x exposed http on port+1
        except Exception:
            pass
    return 10808


def v2rayn_patch_config(exe: str, tun: bool | None = None, sysproxy: int | None = None) -> bool:
    path = v2rayn_config_path(exe)
    if not path:
        return False
    with open(path, encoding="utf-8-sig") as f:
        cfg = json.load(f)
    if tun is not None:
        for parent, k in _jfind(cfg, "enabletun"):
            parent[k] = tun
    if sysproxy is not None:
        for parent, k in _jfind(cfg, "sysproxytype"):
            parent[k] = sysproxy
    with open(path + ".pieguy.bak", "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return True


# ------------------------------------------------------------------ runner
class Runner:
    """Executes one flow. `ui` supplies main-thread services (toast/dialogs)."""

    def __init__(self, cfg: dict, ui, on_history=None):
        self.cfg = cfg
        self.ui = ui
        self.on_history = on_history
        self.cancel = threading.Event()

    # public ---------------------------------------------------------------
    def run(self, steps: list[dict], vars_: dict, title: str = "Action", depth: int = 0):
        steps = [s for s in steps if s.get("enabled", True)]
        n = len(steps)
        for i, step in enumerate(steps):
            if self.cancel.is_set():
                raise Abort("Cancelled")
            label = STEP_LABELS.get(step.get("type"), step.get("type"))
            if depth == 0:
                self.ui.progress(title, i, n, label)
            try:
                self.exec_step(step, vars_, depth)
            except Abort:
                raise
            except Exception as e:
                if step.get("on_error") == "continue":
                    log.warning("step %s failed (continuing): %s", label, e)
                    continue
                if isinstance(e, StepError):
                    raise
                raise StepError(f"{label}: {e}") from e
            post = int(step.get("delay_after", 0) or 0)
            if post:
                self.sleep(post)

    def sleep(self, ms):
        end = time.time() + ms / 1000
        while time.time() < end:
            if self.cancel.is_set():
                raise Abort("Cancelled")
            time.sleep(min(0.05, max(0, end - time.time())))

    # dispatch --------------------------------------------------------------
    def exec_step(self, step, vars_, depth=0):
        s = {k: interpolate(v, vars_) for k, v in step.items()}
        fn = getattr(self, "s_" + s.get("type", ""), None)
        if not fn:
            raise StepError(f"Unknown step type {s.get('type')}")
        fn(s, vars_, depth)

    @property
    def settings(self):
        return self.cfg.get("settings", {})

    # --------------------------------------------------------- apps & web
    def s_launch(self, s, v, d):
        path = _resolve_exe(s.get("path", ""), self.settings)
        if not path:
            raise StepError("No program set")
        name = os.path.basename(path).lower()
        if s.get("if_not_running") and name.endswith(".exe") and W.process_running(name):
            h = W.find_window(process=name)
            if h:
                W.focus_window(h)
            return
        args = s.get("args", "")
        if s.get("admin"):
            ok = W.shell_open(path, args, s.get("cwd", ""), admin=True)
        else:  # never hand our admin rights to normal apps (Firefox etc.)
            ok = W.run_unelevated(path, args, s.get("cwd", ""))
        if not ok:
            subprocess.Popen([path] + (shlex.split(args, posix=False) if args else []), cwd=s.get("cwd") or None)
        if s.get("wait_window") and name.endswith(".exe"):
            h = W.wait_window(process=name, timeout=float(s.get("timeout", 20)), cancel=self.cancel)
            if h:
                W.focus_window(h)

    def s_open_path(self, s, v, d):
        for p in _as_list(s.get("path")):
            W.run_unelevated(os.path.expandvars(p))

    def s_open_url(self, s, v, d):
        url = s.get("url", "").strip()
        self._open_url(url, s.get("browser", "default"), s.get("reuse_title", ""))

    def _browser_exe(self, browser):
        if browser in ("", "default"):
            return ""
        if browser == "firefox":
            return _resolve_exe("firefox", self.settings)
        if browser == "chrome":
            return _resolve_exe("chrome", self.settings)
        if browser == "edge":
            return _resolve_exe("edge", self.settings)
        return _resolve_exe(browser, self.settings)

    def _open_url(self, url, browser="default", reuse_title="") -> int:
        exe = self._browser_exe(browser)
        proc = os.path.basename(exe).lower() if exe else ""
        if reuse_title:
            h = W.find_window(title=reuse_title, process=proc)
            if h and W.focus_window(h):
                self.sleep(120)
                W.release_modifiers()
                W.send_combo("ctrl+l")
                self.sleep(120)
                W.type_text(url, delay_ms=0)
                self.sleep(80)
                W.key_tap(0x2E)  # Delete: drop any address-bar autofill suggestion
                W.key_tap(0x0D)
                return h
        if W.self_elevated():
            return self._open_url_unelevated(url, exe, proc)
        if exe:
            args = ["-new-tab", url] if proc == "firefox.exe" else [url]
            subprocess.Popen([exe] + args)
        else:
            os.startfile(url)
        return 0

    def _type_url(self, h, url, new_tab):
        W.focus_window(h)
        self.sleep(150)
        W.release_modifiers()
        W.send_combo("ctrl+t" if new_tab else "ctrl+l")
        self.sleep(250)
        W.type_text(url, delay_ms=0)
        self.sleep(80)
        W.key_tap(0x2E)
        W.key_tap(0x0D)

    def _open_url_unelevated(self, url, exe, proc) -> int:
        """PieGuy is admin: don't start the browser elevated. Drive the user's own browser."""
        if not exe:
            W.run_unelevated(url)  # default browser via the (non-admin) shell
            return 0
        h = W.find_window(process=proc)
        if h:
            self._type_url(h, url, new_tab=True)
            return h
        W.run_unelevated(exe)
        h = W.wait_window(process=proc, timeout=25, cancel=self.cancel)
        if not h:
            raise StepError(f"{os.path.basename(exe)} didn't open")
        self.sleep(1200)
        self._type_url(h, url, new_tab=False)
        self.sleep(2500)  # fresh browser: let the page load (callers assume a warm tab)
        return h

    def s_close_app(self, s, v, d):
        W.kill_process(s.get("process", ""), tree=True, force=bool(s.get("force", False)))

    def s_run_command(self, s, v, d):
        cmd = s.get("command", "")
        flags = W.CREATE_NO_WINDOW if s.get("hidden", True) else 0
        if s.get("shell") == "powershell":
            argv = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd]
        else:
            argv = ["cmd", "/c", cmd]
        if s.get("wait"):
            r = subprocess.run(argv, capture_output=True, text=True, creationflags=flags,
                               timeout=float(s.get("timeout", 60)))
            v[s.get("var") or "output"] = (r.stdout or "").strip()
            if r.returncode != 0 and s.get("on_error") != "continue":
                raise StepError((r.stderr or r.stdout or f"exit code {r.returncode}").strip()[:200])
        else:
            subprocess.Popen(argv, creationflags=flags)

    # ------------------------------------------------------------ windows
    def s_focus_window(self, s, v, d):
        h = W.wait_window(s.get("title", ""), s.get("process", ""), float(s.get("timeout", 10)), self.cancel)
        if not h:
            raise StepError(f"Window not found ({s.get('title') or s.get('process')})")
        W.focus_window(h)
        v["window"] = h

    def s_wait_window(self, s, v, d):
        h = W.wait_window(s.get("title", ""), s.get("process", ""), float(s.get("timeout", 15)), self.cancel)
        if not h:
            raise StepError("Timed out waiting for window")
        v["window"] = h

    def s_window_op(self, s, v, d):
        if s.get("target", "active") == "active":
            h = v.get("target_hwnd") or W.fg_window()
        else:
            h = W.find_window(s.get("title", ""), s.get("process", ""))
        W.window_op(h, s.get("op", "minimize"))

    # --------------------------------------------------- keyboard & mouse
    def s_hotkey(self, s, v, d):
        check_uipi(W.fg_window())
        W.release_modifiers()
        W.send_combo(s.get("keys", ""))

    def s_type_text(self, s, v, d):
        text = s.get("text", "")
        check_uipi(W.fg_window())
        W.release_modifiers()
        if s.get("paste"):
            W.set_clipboard_text(text)
            self.sleep(60)
            W.send_combo("ctrl+v")
        else:
            W.type_text(text, int(s.get("delay_ms", 4)))

    def s_click_element(self, s, v, d):
        target = s.get("target") or {}
        res = uia.locate(target, float(s.get("timeout", 8)), self.cancel,
                         use_element=s.get("method", "element") == "element")
        if not res:
            raise StepError(f"Couldn't find {uia.element_label(target)}")
        x, y, how, hwnd = res
        check_uipi(hwnd)
        fg = W.fg_window()
        # popups (dropdown lists, menus) must NOT be focused - that would close them
        if fg != hwnd and W.window_title(hwnd) and not W.is_popup(hwnd) and W.window_pid(fg) != W.window_pid(hwnd):
            W.focus_window(hwnd)
            self.sleep(80)
        W.mouse_click(x, y, s.get("button", "left"), bool(s.get("double")))
        log.info("clicked %s via %s", uia.element_label(target), how)

    def s_hover(self, s, v, d):
        """Rest the mouse on an element (opens hover submenus, tooltips...)."""
        target = s.get("target") or {}
        res = uia.locate(target, float(s.get("timeout", 5)), self.cancel)
        if not res:
            raise StepError(f"Couldn't find {uia.element_label(target)}")
        x, y, how, hwnd = res
        check_uipi(hwnd)
        W.user32.SetCursorPos(int(x), int(y))
        W.mouse_nudge()
        self.sleep(int(s.get("ms", 450)))

    def s_click_at(self, s, v, d):
        x, y = int(s.get("x", 0)), int(s.get("y", 0))
        if s.get("relative") == "window":
            h = v.get("target_hwnd") or W.fg_window()
            l, t, _, _ = W.window_rect(h)
            x, y = x + l, y + t
        W.mouse_click(x, y, s.get("button", "left"), bool(s.get("double")))

    def s_drag(self, s, v, d):
        a, b = s.get("from") or {}, s.get("to") or {}
        ra = uia.locate(a, 5, self.cancel, use_element=False)
        rb = uia.locate(b, 5, self.cancel, use_element=False)
        if not ra or not rb:
            raise StepError("Drag target window not found")
        W.mouse_drag(ra[0], ra[1], rb[0], rb[1])

    def s_scroll(self, s, v, d):
        target = s.get("target") or {}
        if (target.get("window") or {}).get("process"):
            res = uia.locate(target, 5, self.cancel, use_element=False)
            if res:
                W.user32.SetCursorPos(res[0], res[1])
                self.sleep(40)
        W.mouse_scroll(int(s.get("amount", -3)))

    # -------------------------------------------------- clipboard & files
    def s_get_files(self, s, v, d):
        src = s.get("source", "auto")
        files: list[str] = []
        if src in ("auto", "explorer"):
            files = W.explorer_selection(v.get("target_hwnd"))
            if not files:
                files = self._explorer_copy(v.get("target_hwnd"))
        if not files and src in ("auto", "clipboard"):
            files = W.get_clipboard_files()
        if not files and src in ("auto", "dialog"):
            files = self.ui.ask_files(s.get("title") or "Choose file(s)")
        if not files:
            raise Abort("No file selected")
        var = s.get("var") or "files"
        v[var] = files
        v["file"] = files[0]
        v["filename"] = os.path.basename(files[0])

    def _explorer_copy(self, hwnd) -> list[str]:
        """Fallback: Ctrl+C in the Explorer window / desktop the pie was opened over.
        Works where Shell COM can't (desktop, or PieGuy running as admin)."""
        if not hwnd or W.window_class(hwnd) not in ("CabinetWClass", "ExploreWClass", "Progman", "WorkerW"):
            return []
        W.focus_window(hwnd)
        self.sleep(120)
        W.release_modifiers()
        W.set_clipboard_text("")
        W.send_combo("ctrl+c")
        for _ in range(12):
            self.sleep(60)
            files = W.get_clipboard_files()
            if files:
                return files
        return []

    def s_set_clipboard_text(self, s, v, d):
        W.set_clipboard_text(s.get("text", ""))

    def s_set_clipboard_files(self, s, v, d):
        W.set_clipboard_files(_as_list(s.get("files") or v.get("files")))

    def s_copy_selection(self, s, v, d):
        h = v.get("target_hwnd")
        if h and W.fg_window() != h:
            W.focus_window(h)
        W.release_modifiers()
        W.set_clipboard_text("")
        W.send_combo("ctrl+c")
        self.sleep(int(s.get("wait_ms", 250)))
        v[s.get("var") or "selection"] = W.get_clipboard_text()

    def s_ask_text(self, s, v, d):
        val = self.ui.ask_text(s.get("prompt") or "Enter text", s.get("default", ""))
        if val is None:
            raise Abort("Cancelled")
        v[s.get("var") or "text"] = val

    # ------------------------------------------------------------ network
    def s_system_proxy(self, s, v, d):
        mode = s.get("mode", "on")
        if mode == "toggle":
            mode = "off" if W.system_proxy_state()[0] else "on"
        W.set_system_proxy(mode == "on", s.get("server") or "127.0.0.1:10808", s.get("bypass") or W.DEFAULT_BYPASS)

    def s_v2rayn(self, s, v, d):
        action = s.get("action", "proxy_on")
        exe = v2rayn_locate(self.settings, s.get("exe", ""))
        if action in ("proxy_off",):
            W.set_system_proxy(False)
            return
        if action == "stop":
            W.set_system_proxy(False)
            W.kill_process("v2rayN.exe", tree=True, force=True)
            return
        if not exe:
            raise StepError("v2rayN.exe not found - set its path in Settings")
        port = v2rayn_proxy_port(exe, s.get("port", ""))
        running = W.process_running("v2rayN.exe")
        if action in ("tun_on", "tun_off"):
            want = action == "tun_on"
            if running:
                W.kill_process("v2rayN.exe", tree=True, force=True)
                for _ in range(20):
                    self.sleep(150)
                    if not W.process_running("v2rayN.exe"):
                        break
                else:
                    raise StepError("Couldn't close v2rayN (it runs as administrator). "
                                    "Turn on Settings → Run PieGuy as administrator.")
                self.sleep(400)
            if not v2rayn_patch_config(exe, tun=want, sysproxy=None if want else 1):
                raise StepError("guiNConfig.json not found next to v2rayN")
            if want:
                W.set_system_proxy(False)  # TUN routes everything; avoid double-proxy
            W.shell_open(exe, "", os.path.dirname(exe), admin=bool(s.get("admin", want)))
            if not want:
                self._wait_port(port, s)
                W.set_system_proxy(True, f"127.0.0.1:{port}")
            else:
                self.sleep(1500)
            return
        # start / proxy_on
        if not running:
            W.shell_open(exe, "", os.path.dirname(exe))
        if action == "proxy_on":
            self._wait_port(port, s)
            W.set_system_proxy(True, f"127.0.0.1:{port}")

    def _wait_port(self, port, s):
        if not s.get("wait_port", True):
            return
        end = time.time() + float(s.get("timeout", 15))
        while time.time() < end:
            if W.port_open("127.0.0.1", port):
                return
            self.sleep(250)
        raise StepError(f"v2rayN core didn't open port {port}")

    # ----------------------------------------------------------- telegram
    def s_telegram_web(self, s, v, d):
        url = telegram_url(s.get("chat", ""), self.settings.get("telegram_version", "k"))
        content = s.get("content", "files")
        if content == "files":
            files = _as_list(v.get("files") or s.get("files") or "")
            if not files:
                raise Abort("No files to send")
            W.set_clipboard_files(files)
        elif content == "text":
            W.set_clipboard_text(s.get("text", ""))
        browser = s.get("browser", "firefox")
        exe = self._browser_exe(browser)
        proc = os.path.basename(exe).lower() if exe else ""
        reused = self._open_url(url, browser, "Telegram" if s.get("reuse_tab", True) else "")
        h = reused or W.wait_window("Telegram", proc, float(s.get("timeout", 25)), self.cancel)
        if not h:
            raise StepError("Telegram Web window didn't appear")
        W.focus_window(h)
        self.sleep(int(s.get("load_wait_ms", 3500)) // (3 if reused else 1))
        if content in ("files", "text"):
            W.release_modifiers()
            self._focus_composer(proc)
            W.send_combo("ctrl+v")
            self.sleep(1300 if content == "files" else 250)
            if s.get("caption") and content == "files":
                W.type_text(s["caption"], delay_ms=2)
                self.sleep(150)
            if s.get("send", True):
                W.key_tap(0x0D)
        if self.on_history and v.get("contact_id"):
            self.on_history(v["contact_id"])

    def _focus_composer(self, proc):
        """Put the caret in Telegram's message box so the paste lands there."""
        target = {"window": {"process": proc}, "element": {"name": "Message"}}
        res = uia.locate(target, 1.5, self.cancel, use_element=True, fallback=False) if uia.HAVE_UIA else None
        if res:
            W.mouse_click(res[0], res[1])
            self.sleep(120)
            return
        # Telegram Web focuses the composer when you start typing anywhere
        W.type_text(" ", delay_ms=0)
        self.sleep(80)
        W.key_tap(0x08)
        self.sleep(80)

    # --------------------------------------------------------------- flow
    def s_wait(self, s, v, d):
        self.sleep(int(float(s.get("ms", 500))))

    def s_notify(self, s, v, d):
        self.ui.toast(s.get("text", "Done"), "ok")

    def s_run_flow(self, s, v, d):
        if d > 8:
            raise StepError("Flows nested too deep")
        flow = next((f for f in self.cfg.get("flows", []) if f["id"] == s.get("flow")), None)
        if not flow:
            raise StepError("Flow not found")
        self.run(flow.get("steps", []), v, flow.get("name", "Flow"), d + 1)


STEP_LABELS = {
    "launch": "Launch app", "open_path": "Open file/folder", "open_url": "Open URL",
    "close_app": "Close app", "run_command": "Run command", "focus_window": "Focus window",
    "wait_window": "Wait for window", "window_op": "Window action", "hotkey": "Press keys",
    "type_text": "Type text", "click_element": "Click element", "click_at": "Click position",
    "drag": "Drag", "hover": "Hover", "scroll": "Scroll", "get_files": "Get files", "set_clipboard_text": "Copy text",
    "set_clipboard_files": "Copy files", "copy_selection": "Copy selection", "ask_text": "Ask for text",
    "system_proxy": "System proxy", "v2rayn": "v2rayN", "telegram_web": "Telegram Web",
    "wait": "Wait", "notify": "Notify", "run_flow": "Run flow",
}
