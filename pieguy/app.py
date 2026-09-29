"""PieGuy application: tray, global hook -> pie, action runner, settings bridge."""
from __future__ import annotations

import base64
import copy
import ctypes
import ctypes.wintypes
import json
import logging
import os
import sys
import threading
import time

from PySide6.QtCore import (QObject, Signal, Slot, Qt, QTimer, QUrl, QBuffer, QByteArray, QIODevice, QFile,
                            QAbstractNativeEventFilter)
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QFont, QAction, QDesktopServices, QRadialGradient
from PySide6.QtWidgets import (QApplication, QSystemTrayIcon, QMenu, QFileDialog, QInputDialog, QMainWindow, QWidget)

from . import config as C
from . import engine as E
from . import win32 as W
from .hooks import InputHook
from .overlay import PieOverlay, Toast, RecPill, ElemHighlight, ClickRipple, icon_pixmap
from .probe import Probe
from .recorder import Recorder
from . import uia

log = logging.getLogger("pieguy")
VERSION = "1.2.0"
UI_DIR = os.path.join(C.RES, "ui")


def app_icon(size=64) -> QIcon:
    png = os.path.join(C.RES, "assets", "pieguy.png")
    if os.path.exists(png):
        return QIcon(png)
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    g = QRadialGradient(size * 0.35, size * 0.3, size * 0.8)
    g.setColorAt(0, QColor("#22d3ee"))
    g.setColorAt(1, QColor("#8b5cf6"))
    p.setBrush(g)
    p.setPen(Qt.NoPen)
    p.drawEllipse(2, 2, size - 4, size - 4)
    p.setBrush(QColor(18, 17, 30))
    p.drawPie(2, 2, size - 4, size - 4, 30 * 16, 60 * 16)  # a slice "missing"
    p.drawEllipse(int(size * 0.36), int(size * 0.36), int(size * 0.28), int(size * 0.28))
    p.end()
    return QIcon(pm)


# ------------------------------------------------------------ UI services
class UiServices(QObject):
    """Main-thread services the worker-thread Runner can call (blocking where needed)."""
    _progress = Signal(str, str, str, float)
    _toast = Signal(str, str, str)
    _ask = Signal(object)

    def __init__(self, app: "PieGuyApp"):
        super().__init__()
        self.app = app
        self.icon = ""
        self._progress.connect(self._on_progress, Qt.QueuedConnection)
        self._toast.connect(self._on_toast, Qt.QueuedConnection)
        self._ask.connect(self._do_ask, Qt.QueuedConnection)

    @Slot(str, str, str, float)
    def _on_progress(self, t, s, i, f):
        self.app.toast.show_msg(t, s, i, "run", f)

    @Slot(str, str, str)
    def _on_toast(self, t, k, i):
        self.app.toast.show_msg(t, "", i, k, 1.0, 2200 if k == "ok" else 6000)

    def progress(self, title, i, n, label):
        self._progress.emit(title, f"{label}  ·  step {i + 1}/{n}", self.icon, (i + 0.5) / max(1, n))

    def toast(self, text, kind="ok"):
        self._toast.emit(text, kind, "")

    def _blocking(self, kind, **kw):
        req = {"kind": kind, "ev": threading.Event(), "result": None, **kw}
        self._ask.emit(req)
        req["ev"].wait()
        return req["result"]

    def ask_files(self, title):
        return self._blocking("files", title=title) or []

    def ask_text(self, prompt, default=""):
        return self._blocking("text", prompt=prompt, default=default)

    @Slot(object)
    def _do_ask(self, req):
        try:
            if req["kind"] == "files":
                QTimer.singleShot(250, lambda: self._raise_own(req["title"]))
                files, _ = QFileDialog.getOpenFileNames(None, req["title"])
                req["result"] = [os.path.normpath(f) for f in files]
            else:
                dlg = QInputDialog()
                dlg.setWindowTitle("PieGuy")
                dlg.setLabelText(req["prompt"])
                dlg.setTextValue(req.get("default", ""))
                dlg.setWindowFlag(Qt.WindowStaysOnTopHint)
                dlg.resize(420, 120)
                QTimer.singleShot(120, lambda: W.focus_window(int(dlg.winId())))
                req["result"] = dlg.textValue() if dlg.exec() else None
        finally:
            req["ev"].set()

    @staticmethod
    def _raise_own(title):
        pid = os.getpid()
        for w in W.list_windows():
            if W.window_pid(w["hwnd"]) == pid and title in w["title"]:
                W.focus_window(w["hwnd"])
                return


# ---------------------------------------------------------------- bridge
class Bridge(QObject):
    """Exposed to the settings page as `window.bridge` via QWebChannel."""
    triggerCaptured = Signal(str)
    recordingDone = Signal(str)
    elementPicked = Signal(str)
    notice = Signal(str)

    def __init__(self, app: "PieGuyApp"):
        super().__init__()
        self.app = app

    @Slot(result=str)
    def getState(self):
        return json.dumps({
            "config": self.app.cfg,
            "startup": W.get_startup() if W.IS_WIN else False,
            "meta": {"version": VERSION, "uia": uia.HAVE_UIA, "config_path": C.CONFIG_PATH,
                     "windows": W.IS_WIN, "elevated": W.IS_WIN and W.self_elevated()},
        }, ensure_ascii=False)

    @Slot(str, result=bool)
    def saveConfig(self, js):
        try:
            self.app.set_config(json.loads(js))
            return True
        except Exception as e:
            log.exception("saveConfig")
            self.notice.emit(f"Save failed: {e}")
            return False

    @Slot(str)
    def runItem(self, js):
        item = json.loads(js)
        self.app.settings_win.showMinimized()
        QTimer.singleShot(450, lambda: self.app.activate(item, from_settings=True))

    @Slot(str)
    def runSteps(self, js):
        self.runItem(json.dumps({"label": "Test", "icon": "🧪", "kind": "action", "steps": json.loads(js)}))

    @Slot()
    def captureTrigger(self):
        self.app.hook.capture = True

    @Slot()
    def cancelCapture(self):
        self.app.hook.capture = False

    @Slot()
    def startRecording(self):
        self.app.start_recording()

    @Slot()
    def pickElement(self):
        self.app.start_pick()

    @Slot(str, str, result=str)
    def browseFile(self, title, flt):
        path, _ = QFileDialog.getOpenFileName(self.app.settings_win, title or "Choose file", "", flt or "All files (*.*)")
        return os.path.normpath(path) if path else ""

    @Slot(result=str)
    def browseFolder(self):
        path = QFileDialog.getExistingDirectory(self.app.settings_win, "Choose folder")
        return os.path.normpath(path) if path else ""

    @Slot(result=str)
    def listWindows(self):
        wins = W.list_windows() if W.IS_WIN else []
        pid = os.getpid()
        return json.dumps([w for w in wins if W.window_pid(w["hwnd"]) != pid], ensure_ascii=False)

    @Slot(bool, result=bool)
    def setStartup(self, on):
        st = self.app.cfg["settings"]
        admin = bool(st.get("run_as_admin"))
        try:
            res = W.set_startup(on, admin=admin)
            if res == "needs-admin":
                self.notice.emit("Admin startup is set up from the admin instance — restart PieGuy as admin first")
            elif res != "ok":
                self.notice.emit(f"Startup change failed: {res}")
            st["startup"] = on
            C.save(self.app.cfg)
            return W.get_startup()
        except Exception as e:
            self.notice.emit(f"Startup change failed: {e}")
            return not on

    @Slot(bool, result=str)
    def setAdmin(self, on):
        """Run PieGuy elevated so it can see & control admin apps (v2rayN in TUN mode...)."""
        st = self.app.cfg["settings"]
        st["run_as_admin"] = on
        C.save(self.app.cfg)
        if on and not W.self_elevated():
            if W.relaunch_admin("--replace"):
                return "relaunching"
            st["run_as_admin"] = False
            C.save(self.app.cfg)
            return "declined"
        if not on and W.self_elevated():
            if st.get("startup"):
                W.set_startup(True, admin=False)
            return "restart"
        return "ok"

    @Slot()
    def openConfigFolder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(C.DATA))

    @Slot(str)
    def openUrl(self, url):
        QDesktopServices.openUrl(QUrl(url))

    @Slot(result=str)
    def exportConfig(self):
        path, _ = QFileDialog.getSaveFileName(self.app.settings_win, "Export PieGuy config", "pieguy-config.json", "JSON (*.json)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.app.cfg, f, ensure_ascii=False, indent=2)
        return path or ""

    @Slot(result=str)
    def importConfig(self):
        path, _ = QFileDialog.getOpenFileName(self.app.settings_win, "Import PieGuy config", "", "JSON (*.json)")
        if not path:
            return ""
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
        self.app.set_config(cfg)
        return json.dumps(self.app.cfg, ensure_ascii=False)

    @Slot(result=str)
    def resetConfig(self):
        self.app.set_config(C.default_config())
        return json.dumps(self.app.cfg, ensure_ascii=False)

    @Slot(result=str)
    def detectV2rayN(self):
        return E.v2rayn_locate(self.app.cfg.get("settings", {})) if W.IS_WIN else ""

    @Slot(result=str)
    def detectFirefox(self):
        return W.find_app("firefox") if W.IS_WIN else ""

    @Slot(str, result=str)
    def iconFor(self, path):
        pm = icon_pixmap(path, 64)
        if pm is None or pm.isNull():
            return ""
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.WriteOnly)
        pm.save(buf, "PNG")
        return "data:image/png;base64," + base64.b64encode(bytes(ba)).decode()

    @Slot(str)
    def previewPie(self, pie_id):
        self.app.preview_pie(pie_id)

    @Slot(result=str)
    def proxyState(self):
        if not W.IS_WIN:
            return json.dumps({"on": False, "server": ""})
        on, srv = W.system_proxy_state()
        return json.dumps({"on": on, "server": srv})

    @Slot(str, result=str)
    def telegramUrl(self, chat):
        return E.telegram_url(chat, self.app.cfg["settings"].get("telegram_version", "k"))


# ------------------------------------------------------------ settings UI
class SettingsWindow(QMainWindow):
    def __init__(self, app: "PieGuyApp"):
        super().__init__()
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from PySide6.QtWebEngineCore import QWebEngineScript
        from PySide6.QtWebChannel import QWebChannel
        self.app = app
        self.setWindowTitle("PieGuy")
        self.setWindowIcon(app_icon())
        self.resize(1320, 860)
        self.setMinimumSize(1040, 680)
        self.view = QWebEngineView(self)
        self.view.page().setBackgroundColor(QColor("#0c0b14"))
        self.channel = QWebChannel(self.view.page())
        self.channel.registerObject("bridge", app.bridge)
        self.view.page().setWebChannel(self.channel)
        f = QFile(":/qtwebchannel/qwebchannel.js")
        if f.open(QIODevice.ReadOnly):
            src = bytes(f.readAll()).decode()
            f.close()
            script = QWebEngineScript()
            script.setSourceCode(src)
            script.setName("qwebchannel")
            script.setWorldId(QWebEngineScript.MainWorld)
            script.setInjectionPoint(QWebEngineScript.DocumentCreation)
            script.setRunsOnSubFrames(False)
            self.view.page().scripts().insert(script)
        self.view.load(QUrl.fromLocalFile(os.path.join(UI_DIR, "settings.html")))
        self.setCentralWidget(self.view)
        W.dark_titlebar(int(self.winId()))

    def reveal(self):
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()
        W.focus_window(int(self.winId()))

    def closeEvent(self, e):
        e.ignore()
        self.hide()
        self.app.toast.show_msg("PieGuy is still running", "Right-click the tray icon to quit", "🥧", "ok", 1, 2600)


class _IpcFilter(QAbstractNativeEventFilter):
    def __init__(self, msg_id, cb):
        super().__init__()
        self.msg_id, self.cb = msg_id, cb

    def nativeEventFilter(self, event_type, message):
        try:
            et = event_type.data() if hasattr(event_type, "data") else event_type
            if isinstance(et, str):
                et = et.encode()
            if et == b"windows_generic_MSG":
                m = ctypes.wintypes.MSG.from_address(int(message))
                if m.message == self.msg_id:
                    QTimer.singleShot(0, lambda c=int(m.wParam): self.cb(c))
                    return True, 0
        except Exception:
            pass
        return False, 0


# ------------------------------------------------------------------- app
class PieGuyApp(QObject):
    hook_event = Signal(str, object)
    save_later = Signal()
    picked_ready = Signal(str)
    probe_event = Signal(object)

    def __init__(self, qapp: QApplication, background: bool):
        super().__init__()
        self.qapp = qapp
        self.cfg = C.load()
        self.overlay = PieOverlay()
        self.overlay.set_resolver(self.resolve_children)
        self.overlay.activated.connect(self.activate)
        self.overlay.closed.connect(lambda: setattr(self.hook, "pie_open", False))
        self.overlay.passthrough.connect(self._passthrough)
        self.toast = Toast()
        self.toast.cancel_clicked.connect(self.cancel_run)
        self.recpill = RecPill()
        self.recpill.stop_clicked.connect(self.stop_recording)
        self.ui = UiServices(self)
        self.bridge = Bridge(self)
        self._settings_win = None
        self.runner = None
        self.recorder = None
        self.rec_timer = QTimer(self, interval=150, timeout=self._poll_rec)
        self.pick_hint = RecPill()
        self.pick_hint.stop_clicked.connect(lambda: self._on_hook("pick", None))
        self.highlight = ElemHighlight()
        self.ripples = [ClickRipple() for _ in range(3)]
        self._ripple_i = 0
        self._probe_mode = ""
        self.probe = Probe(lambda d: self.probe_event.emit(d))
        self.target_hwnd = 0
        self.current_pie = None
        self.last_trigger = None

        self.hook_event.connect(self._on_hook, Qt.QueuedConnection)
        self.save_later.connect(lambda: C.save(self.cfg), Qt.QueuedConnection)
        self.picked_ready.connect(self._picked, Qt.QueuedConnection)
        self.probe_event.connect(self._on_probe, Qt.QueuedConnection)
        self.hook = InputHook(lambda k, d: self.hook_event.emit(k, d))
        self.apply_config()
        if W.IS_WIN:
            self.hook.start()
            self.probe.start()
            QTimer.singleShot(1500, self._sync_startup)
        self._tray()
        self._single_instance()
        self._apply_accent()
        if not background:
            QTimer.singleShot(50, self.show_settings)
        else:
            trig = self._trigger_name()
            self.toast.show_msg("PieGuy is ready", f"Hold {trig} anywhere" + ("  ·  admin mode" if W.IS_WIN and W.self_elevated() else ""),
                                "🥧", "ok", 1, 2500)
        if W.IS_WIN and self.cfg["settings"].get("run_as_admin") and not W.self_elevated():
            QTimer.singleShot(2800, lambda: self.toast.show_msg(
                "Running without admin", "Admin apps (e.g. v2rayN in TUN) can't be seen or controlled", "🛡️", "err", 0, 5000))

    def _sync_startup(self):
        """Keep the startup entry valid: right mode (task vs. Run key) and the *current*
        exe path - so the portable folder can be moved anywhere and still autostart."""
        st = self.cfg["settings"]
        want = ("--enable-startup" in sys.argv) or (C.FIRST_RUN and C.FROZEN)
        if want and not st.get("startup"):
            st["startup"] = True
            C.save(self.cfg)
        if not st.get("startup"):
            return
        if st.get("run_as_admin") and not W.self_elevated():
            return  # the admin instance owns the (task) entry; don't touch it without rights
        try:
            admin = bool(st.get("run_as_admin"))
            if admin or W.registered_startup() != W.startup_command():
                res = W.set_startup(True, admin=admin)
                log.info("startup entry refreshed (%s): %s", "task" if admin else "run key", res)
        except Exception:
            log.exception("sync startup")

    # ----------------------------------------------------------- config
    @property
    def settings_win(self):
        if self._settings_win is None:
            self._settings_win = SettingsWindow(self)
        return self._settings_win

    def set_config(self, cfg):
        cfg.setdefault("settings", {})
        base = C.default_config()["settings"]
        for k, v in base.items():
            cfg["settings"].setdefault(k, v)
        cfg["history"] = self.cfg.get("history", {}) | cfg.get("history", {})
        self.cfg = cfg
        C.save(cfg)
        self.apply_config()
        self._apply_accent()

    def apply_config(self):
        trigs = []
        for pie in self.cfg.get("pies", []):
            if not pie.get("enabled", True):
                continue
            t = pie.get("trigger") or {}
            apps = {a.lower().strip() if a.lower().strip().endswith(".exe") else a.lower().strip() + ".exe"
                    for a in pie.get("apps", []) if a.strip()}
            trigs.append({"pie": pie["id"], "kind": t.get("kind", "key"), "vk": int(t.get("vk", 0) or 0),
                          "button": t.get("button", ""), "mods": set(t.get("mods", [])), "apps": apps})
        excl = {a.lower().strip() if a.lower().strip().endswith(".exe") else a.lower().strip() + ".exe"
                for a in self.cfg["settings"].get("excluded_apps", []) if a.strip()}
        self.hook.triggers = trigs
        self.hook.excluded = excl

    def _apply_accent(self):
        from PySide6.QtGui import QColor as _C
        acc = _C(self.cfg["settings"].get("accent", "#8b5cf6"))
        self.toast.accent = acc

    def _trigger_name(self):
        pies = self.cfg.get("pies", [])
        if not pies:
            return "your trigger"
        t = pies[0].get("trigger", {})
        return "+".join([m.capitalize() for m in t.get("mods", [])] + [t.get("name") or "key"])

    # ------------------------------------------------------------- tray
    def _tray(self):
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip(f"PieGuy — hold {self._trigger_name()}")
        m = QMenu()
        m.addAction("⚙  Settings", self.show_settings)
        self.pause_act = QAction("⏸  Pause", m, checkable=True)
        self.pause_act.toggled.connect(self._pause)
        m.addAction(self.pause_act)
        m.addAction("⏺  Record steps", self.start_recording)
        if W.IS_WIN and not W.self_elevated():
            m.addAction("🛡  Restart as administrator", lambda: W.relaunch_admin("--replace"))
        m.addSeparator()
        m.addAction("✕  Quit", self.quit)
        self.tray.setContextMenu(m)
        self.tray.activated.connect(lambda r: self.show_settings() if r in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
        self.tray.show()
        self._tray_menu = m

    def _pause(self, on):
        self.hook.paused = on
        self.toast.show_msg("PieGuy paused" if on else "PieGuy active", "", "⏸" if on else "🥧", "ok", 1, 1500)

    def show_settings(self):
        self.settings_win.reveal()

    def quit(self):
        self.probe.shutdown()
        self.hook.stop()
        self.tray.hide()
        self.qapp.quit()

    def _single_instance(self):
        """A hidden native window other PieGuy launches can post 'show' / 'quit' to
        (works across admin / non-admin, unlike pipes)."""
        if not W.IS_WIN:
            return
        self.ipc_win = QWidget()
        self.ipc_win.setWindowTitle(W.ipc_title())
        hwnd = int(self.ipc_win.winId())
        self._ipc_msg = W.ipc_listen(hwnd)
        self._ipc_filter = _IpcFilter(self._ipc_msg, self._on_ipc)
        self.qapp.installNativeEventFilter(self._ipc_filter)

    def _on_ipc(self, cmd):
        if cmd == W.IPC_QUIT:
            log.info("replaced by a new instance")
            self.quit()
        else:
            self.show_settings()

    # ------------------------------------------------------------- hook
    @Slot(str, object)
    def _on_hook(self, kind, data):
        try:
            if kind == "trigger_down":
                if self.overlay.is_open:
                    self.overlay.close_pie()
                    return
                pie = next((p for p in self.cfg["pies"] if p["id"] == data["pie"]), None)
                if not pie:
                    return
                self.target_hwnd = W.fg_window()
                self.current_pie = pie
                self.last_trigger = data
                self.hook.pie_open = True
                self.overlay.open(pie, self.cfg["settings"])
            elif kind == "trigger_up":
                if self.overlay.is_open and self.current_pie:
                    self.overlay.trigger_released(data["ms"], self.current_pie.get("tap", "sticky"))
            elif kind == "pie_key":
                self.overlay.key(data)
            elif kind == "captured":
                if data and data["kind"] == "key":
                    data["name"] = W.vk_name(data["vk"])
                elif data:
                    data["name"] = {"middle": "Middle mouse", "x1": "Mouse back (X1)", "x2": "Mouse forward (X2)"}[data["button"]]
                self.bridge.triggerCaptured.emit(json.dumps(data))
            elif kind == "rec_key" and self.recorder:
                self.recorder.on_key(data)
            elif kind == "rec_mouse" and self.recorder:
                self.recorder.on_mouse(data)
            elif kind == "rec_wheel" and self.recorder:
                self.recorder.on_wheel(data)
            elif kind == "rec_stop":
                self.stop_recording()
            elif kind == "pick":
                self.hook.picking = False
                self.pick_hint.hide()
                self._stop_probe()
                if data:
                    snap = self.probe.lookup(data["x"], data["y"])
                    if snap:
                        self.ripple(data["x"], data["y"], snap.get("label", "Picked"))
                        self._picked(json.dumps(uia.slim(snap), ensure_ascii=False))
                    else:
                        def work(x=data["x"], y=data["y"]):
                            self.picked_ready.emit(json.dumps(uia.slim(uia.describe_point(x, y)), ensure_ascii=False))
                        threading.Thread(target=work, daemon=True).start()
                else:
                    self._picked("null")
        except Exception:
            log.exception("hook event %s", kind)

    def _picked(self, js):
        self.bridge.elementPicked.emit(js)
        self.settings_win.reveal()

    def _passthrough(self):
        t = self.last_trigger or {}
        if t.get("kind") == "key":
            W.key_tap(t["vk"])
        elif t.get("kind") == "mouse":
            W.mouse_button_inject(t["button"])

    def preview_pie(self, pie_id):
        pie = next((p for p in self.cfg["pies"] if p["id"] == pie_id), None)
        if pie:
            self.current_pie = pie
            self.target_hwnd = 0
            self.hook.pie_open = True
            self.overlay.open(pie, self.cfg["settings"], sticky=True)

    # ------------------------------------------------------ dynamic menus
    def resolve_children(self, item):
        if item.get("kind") != "contacts":
            return item.get("children") or []
        hist = self.cfg.get("history", {})
        contacts = list(self.cfg.get("contacts", []))
        order = {c["id"]: i for i, c in enumerate(contacts)}
        contacts.sort(key=lambda c: (-hist.get(c["id"], 0), order[c["id"]]))
        contacts = contacts[: int(item.get("max", 8) or 8)]
        ver = self.cfg["settings"].get("telegram_version", "k")
        steps = item.get("steps") or []
        if not steps and item.get("flow"):
            steps = [{"type": "run_flow", "flow": item["flow"], "enabled": True}]
        out = []
        for c in contacts:
            out.append({"id": f"{item.get('id')}:{c['id']}", "label": c.get("name", "?"),
                        "icon": c.get("emoji") or "💬", "color": c.get("color") or item.get("color"),
                        "kind": "action", "steps": steps,
                        "_vars": {"contact_url": E.telegram_url(c.get("url", ""), ver),
                                  "contact_name": c.get("name", ""), "contact_id": c["id"],
                                  "contact_raw": c.get("url", "")},
                        "_parent": item.get("label", "")})
        return out

    # ------------------------------------------------------------ run
    def activate(self, item, from_settings=False):
        steps = item.get("steps") or []
        if not steps:
            self.toast.show_msg(f"“{item.get('label', 'Item')}” has no steps yet", "Add some in Settings", "🫥", "err", 0, 3000)
            return
        if self.runner is not None:
            self.toast.show_msg("Another action is still running", "Click here to cancel it", "⏳", "err", 0, 2500)
            return
        vars_ = {"target_hwnd": 0 if from_settings else self.target_hwnd}
        vars_.update(item.get("_vars") or {})
        title = item.get("label", "Action")
        if item.get("_parent"):
            title = f"{item['_parent']} → {title}"
        self.ui.icon = item.get("icon", "") if len(item.get("icon", "")) <= 4 else ""
        runner = E.Runner(copy.deepcopy(self.cfg), self.ui, on_history=self._history)
        self.runner = runner

        def work():
            try:
                runner.run(steps, vars_, title)
                if not any(s.get("type") == "notify" for s in steps):
                    self.ui._toast.emit(title, "ok", self.ui.icon)
            except E.Abort as e:
                self.ui._toast.emit(f"{title}: {e}", "err", "↩")
            except Exception as e:
                log.exception("run %s", title)
                self.ui._toast.emit(str(e), "err", "")
            finally:
                self.runner = None

        threading.Thread(target=work, daemon=True, name="PieGuyRun").start()

    def cancel_run(self):
        if self.runner:
            self.runner.cancel.set()

    def _history(self, contact_id):
        self.cfg.setdefault("history", {})[contact_id] = time.time()
        self.save_later.emit()

    # ------------------------------------------------------ recording
    def start_recording(self):
        if self.recorder:
            return
        if self._settings_win is not None:
            self._settings_win.hide()
        self.recorder = Recorder(ignore_rect=self.recpill.rect_global, probe_lookup=self.probe.lookup,
                                 on_step=self._rec_step, probe_hover=self.probe.hover_parent)
        self.hook.recording = True
        self.recpill.start("Recording", "Do the task normally · the box shows what PieGuy sees · Pause to stop")
        self.rec_timer.start()
        self._start_probe("rec")

    def _poll_rec(self):
        if self.recorder:
            try:
                self.recorder.poll_foreground()
            except Exception:
                log.exception("poll")

    def _rec_step(self, step, label, count):
        self.recpill.step_added(count, label)
        if step.get("type") == "click_element" and "_x" in step and not step.get("double"):
            tg = step.get("target") or {}
            self.ripple(step["_x"], step["_y"], (tg.get("label") or "Click").split(" · ")[0],
                        "blocked" if tg.get("blocked") else ("ok" if tg.get("element") else "weak"))

    def ripple(self, x, y, label, status="ok"):
        r = self.ripples[self._ripple_i % len(self.ripples)]
        self._ripple_i += 1
        r.fire(x, y, label, status)

    def stop_recording(self):
        if not self.recorder:
            return
        self.hook.recording = False
        self.rec_timer.stop()
        self.recpill.hide()
        self._stop_probe()
        steps = self.recorder.finish()
        self.recorder = None
        self.bridge.recordingDone.emit(json.dumps(steps, ensure_ascii=False))
        self.settings_win.reveal()

    def start_pick(self):
        self.settings_win.hide()
        self.hook.picking = True
        self.pick_hint.start("Pick an element", "Hover — the box shows what will be targeted · click it · Esc cancels",
                             show_timer=False)
        self._start_probe("pick")

    # ---------------------------------------------------- live highlight
    def _start_probe(self, mode):
        self._probe_mode = mode
        if W.IS_WIN:
            self.probe.start_probe()

    def _stop_probe(self):
        if self.recorder is None and not self.hook.picking:
            self._probe_mode = ""
            self.probe.stop_probe()
            self.highlight.hide()

    @Slot(object)
    def _on_probe(self, desc):
        hud = self.recpill if self._probe_mode == "rec" else self.pick_hint
        if not self._probe_mode or not desc:
            self.highlight.hide()
            return
        el = desc.get("element") or {}
        app_name = os.path.splitext((desc.get("window") or {}).get("process") or "app")[0]
        if desc.get("blocked"):
            status, sub = "blocked", "Admin app — hidden from PieGuy. Settings › Run as administrator"
            hud.set_warn(f"{app_name} is an admin app (hidden) → Settings › Run as administrator")
        else:
            named = el.get("name") or el.get("auto_id") or (el.get("anchor") or {}).get("name")
            status = ("pick" if self._probe_mode == "pick" else "ok") if named else "weak"
            sub = ("PieGuy recognizes this" if named else "No name — will use its position in the window")
            if (desc.get("window") or {}).get("popup"):
                sub += "  ·  menu / dropdown"
            hud.set_warn("")
        rect = el.get("rect") or desc.get("wrect")
        if rect:
            self.highlight.set_target(rect, desc.get("label") or uia.element_label(desc), sub, status)

def main():
    os.makedirs(C.DATA, exist_ok=True)
    logging.basicConfig(filename=C.LOG_PATH, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    background = "--background" in sys.argv
    sys.excepthook = lambda *a: log.error("uncaught", exc_info=a)
    try:  # WebEngine must be loaded before the QApplication exists
        import PySide6.QtWebEngineWidgets  # noqa: F401
    except ImportError:
        log.error("PySide6 WebEngine missing - run install.bat")
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    qapp = QApplication(sys.argv)
    logging.getLogger("comtypes").setLevel(logging.WARNING)
    replace = "--replace" in sys.argv
    if W.IS_WIN:
        mutex, state = W.instance_mutex()
        if state == "exists" and not replace:
            W.ipc_send(W.IPC_SHOW)  # already running: just bring up its settings
            return 0
        cfg = C.load()
        if cfg["settings"].get("run_as_admin") and not W.self_elevated():
            W.release_handle(mutex)
            extra = " ".join(["--replace"] + [a for a in ("--background", "--enable-startup") if a in sys.argv])
            if W.relaunch_admin(extra):
                return 0  # the elevated copy takes over
            mutex, state = W.instance_mutex()  # UAC declined: carry on without admin
        if state == "exists":  # --replace: ask the old instance to quit, then take over
            W.ipc_send(W.IPC_QUIT)
            for _ in range(50):
                time.sleep(0.15)
                mutex, state = W.instance_mutex()
                if state == "ok":
                    break
            if state != "ok":
                log.error("old instance did not exit")
                return 0
        qapp._pieguy_mutex = mutex
    qapp.setQuitOnLastWindowClosed(False)
    qapp.setApplicationName("PieGuy")
    qapp.setWindowIcon(app_icon())
    f = QFont("Segoe UI")
    f.setPixelSize(13)
    qapp.setFont(f)
    app = PieGuyApp(qapp, background)
    qapp._pieguy = app
    return qapp.exec()
