"""The pie itself (QPainter, 60 fps), plus toast + REC pill widgets.

Interaction model (marking-menu style):
  hold key  -> pie at cursor; flick toward a slice; release = run it
  submenus  -> cross the outer edge or dwell -> child pie opens at the cursor
  quick tap -> pie stays open (sticky): click / 1-9 / Esc / Backspace
"""
from __future__ import annotations

import math
import os
import time

from PySide6.QtCore import Qt, QTimer, QPointF, QRectF, Signal, QFileInfo, QSize
from PySide6.QtGui import (QRegion, QPainter, QColor, QPainterPath, QRadialGradient, QFont, QPen, QCursor,
                           QGuiApplication, QFontMetrics, QPixmap, QIcon, QLinearGradient, QConicalGradient)
from PySide6.QtWidgets import QWidget, QFileIconProvider

from . import win32 as W

EMOJI_FONT = "Segoe UI Emoji"
UI_FONT = "Segoe UI Variable Display"


def ease_out_back(t, s=1.4):
    t -= 1
    return t * t * ((s + 1) * t + s) + 1


def ease_out(t):
    return 1 - (1 - t) ** 3


def qcolor(hexstr, alpha=255):
    c = QColor(hexstr or "#8b5cf6")
    if not c.isValid():
        c = QColor("#8b5cf6")
    c.setAlpha(alpha)
    return c


_icon_cache: dict[str, QPixmap] = {}


def icon_pixmap(icon: str, size: int):
    """File-based icons: .png/.ico/.svg images, or any .exe/.lnk (its own icon)."""
    if not icon or len(icon) < 4 or not (":" in icon or icon.startswith("\\\\")):
        return None
    key = f"{icon}|{size}"
    if key in _icon_cache:
        return _icon_cache[key]
    pm = None
    if os.path.exists(icon):
        ext = os.path.splitext(icon)[1].lower()
        if ext in (".png", ".jpg", ".jpeg", ".ico", ".svg", ".bmp", ".webp"):
            pm = QIcon(icon).pixmap(QSize(size, size))
        else:
            pm = QFileIconProvider().icon(QFileInfo(icon)).pixmap(QSize(size, size))
    _icon_cache[key] = pm
    return pm


class Level:
    def __init__(self, items, title, center: QPointF, via=None):
        self.items = items
        self.title = title
        self.center = QPointF(center)
        self.hover = [0.0] * len(items)
        self.born = time.time()
        self.via = via  # the item that opened this level


class Backdrop(QWidget):
    clicked = Signal(object)

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool |
                         Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.dim = 0.28
        self.levels: list[Level] = []
        self.accent = QColor("#8b5cf6")
        self._native = False

    def showEvent(self, e):
        if not self._native:
            W.set_noactivate(int(self.winId()))
            self._native = True

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(0, 0, 0, max(2, int(255 * self.dim * 0.55))))
        if self.levels:
            c = self.mapFromGlobal(self.levels[-1].center.toPoint())
            g = QRadialGradient(QPointF(c), max(self.width(), self.height()) * 0.6)
            g.setColorAt(0, QColor(0, 0, 0, 0))
            g.setColorAt(1, QColor(0, 0, 0, int(255 * self.dim * 0.8)))
            p.fillRect(self.rect(), g)
        # ghosts of parent levels, connected by a light trail
        for i, lv in enumerate(self.levels[:-1]):
            a = QPointF(self.mapFromGlobal(lv.center.toPoint()))
            b = QPointF(self.mapFromGlobal(self.levels[i + 1].center.toPoint()))
            pen = QPen(qcolor(self.accent.name(), 110), 2, Qt.DashLine)
            p.setPen(pen)
            p.drawLine(a, b)
            p.setPen(QPen(QColor(255, 255, 255, 50), 1.5))
            p.setBrush(QColor(18, 18, 28, 150))
            p.drawEllipse(a, 34, 34)
            via = self.levels[i + 1].via or {}
            p.setFont(QFont(EMOJI_FONT, 16))
            p.setPen(QColor(255, 255, 255, 200))
            p.drawText(QRectF(a.x() - 30, a.y() - 30, 60, 60), Qt.AlignCenter, via.get("icon", "•") or "•")
        p.end()

    def mousePressEvent(self, e):
        self.clicked.emit(e.button())


class PieOverlay(QWidget):
    activated = Signal(dict)       # leaf item chosen
    closed = Signal()
    passthrough = Signal()         # quick tap should re-send the trigger key

    MARGIN = 70

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool |
                         Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.backdrop = Backdrop()
        self.backdrop.clicked.connect(self._backdrop_click)
        self.timer = QTimer(self)
        self.timer.setInterval(14)
        self.timer.timeout.connect(self._tick)
        self.levels: list[Level] = []
        self.s = {}
        self.pie = {}
        self.mode = "hold"
        self.hovered = -1
        self.hover_since = 0.0
        self.dist = 0.0
        self.angle = 0.0
        self.closing = 0.0
        self.flash = None
        self.is_open = False
        self._native = False
        self._resolver = None

    # --------------------------------------------------------------- API
    def set_resolver(self, fn):
        """fn(item) -> list of child items (handles dynamic 'contacts' menus)."""
        self._resolver = fn

    def open(self, pie: dict, settings: dict, sticky=False):
        self.pie, self.s = pie, settings
        self.R = int(settings.get("radius", 170))
        self.r0 = int(settings.get("inner", 58))
        size = 2 * (self.R + self.MARGIN)
        self.resize(size, size)
        self.mode = "sticky" if sticky else "hold"
        self.closing = 0.0
        self.flash = None
        self.hovered = -1
        self.levels = []
        self.backdrop.dim = float(settings.get("dim", 0.28))
        self.backdrop.accent = qcolor(settings.get("accent"))
        pos = QCursor.pos()
        screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        self.backdrop.setGeometry(screen.geometry())
        self._push_level(pie.get("items", []), pie.get("name", "PieGuy"), QPointF(pos), None)
        self.is_open = True
        self.backdrop.levels = self.levels
        self.backdrop.show()
        self.show()
        self.raise_()
        self.timer.start()

    def trigger_released(self, held_ms: float, tap_behavior: str):
        if not self.is_open or self.mode != "hold":
            return
        item = self._hover_item()
        if item is not None:
            self._choose(item)
            return
        if held_ms < float(self.s.get("tap_ms", 220)) or len(self.levels) > 1:
            if tap_behavior == "passthrough" and len(self.levels) == 1:
                self.close_pie()
                self.passthrough.emit()
            elif tap_behavior == "close" and len(self.levels) == 1:
                self.close_pie()
            else:
                self.mode = "sticky"
            return
        self.close_pie()

    def key(self, name: str):
        if not self.is_open:
            return
        if name == "esc":
            self.close_pie()
        elif name == "back":
            self._back()
        elif name == "enter":
            item = self._hover_item()
            if item is not None:
                self._choose(item)
        elif name.isdigit():
            lv = self.levels[-1]
            i = int(name) - 1
            if 0 <= i < len(lv.items):
                self._choose(lv.items[i], by_key=True)

    def close_pie(self):
        if not self.is_open:
            return
        self.is_open = False
        self.closing = time.time()

    # --------------------------------------------------------- internals
    def _push_level(self, items, title, center: QPointF, via):
        screen = QGuiApplication.screenAt(center.toPoint()) or QGuiApplication.primaryScreen()
        g = screen.availableGeometry()
        half = self.width() / 2
        pad = self.R + 12
        cx = min(max(center.x(), g.left() + pad), g.right() - pad)
        cy = min(max(center.y(), g.top() + pad), g.bottom() - pad)
        c = QPointF(cx, cy)
        if abs(cx - center.x()) > 1 or abs(cy - center.y()) > 1:
            QCursor.setPos(int(cx), int(cy))
        lv = Level(items, title, c, via)
        self.levels.append(lv)
        self.move(int(cx - half), int(cy - half))
        self.hovered = -1
        self.hover_since = time.time()
        self.backdrop.levels = self.levels
        self.backdrop.update()

    def _children(self, item):
        if self._resolver:
            return self._resolver(item)
        return item.get("children") or []

    def _has_children(self, item):
        return item.get("kind") in ("submenu", "contacts")

    def _hover_item(self):
        lv = self.levels[-1] if self.levels else None
        if lv and 0 <= self.hovered < len(lv.items):
            return lv.items[self.hovered]
        return None

    def _choose(self, item, by_key=False):
        if self._has_children(item):
            kids = self._children(item)
            if not kids:
                return
            center = QPointF(QCursor.pos()) if not by_key else self.levels[-1].center
            self._push_level(kids, item.get("label", ""), center, item)
            if by_key:
                QCursor.setPos(self.levels[-1].center.toPoint())
            self.mode = "sticky" if self.mode == "sticky" or by_key else self.mode
            return
        self.flash = (len(self.levels) - 1, self.levels[-1].items.index(item) if item in self.levels[-1].items else -1)
        self.close_pie()
        self.activated.emit(item)

    def _back(self):
        if len(self.levels) > 1:
            self.levels.pop()
            QCursor.setPos(self.levels[-1].center.toPoint())
            self.move(int(self.levels[-1].center.x() - self.width() / 2), int(self.levels[-1].center.y() - self.width() / 2))
            self.levels[-1].born = time.time() - 1
            self.backdrop.levels = self.levels
            self.backdrop.update()
        else:
            self.close_pie()

    def _tick(self):
        now = time.time()
        if self.closing:
            if now - self.closing > 0.11:
                self.timer.stop()
                self.hide()
                self.backdrop.hide()
                self.levels = []
                self.closed.emit()
                return
            self.update()
            return
        lv = self.levels[-1]
        pos = QPointF(QCursor.pos())
        dx, dy = pos.x() - lv.center.x(), pos.y() - lv.center.y()
        self.dist = math.hypot(dx, dy)
        self.angle = math.atan2(dy, dx)
        n = len(lv.items)
        new = -1
        if n and self.dist > float(self.s.get("deadzone", 26)):
            step = 2 * math.pi / n
            a = (self.angle + math.pi / 2 + step / 2) % (2 * math.pi)
            new = int(a // step) % n
        if new != self.hovered:
            self.hovered = new
            self.hover_since = now
        for i in range(n):
            target = 1.0 if i == self.hovered else 0.0
            lv.hover[i] += (target - lv.hover[i]) * 0.32
        item = self._hover_item()
        if item is not None and self._has_children(item):
            dwell = (now - self.hover_since) * 1000 >= float(self.s.get("submenu_dwell_ms", 420))
            if self.dist > self.R + 16 or (dwell and self.dist > self.r0):
                self._choose(item)
        self.update()

    def _backdrop_click(self, button):
        if button == Qt.RightButton:
            self._back()
        else:
            self.close_pie()

    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton:
            self._back()
            return
        if e.button() != Qt.LeftButton:
            return
        item = self._hover_item()
        if item is not None and self.dist > self.R + 45:
            self.close_pie()  # clicked well outside the ring
        elif item is not None:
            self._choose(item)
        elif len(self.levels) > 1:
            self._back()
        else:
            self.close_pie()

    def showEvent(self, e):
        if not self._native:
            W.set_noactivate(int(self.winId()))
            self._native = True

    # ------------------------------------------------------------ paint
    def _slice_path(self, c: QPointF, r_in, r_out, a1, a2, gap):
        gi = gap / max(r_in, 1)
        go = gap / max(r_out, 1)
        p = QPainterPath()
        p.moveTo(c.x() + r_in * math.cos(a1 + gi), c.y() + r_in * math.sin(a1 + gi))
        p.lineTo(c.x() + r_out * math.cos(a1 + go), c.y() + r_out * math.sin(a1 + go))
        ro = QRectF(c.x() - r_out, c.y() - r_out, 2 * r_out, 2 * r_out)
        p.arcTo(ro, -math.degrees(a1 + go), -math.degrees((a2 - go) - (a1 + go)))
        p.lineTo(c.x() + r_in * math.cos(a2 - gi), c.y() + r_in * math.sin(a2 - gi))
        ri = QRectF(c.x() - r_in, c.y() - r_in, 2 * r_in, 2 * r_in)
        p.arcTo(ri, -math.degrees(a2 - gi), math.degrees((a2 - gi) - (a1 + gi)))
        p.closeSubpath()
        return p

    def paintEvent(self, e):
        if not self.levels:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        lv = self.levels[-1]
        c = QPointF(self.width() / 2, self.height() / 2)
        now = time.time()
        anim = max(60.0, float(self.s.get("anim_ms", 170))) / 1000
        t = min(1.0, (now - lv.born) / anim)
        scale = 0.72 + 0.28 * ease_out_back(t)
        opacity = ease_out(t)
        if self.closing:
            k = min(1.0, (now - self.closing) / 0.11)
            opacity *= 1 - k
            scale *= 1 + 0.06 * k
        p.setOpacity(max(0.0, opacity))
        p.translate(c)
        p.scale(scale, scale)
        p.translate(-c)
        self._paint_level(p, lv, c, now, t)
        p.end()

    def _paint_level(self, p: QPainter, lv: Level, c: QPointF, now, t):
        accent, accent2 = qcolor(self.s.get("accent")), qcolor(self.s.get("accent2", "#22d3ee"))
        R, r0 = self.R, self.r0
        n = max(1, len(lv.items))
        step = 2 * math.pi / n
        gap = float(self.s.get("gap", 3))

        # soft outer glow + glass disc
        glow = QRadialGradient(c, R + 60)
        glow.setColorAt(0.55, qcolor(accent.name(), 70))
        glow.setColorAt(0.8, qcolor(accent2.name(), 22))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(c, R + 60, R + 60)
        disc = QRadialGradient(c, R)
        disc.setColorAt(0.0, QColor(30, 27, 48, 235))
        disc.setColorAt(1.0, QColor(14, 14, 24, 235))
        p.setBrush(disc)
        p.drawEllipse(c, R + 4, R + 4)
        ring = QConicalGradient(c, 90)
        ring.setColorAt(0, qcolor(accent.name(), 180))
        ring.setColorAt(0.5, qcolor(accent2.name(), 180))
        ring.setColorAt(1, qcolor(accent.name(), 180))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(ring, 1.4))
        p.drawEllipse(c, R + 4, R + 4)

        sticky = self.mode == "sticky"
        for i, item in enumerate(lv.items):
            stagger = min(1.0, max(0.0, (t * 1.6) - i * 0.05))
            if stagger <= 0:
                continue
            h = lv.hover[i]
            col = qcolor(item.get("color"))
            a_mid = -math.pi / 2 + i * step
            a1, a2 = a_mid - step / 2, a_mid + step / 2
            push = 10 * h
            ro = R + push
            ri = r0 + 4 + push * 0.4
            path = self._slice_path(c, ri, ro, a1, a2, gap)
            p.setOpacity(p.opacity())
            # base fill
            fill = QRadialGradient(c, ro)
            fill.setColorAt(ri / ro, QColor(38, 36, 58, int(200 + 40 * h)))
            fill.setColorAt(1.0, qcolor(col.name(), int(40 + 170 * h)))
            p.setPen(Qt.NoPen)
            p.setBrush(fill)
            p.drawPath(path)
            if h > 0.02:  # neon edge on the hovered slice
                for w_, al in ((10, 30), (5, 60), (1.6, 230)):
                    p.setPen(QPen(qcolor(col.name(), int(al * h)), w_))
                    p.setBrush(Qt.NoBrush)
                    p.drawPath(path)
            # colored rim arc (identity color even when idle)
            rim = self._slice_path(c, ro - 4, ro, a1, a2, gap)
            p.setPen(Qt.NoPen)
            p.setBrush(qcolor(col.name(), int(140 + 115 * h)))
            p.drawPath(rim)
            # icon + label
            rm = (ri + ro) / 2 + 4 * h
            ix, iy = c.x() + rm * math.cos(a_mid), c.y() + rm * math.sin(a_mid)
            show_labels = self.s.get("show_labels", True)
            icon_y = iy - (9 if show_labels else 0)
            isz = int(26 + 6 * h)
            pm = icon_pixmap(item.get("icon", ""), isz * 2)
            if pm is not None and not pm.isNull():
                p.drawPixmap(QRectF(ix - isz / 2, icon_y - isz / 2, isz, isz), pm, QRectF(pm.rect()))
            else:
                f = QFont(EMOJI_FONT)
                f.setPixelSize(isz)
                p.setFont(f)
                p.setPen(QColor(255, 255, 255))
                p.drawText(QRectF(ix - 40, icon_y - isz, 80, 2 * isz), Qt.AlignCenter, item.get("icon") or "•")
            if show_labels:
                f = QFont(UI_FONT)
                f.setPixelSize(int(self.s.get("font_size", 12)) + (1 if h > 0.5 else 0))
                f.setWeight(QFont.DemiBold if h > 0.5 else QFont.Medium)
                p.setFont(f)
                fm = QFontMetrics(f)
                arc_w = max(50, min(120, (rm * step) - 10))
                label = fm.elidedText(item.get("label", ""), Qt.ElideRight, int(arc_w))
                p.setPen(QColor(255, 255, 255, int(190 + 65 * h)))
                p.drawText(QRectF(ix - arc_w / 2, iy + 12, arc_w, fm.height() + 2), Qt.AlignHCenter | Qt.AlignTop, label)
            if self._has_children(item):  # submenu chevron on the rim
                cx_, cy_ = c.x() + (ro + 9) * math.cos(a_mid), c.y() + (ro + 9) * math.sin(a_mid)
                p.save()
                p.translate(cx_, cy_)
                p.rotate(math.degrees(a_mid))
                p.setPen(QPen(qcolor(col.name(), 230), 1.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                p.setBrush(qcolor(col.name(), 230))
                p.drawPolygon([QPointF(-3.5, -5.5), QPointF(4.5, 0), QPointF(-3.5, 5.5)])
                p.restore()
            if sticky and i < 9:
                bx, by = c.x() + (ri + 13) * math.cos(a_mid), c.y() + (ri + 13) * math.sin(a_mid)
                f = QFont(UI_FONT)
                f.setPixelSize(10)
                f.setBold(True)
                p.setFont(f)
                p.setPen(QColor(255, 255, 255, 120))
                p.drawText(QRectF(bx - 8, by - 8, 16, 16), Qt.AlignCenter, str(i + 1))

        # direction beam on the inner ring
        if self.hovered >= 0 and not self.closing:
            col = qcolor(lv.items[self.hovered].get("color"))
            r = r0 + 1
            rect = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
            span = 34
            for w_, al in ((9, 50), (3, 255)):
                p.setPen(QPen(qcolor(col.name(), al), w_, Qt.SolidLine, Qt.RoundCap))
                p.drawArc(rect, int((-math.degrees(self.angle) - span / 2) * 16), int(span * 16))

        # center hub
        hub = QRadialGradient(c, r0)
        hub.setColorAt(0, QColor(44, 40, 70, 250))
        hub.setColorAt(1, QColor(20, 19, 32, 250))
        p.setBrush(hub)
        hub_ring = QConicalGradient(c, (time.time() * 60) % 360)
        hub_ring.setColorAt(0, accent)
        hub_ring.setColorAt(0.5, accent2)
        hub_ring.setColorAt(1, accent)
        p.setPen(QPen(hub_ring, 2))
        p.drawEllipse(c, r0 - 6, r0 - 6)
        item = self._hover_item()
        title = item.get("label", "") if item else lv.title
        sub = ""
        if item is not None:
            sub = "open ›" if self._has_children(item) else ("click" if self.mode == "sticky" else "release")
        elif len(self.levels) > 1:
            sub = "‹ back"
        elif self.mode == "sticky":
            sub = "Esc to close"
        f = QFont(UI_FONT)
        f.setPixelSize(13)
        f.setWeight(QFont.Bold)
        p.setFont(f)
        fm = QFontMetrics(f)
        p.setPen(QColor(255, 255, 255))
        box = 2 * (r0 - 12)
        p.drawText(QRectF(c.x() - box / 2, c.y() - 16, box, 20), Qt.AlignCenter,
                   fm.elidedText(title, Qt.ElideRight, int(box)))
        f.setPixelSize(10)
        f.setWeight(QFont.Medium)
        p.setFont(f)
        p.setPen(qcolor(accent2.name(), 220))
        p.drawText(QRectF(c.x() - box / 2, c.y() + 4, box, 16), Qt.AlignCenter, sub)


class Toast(QWidget):
    """Bottom-center pill: progress while an action runs, then result."""
    cancel_clicked = Signal()

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool |
                         Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.resize(430, 64)
        self.text, self.sub, self.icon, self.kind, self.frac = "", "", "", "run", 0.0
        self.accent = QColor("#8b5cf6")
        self.hide_timer = QTimer(self, singleShot=True, timeout=self.hide)
        self.anim = QTimer(self, interval=30, timeout=self.update)
        self._native = False

    def showEvent(self, e):
        if not self._native:
            W.set_noactivate(int(self.winId()))
            self._native = True

    def show_msg(self, text, sub="", icon="", kind="run", frac=0.0, ms=0):
        self.text, self.sub, self.icon, self.kind, self.frac = text, sub, icon, kind, frac
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        g = screen.availableGeometry()
        self.move(g.center().x() - self.width() // 2, g.bottom() - self.height() - 28)
        self.show()
        self.raise_()
        self.anim.start()
        self.hide_timer.stop()
        if ms:
            self.hide_timer.start(ms)
        self.update()

    def hideEvent(self, e):
        self.anim.stop()

    def mousePressEvent(self, e):
        if self.kind == "run":
            self.cancel_clicked.emit()
        self.hide()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(4, 4, -4, -4)
        col = {"ok": QColor("#34d399"), "err": QColor("#f87171"), "run": self.accent}.get(self.kind, self.accent)
        p.setPen(QPen(qcolor(col.name(), 150), 1.2))
        p.setBrush(QColor(18, 17, 30, 240))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        # icon bubble
        ib = QRectF(r.left() + 10, r.top() + 10, r.height() - 20, r.height() - 20)
        p.setPen(Qt.NoPen)
        p.setBrush(qcolor(col.name(), 60))
        p.drawEllipse(ib)
        f = QFont(EMOJI_FONT)
        f.setPixelSize(18)
        p.setFont(f)
        p.setPen(QColor("white"))
        glyph = self.icon or {"ok": "✓", "err": "✕", "run": "⚡"}[self.kind]
        p.drawText(ib, Qt.AlignCenter, glyph)
        tx = ib.right() + 12
        f = QFont(UI_FONT)
        f.setPixelSize(14)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        fm = QFontMetrics(f)
        p.drawText(QRectF(tx, r.top() + 9, r.right() - tx - 40, 20), Qt.AlignLeft | Qt.AlignVCenter,
                   fm.elidedText(self.text, Qt.ElideRight, int(r.right() - tx - 44)))
        f.setPixelSize(11)
        f.setWeight(QFont.Normal)
        p.setFont(f)
        p.setPen(QColor(255, 255, 255, 160))
        fm = QFontMetrics(f)
        p.drawText(QRectF(tx, r.top() + 29, r.right() - tx - 40, 18), Qt.AlignLeft | Qt.AlignVCenter,
                   fm.elidedText(self.sub, Qt.ElideRight, int(r.right() - tx - 44)))
        if self.kind == "run":
            bar = QRectF(tx, r.bottom() - 9, r.right() - tx - 26, 3)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 30))
            p.drawRoundedRect(bar, 1.5, 1.5)
            g = QLinearGradient(bar.topLeft(), bar.topRight())
            g.setColorAt(0, self.accent)
            g.setColorAt(1, QColor("#22d3ee"))
            p.setBrush(g)
            w = max(8.0, bar.width() * self.frac)
            p.drawRoundedRect(QRectF(bar.left(), bar.top(), w, 3), 1.5, 1.5)
            p.setPen(QColor(255, 255, 255, 120))
            f.setPixelSize(13)
            p.setFont(f)
            p.drawText(QRectF(r.right() - 34, r.top(), 26, r.height()), Qt.AlignCenter, "✕")


# ------------------------------------------------------ coordinate mapping
def _screens():
    return QGuiApplication.screens() or [QGuiApplication.primaryScreen()]


def phys_to_logical(x, y):
    """Windows physical pixels -> Qt logical coords (per-monitor DPI aware).
    Qt keeps each screen's origin in native pixels and scales inside it."""
    for s in _screens():
        g, dpr = s.geometry(), s.devicePixelRatio()
        if g.x() <= x < g.x() + g.width() * dpr and g.y() <= y < g.y() + g.height() * dpr:
            return QPointF(g.x() + (x - g.x()) / dpr, g.y() + (y - g.y()) / dpr)
    return QPointF(x, y)


def logical_to_phys(x, y):
    for s in _screens():
        g, dpr = s.geometry(), s.devicePixelRatio()
        if g.contains(int(x), int(y)):
            return int(g.x() + (x - g.x()) * dpr), int(g.y() + (y - g.y()) * dpr)
    return int(x), int(y)


def phys_rect_to_logical(rect):
    a = phys_to_logical(rect[0], rect[1])
    b = phys_to_logical(rect[2], rect[3])
    return QRectF(a, b).normalized()


class _ClickThrough(QWidget):
    """Base for overlays you can see but never hit (clicks & UI Automation pass through)."""

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool |
                         Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._native = False

    def showEvent(self, e):
        if not self._native:
            W.set_clickthrough(int(self.winId()))
            self._native = True


STATUS_COL = {"ok": "#34d399", "weak": "#fbbf24", "blocked": "#f87171", "pick": "#22d3ee"}


class ElemHighlight(_ClickThrough):
    """Glowing box around the element under the mouse + a name chip ('I see this')."""
    PAD, CHIP = 6, 30

    def __init__(self):
        super().__init__()
        self.label, self.sub, self.status = "", "", "ok"
        self.box = QRectF()
        self.chip = QRectF()
        self.born = time.time()
        self.anim = QTimer(self, interval=33, timeout=self.update)

    def set_target(self, rect_phys, label, sub, status):
        r = phys_rect_to_logical(rect_phys)
        if r.width() < 4 or r.height() < 4:
            self.hide()
            return
        f = QFont(UI_FONT)
        f.setPixelSize(12)
        f.setWeight(QFont.DemiBold)
        fm = QFontMetrics(f)
        chip_w = min(520, max(fm.horizontalAdvance(label), fm.horizontalAdvance(sub) - 20) + 44)
        P, CH = self.PAD, self.CHIP
        scr = (QGuiApplication.screenAt(r.center().toPoint()) or QGuiApplication.primaryScreen()).availableGeometry()
        # chip goes beside the element (keeps neighbours - e.g. other menu items - visible),
        # else above, else below
        if r.right() + P + 8 + chip_w < scr.right():
            chip = QRectF(r.right() + P + 8, r.center().y() - CH / 2, chip_w, CH)
        elif r.left() - P - 8 - chip_w > scr.left():
            chip = QRectF(r.left() - P - 8 - chip_w, r.center().y() - CH / 2, chip_w, CH)
        elif r.top() - P - CH - 4 > scr.top():
            chip = QRectF(r.left() - P, r.top() - P - CH - 4, chip_w, CH)
        else:
            chip = QRectF(r.left() - P, r.bottom() + P + 4, chip_w, CH)
        boxg = r.adjusted(-P, -P, P, P)
        win = boxg.united(chip).adjusted(-2, -2, 2, 2)
        if (label, status) != (self.label, self.status) or self.box.size() != r.size():
            self.born = time.time()
        self.label, self.sub, self.status = label, sub, status
        self.box = r.translated(-win.left(), -win.top())
        self.chip = chip.translated(-win.left(), -win.top())
        self.setGeometry(win.toRect())
        # carve the element's interior out of the window: the mouse/UIA always reach the real app
        region = QRegion(0, 0, int(win.width()) + 1, int(win.height()) + 1)
        inner = self.box.adjusted(3, 3, -3, -3).toRect()
        if inner.width() > 2 and inner.height() > 2:
            region = region.subtracted(QRegion(inner))
        self.setMask(region)
        if not self.isVisible():
            self.show()
        self.anim.start()
        self.update()

    def hideEvent(self, e):
        self.anim.stop()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        col = QColor(STATUS_COL.get(self.status, "#34d399"))
        k = min(1.0, (time.time() - self.born) / 0.18)
        pulse = 0.5 + 0.5 * math.sin(time.time() * 6)
        grow = (1 - ease_out(k)) * 8
        box = self.box.adjusted(-2 - grow, -2 - grow, 2 + grow, 2 + grow)
        p.setBrush(Qt.NoBrush)
        for w_, al in ((9, 40), (4, 120)):
            p.setPen(QPen(qcolor(col.name(), int(al * (0.6 + 0.4 * pulse))), w_))
            p.drawRoundedRect(box, 6, 6)
        p.setPen(QPen(col, 2))
        p.drawRoundedRect(box, 6, 6)
        p.setPen(QPen(QColor("white"), 2.4, Qt.SolidLine, Qt.RoundCap))
        L = min(10.0, box.width() / 3, box.height() / 3)
        for cx, cy, dx, dy in ((box.left(), box.top(), 1, 1), (box.right(), box.top(), -1, 1),
                               (box.left(), box.bottom(), 1, -1), (box.right(), box.bottom(), -1, -1)):
            p.drawLine(QPointF(cx, cy), QPointF(cx + dx * L, cy))
            p.drawLine(QPointF(cx, cy), QPointF(cx, cy + dy * L))
        chip = self.chip
        p.setOpacity(ease_out(k))
        p.setPen(QPen(qcolor(col.name(), 210), 1))
        p.setBrush(QColor(16, 15, 26, 240))
        p.drawRoundedRect(chip, 9, 9)
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawEllipse(QPointF(chip.left() + 13, chip.center().y()), 4, 4)
        f = QFont(UI_FONT)
        f.setPixelSize(12)
        f.setWeight(QFont.DemiBold)
        fm = QFontMetrics(f)
        p.setFont(f)
        p.setPen(QColor("white"))
        tw = chip.width() - 32
        p.drawText(QRectF(chip.left() + 24, chip.top() + 2, tw, 15), Qt.AlignLeft | Qt.AlignVCenter,
                   fm.elidedText(self.label, Qt.ElideRight, int(tw)))
        f.setPixelSize(10)
        f.setWeight(QFont.Normal)
        p.setFont(f)
        p.setPen(qcolor(col.name(), 235))
        p.drawText(QRectF(chip.left() + 24, chip.top() + 16, tw, 12), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetrics(f).elidedText(self.sub, Qt.ElideRight, int(tw)))
        p.end()


class ClickRipple(_ClickThrough):
    """Ring burst + '✓ label' where a click was recorded."""
    SIZE = 360

    def __init__(self):
        super().__init__()
        self.resize(self.SIZE, 110)
        self.t0 = 0.0
        self.label = ""
        self.col = QColor("#34d399")
        self.timer = QTimer(self, interval=16, timeout=self._tick)

    def fire(self, x_phys, y_phys, label, status="ok"):
        pt = phys_to_logical(x_phys, y_phys)
        self.label, self.col, self.t0 = label, QColor(STATUS_COL.get(status, "#34d399")), time.time()
        self.move(int(pt.x() - 55), int(pt.y() - 55))
        self.show()
        self.raise_()
        self.timer.start()

    def _tick(self):
        if time.time() - self.t0 > 1.1:
            self.timer.stop()
            self.hide()
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        k = min(1.0, (time.time() - self.t0) / 0.6)
        c = QPointF(55, 55)
        for i, delay in enumerate((0.0, 0.18)):
            kk = max(0.0, min(1.0, (k - delay) / (1 - delay)))
            if kk <= 0:
                continue
            p.setPen(QPen(qcolor(self.col.name(), int(230 * (1 - kk))), 3 - i))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(c, 8 + 40 * ease_out(kk), 8 + 40 * ease_out(kk))
        p.setPen(Qt.NoPen)
        p.setBrush(qcolor(self.col.name(), int(255 * (1 - k * 0.6))))
        p.drawEllipse(c, 5, 5)
        fade = 1.0 if time.time() - self.t0 < 0.8 else max(0.0, 1 - (time.time() - self.t0 - 0.8) / 0.3)
        p.setOpacity(fade)
        f = QFont(UI_FONT)
        f.setPixelSize(12)
        f.setWeight(QFont.Bold)
        fm = QFontMetrics(f)
        text = fm.elidedText("✓ " + self.label, Qt.ElideRight, self.SIZE - 90)
        w = fm.horizontalAdvance(text) + 18
        chip = QRectF(70, 62, w, 24)
        p.setBrush(QColor(16, 15, 26, 235))
        p.setPen(QPen(qcolor(self.col.name(), 200), 1))
        p.drawRoundedRect(chip, 12, 12)
        p.setFont(f)
        p.setPen(self.col)
        p.drawText(chip, Qt.AlignCenter, text)
        p.end()


class RecPill(QWidget):
    """Top-center recorder HUD: timer, step count, last step, warnings. Click to stop."""
    stop_clicked = Signal()

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool |
                         Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.resize(540, 64)
        self.t0 = time.time()
        self.text, self.sub, self.last, self.warn = "Recording", "", "", ""
        self.count = 0
        self.show_timer = True
        self.flash = 0.0
        self.timer = QTimer(self, interval=60, timeout=self.update)
        self._native = False

    def start(self, text, sub, show_timer=True):
        self.text, self.sub, self.t0 = text, sub, time.time()
        self.last, self.warn, self.count, self.show_timer = "", "", 0, show_timer
        g = (QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()).availableGeometry()
        self.move(g.center().x() - self.width() // 2, g.top() + 12)
        self.show()
        self.timer.start()

    def step_added(self, count, label):
        self.count, self.last, self.flash = count, label, time.time()
        self.update()

    def set_warn(self, text):
        if text != self.warn:
            self.warn = text
            self.update()

    def rect_global(self):
        """Physical-pixel rect so the recorder can ignore clicks on us."""
        g = self.frameGeometry()
        l, t = logical_to_phys(g.left(), g.top())
        r, b = logical_to_phys(g.right(), g.bottom())
        return l - 4, t - 4, r + 4, b + 4

    def showEvent(self, e):
        if not self._native:
            W.set_noactivate(int(self.winId()))
            self._native = True

    def hideEvent(self, e):
        self.timer.stop()

    def mousePressEvent(self, e):
        self.stop_clicked.emit()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        warn = bool(self.warn)
        edge = QColor("#fbbf24") if warn else QColor(248, 113, 113)
        p.setPen(QPen(qcolor(edge.name(), 190), 1.3))
        p.setBrush(QColor(20, 14, 22, 242))
        p.drawRoundedRect(r, 22, 22)
        fl = max(0.0, 1 - (time.time() - self.flash) / 0.5)
        if fl > 0:
            p.setPen(QPen(qcolor("#34d399", int(200 * fl)), 2.5))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 21, 21)
        pulse = 0.5 + 0.5 * math.sin(time.time() * 5)
        dot = QPointF(r.left() + 24, r.top() + 22)
        p.setPen(Qt.NoPen)
        p.setBrush(qcolor(edge.name(), int(90 * pulse)))
        p.drawEllipse(dot, 11, 11)
        p.setBrush(edge)
        p.drawEllipse(dot, 6, 6)
        f = QFont(UI_FONT)
        f.setPixelSize(13)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.setPen(QColor("white"))
        el = int(time.time() - self.t0)
        head = self.text + (f"  {el // 60:02d}:{el % 60:02d}" if self.show_timer else "")
        if self.show_timer:
            head += f"  ·  {self.count} step{'s' if self.count != 1 else ''}"
        p.drawText(QRectF(r.left() + 42, r.top() + 10, r.width() - 120, 20), Qt.AlignLeft | Qt.AlignVCenter, head)
        f.setPixelSize(11)
        f.setWeight(QFont.Normal)
        p.setFont(f)
        fm = QFontMetrics(f)
        if warn:
            line, col = "⚠ " + self.warn, QColor("#fde68a")
        elif self.last:
            line, col = "✓ " + self.last, QColor("#a7f3d0")
        else:
            line, col = self.sub, QColor(255, 255, 255, 150)
        p.setPen(col)
        p.drawText(QRectF(r.left() + 42, r.top() + 31, r.width() - 120, 18), Qt.AlignLeft | Qt.AlignVCenter,
                   fm.elidedText(line, Qt.ElideRight, int(r.width() - 122)))
        stop = QRectF(r.right() - 70, r.top() + 13, 60, r.height() - 26)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(248, 113, 113, 60))
        p.drawRoundedRect(stop, stop.height() / 2, stop.height() / 2)
        p.setPen(QColor("#fecaca"))
        f.setPixelSize(11)
        f.setWeight(QFont.Bold)
        p.setFont(f)
        p.drawText(stop, Qt.AlignCenter, "■ Stop" if self.show_timer else "Cancel")
