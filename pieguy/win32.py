"""Low-level Windows helpers (ctypes only, 64-bit safe).

Everything that touches the OS lives here: input injection, windows,
processes, clipboard (text + real file drops), system proxy, Explorer
selection, startup registration. Importable on non-Windows for tests.
"""
from __future__ import annotations

import os
import re
import sys
import time
import ctypes
import subprocess

IS_WIN = sys.platform == "win32"
MAGIC = 0x50494547  # 'PIEG' - tags our own injected input so hooks ignore it
CREATE_NO_WINDOW = 0x08000000

if IS_WIN:
    from ctypes import wintypes as wt
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    wininet = ctypes.WinDLL("wininet", use_last_error=True)
    ULONG_PTR = ctypes.c_size_t
    LRESULT = ctypes.c_ssize_t
else:  # pragma: no cover - lets the module import on Linux for tests
    wt = None

# --------------------------------------------------------------------- keys
VK = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "return": 0x0D, "shift": 0x10,
    "ctrl": 0x11, "control": 0x11, "alt": 0x12, "menu": 0x12, "pause": 0x13,
    "capslock": 0x14, "caps": 0x14, "esc": 0x1B, "escape": 0x1B, "space": 0x20,
    "pageup": 0x21, "pgup": 0x21, "pagedown": 0x22, "pgdn": 0x22, "end": 0x23,
    "home": 0x24, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "printscreen": 0x2C, "prtsc": 0x2C, "insert": 0x2D, "ins": 0x2D, "delete": 0x2E, "del": 0x2E,
    "win": 0x5B, "lwin": 0x5B, "rwin": 0x5C, "apps": 0x5D, "contextmenu": 0x5D,
    "num0": 0x60, "num1": 0x61, "num2": 0x62, "num3": 0x63, "num4": 0x64,
    "num5": 0x65, "num6": 0x66, "num7": 0x67, "num8": 0x68, "num9": 0x69,
    "multiply": 0x6A, "add": 0x6B, "subtract": 0x6D, "decimal": 0x6E, "divide": 0x6F,
    "numlock": 0x90, "scrolllock": 0x91,
    "lshift": 0xA0, "rshift": 0xA1, "lctrl": 0xA2, "rctrl": 0xA3, "lalt": 0xA4, "ralt": 0xA5,
    "browser_back": 0xA6, "browser_forward": 0xA7, "browser_refresh": 0xA8,
    "volume_mute": 0xAD, "volume_down": 0xAE, "volume_up": 0xAF,
    "media_next": 0xB0, "media_prev": 0xB1, "media_stop": 0xB2, "media_play_pause": 0xB3,
    ";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD, ".": 0xBE, "/": 0xBF, "`": 0xC0,
    "[": 0xDB, "\\": 0xDC, "]": 0xDD, "'": 0xDE,
}
for _i in range(1, 25):
    VK[f"f{_i}"] = 0x6F + _i
for _c in "abcdefghijklmnopqrstuvwxyz":
    VK[_c] = ord(_c.upper())
for _d in "0123456789":
    VK[_d] = ord(_d)

_PRETTY = {0x14: "CapsLock", 0x20: "Space", 0xC0: "`", 0x13: "Pause", 0x91: "ScrollLock",
           0x2D: "Insert", 0x5D: "Menu", 0xA5: "Right Alt", 0xA3: "Right Ctrl", 0x5B: "Win",
           0x09: "Tab", 0x1B: "Esc", 0x0D: "Enter", 0xA4: "Left Alt", 0xA2: "Left Ctrl"}
MODIFIER_VKS = {0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5}
EXTENDED_VKS = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2C, 0x2D, 0x2E,
                0x5B, 0x5C, 0x5D, 0x6F, 0x90, 0xA3, 0xA5, 0xA6, 0xA7, 0xA8, 0xAD,
                0xAE, 0xAF, 0xB0, 0xB1, 0xB2, 0xB3}


def vk_name(vk: int) -> str:
    if vk in _PRETTY:
        return _PRETTY[vk]
    for k, v in VK.items():
        if v == vk and len(k) > 1 and k not in ("return", "control", "menu", "escape", "caps"):
            return k.capitalize() if not k.startswith("f") else k.upper()
    if 0x30 <= vk <= 0x5A:
        return chr(vk)
    return f"VK {vk}"


def parse_combo(combo: str) -> list[int]:
    keys = []
    for part in combo.lower().replace(" ", "").split("+"):
        if not part:
            continue
        if part not in VK:
            raise ValueError(f"Unknown key '{part}'")
        keys.append(VK[part])
    return keys


# ------------------------------------------------------------------ structs
if IS_WIN:
    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                    ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                    ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD),
                    ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class MSLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [("pt", wt.POINT), ("mouseData", wt.DWORD), ("flags", wt.DWORD),
                    ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]

    HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)
    WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.SendInput.restype = wt.UINT
    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
    user32.SetWindowsHookExW.restype = ctypes.c_void_p
    user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wt.WPARAM, wt.LPARAM]
    user32.CallNextHookEx.restype = LRESULT
    user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
    user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
    user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.GetForegroundWindow.restype = wt.HWND
    user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
    user32.GetWindowThreadProcessId.restype = wt.DWORD
    user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    user32.GetWindowTextLengthW.argtypes = [wt.HWND]
    user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    user32.IsWindowVisible.argtypes = [wt.HWND]
    user32.IsIconic.argtypes = [wt.HWND]
    user32.IsWindow.argtypes = [wt.HWND]
    user32.IsZoomed.argtypes = [wt.HWND]
    user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [wt.HWND]
    user32.BringWindowToTop.argtypes = [wt.HWND]
    user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
    user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
    user32.GetAncestor.restype = wt.HWND
    user32.WindowFromPoint.argtypes = [wt.POINT]
    user32.WindowFromPoint.restype = wt.HWND
    user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
    user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
    user32.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_ssize_t]
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.GetKeyState.argtypes = [ctypes.c_int]
    user32.GetKeyState.restype = ctypes.c_short
    user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
    user32.OpenClipboard.argtypes = [wt.HWND]
    user32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
    user32.SetClipboardData.restype = wt.HANDLE
    user32.GetClipboardData.argtypes = [wt.UINT]
    user32.GetClipboardData.restype = wt.HANDLE
    user32.IsClipboardFormatAvailable.argtypes = [wt.UINT]
    user32.RegisterClipboardFormatW.argtypes = [wt.LPCWSTR]
    user32.RegisterClipboardFormatW.restype = wt.UINT
    user32.MapVirtualKeyW.argtypes = [wt.UINT, wt.UINT]
    user32.MapVirtualKeyW.restype = wt.UINT
    user32.GetKeyboardLayout.argtypes = [wt.DWORD]
    user32.GetKeyboardLayout.restype = ctypes.c_void_p
    user32.ToUnicodeEx.argtypes = [wt.UINT, wt.UINT, ctypes.POINTER(ctypes.c_ubyte), wt.LPWSTR,
                                   ctypes.c_int, wt.UINT, ctypes.c_void_p]
    user32.GetKeyboardState.argtypes = [ctypes.POINTER(ctypes.c_ubyte)]
    kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
    kernel32.GetModuleHandleW.restype = ctypes.c_void_p
    kernel32.GetCurrentThreadId.restype = wt.DWORD
    kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wt.HGLOBAL
    kernel32.GlobalLock.argtypes = [wt.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wt.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wt.HGLOBAL]
    kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    kernel32.OpenProcess.restype = wt.HANDLE
    kernel32.CloseHandle.argtypes = [wt.HANDLE]
    kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
    shell32.DragQueryFileW.argtypes = [wt.HANDLE, wt.UINT, wt.LPWSTR, wt.UINT]
    shell32.DragQueryFileW.restype = wt.UINT
    shell32.ShellExecuteW.argtypes = [wt.HWND, wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, ctypes.c_int]
    shell32.ShellExecuteW.restype = ctypes.c_void_p
    wininet.InternetSetOptionW.argtypes = [ctypes.c_void_p, wt.DWORD, ctypes.c_void_p, wt.DWORD]

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x1, 0x2, 0x4
MOUSE = {"left": (0x2, 0x4), "right": (0x8, 0x10), "middle": (0x20, 0x40)}
MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL = 0x800, 0x1000


# ------------------------------------------------------------------- input
def _send(inputs):
    arr = (INPUT * len(inputs))(*inputs)
    user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


def _key_input(vk: int, up: bool) -> "INPUT":
    flags = KEYEVENTF_KEYUP if up else 0
    if vk in EXTENDED_VKS:
        flags |= KEYEVENTF_EXTENDEDKEY
    scan = user32.MapVirtualKeyW(vk, 0) & 0xFF
    return INPUT(type=INPUT_KEYBOARD, u=_INPUTUNION(ki=KEYBDINPUT(vk, scan, flags, 0, MAGIC)))


def key_tap(vk: int):
    _send([_key_input(vk, False), _key_input(vk, True)])


def key_down(vk: int):
    _send([_key_input(vk, False)])


def key_up(vk: int):
    _send([_key_input(vk, True)])


def release_modifiers():
    """Lift any modifier the user is physically holding so injected combos are clean."""
    ups = [_key_input(vk, True) for vk in (0x10, 0x11, 0x12, 0x5B, 0x5C)
           if user32.GetAsyncKeyState(vk) & 0x8000]
    if ups:
        _send(ups)


def send_combo(combo: str, hold_ms: int = 20):
    """'ctrl+shift+t'. Several combos may be separated by spaces or commas."""
    for chunk in re.split(r"[\s,]+", combo.strip()):
        if not chunk:
            continue
        vks = parse_combo(chunk)
        _send([_key_input(v, False) for v in vks])
        time.sleep(hold_ms / 1000)
        _send([_key_input(v, True) for v in reversed(vks)])
        time.sleep(0.03)


def type_text(text: str, delay_ms: int = 4):
    for ch in text:
        if ch == "\n":
            key_tap(0x0D)
        else:
            code_units = ch.encode("utf-16-le")
            ins = []
            for i in range(0, len(code_units), 2):
                cu = int.from_bytes(code_units[i:i + 2], "little")
                ins.append(INPUT(type=INPUT_KEYBOARD, u=_INPUTUNION(ki=KEYBDINPUT(0, cu, KEYEVENTF_UNICODE, 0, MAGIC))))
                ins.append(INPUT(type=INPUT_KEYBOARD, u=_INPUTUNION(ki=KEYBDINPUT(0, cu, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, MAGIC))))
            _send(ins)
        if delay_ms:
            time.sleep(delay_ms / 1000)


def cursor_pos() -> tuple[int, int]:
    p = wt.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def mouse_click(x: int | None = None, y: int | None = None, button: str = "left", double: bool = False):
    if x is not None:
        user32.SetCursorPos(int(x), int(y))
        time.sleep(0.03)
    down, up = MOUSE[button]
    for _ in range(2 if double else 1):
        _send([INPUT(type=INPUT_MOUSE, u=_INPUTUNION(mi=MOUSEINPUT(0, 0, 0, down, 0, MAGIC))),
               INPUT(type=INPUT_MOUSE, u=_INPUTUNION(mi=MOUSEINPUT(0, 0, 0, up, 0, MAGIC)))])
        time.sleep(0.06)


def mouse_button(button: str, up: bool):
    flag = MOUSE[button][1 if up else 0]
    _send([INPUT(type=INPUT_MOUSE, u=_INPUTUNION(mi=MOUSEINPUT(0, 0, 0, flag, 0, MAGIC)))])


def mouse_drag(x1, y1, x2, y2, steps: int = 20):
    user32.SetCursorPos(int(x1), int(y1))
    time.sleep(0.05)
    mouse_button("left", False)
    for i in range(1, steps + 1):
        user32.SetCursorPos(int(x1 + (x2 - x1) * i / steps), int(y1 + (y2 - y1) * i / steps))
        time.sleep(0.012)
    mouse_button("left", True)


def mouse_nudge():
    """Tiny real mouse move so apps notice the pointer (hover menus/tooltips)."""
    for dx in (1, -1):
        _send([INPUT(type=INPUT_MOUSE, u=_INPUTUNION(mi=MOUSEINPUT(dx, 0, 0, 0x1, 0, MAGIC)))])
        time.sleep(0.02)


def mouse_scroll(amount: int, horizontal: bool = False):
    flag = MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL
    _send([INPUT(type=INPUT_MOUSE, u=_INPUTUNION(mi=MOUSEINPUT(0, 0, ctypes.c_uint32(int(amount) * 120).value, flag, 0, MAGIC)))])


def mouse_button_inject(button: str):
    """Re-inject a mouse click we swallowed (quick tap pass-through)."""
    names = {"middle": "middle", "left": "left", "right": "right"}
    if button in names:
        mouse_click(button=names[button])
    elif button in ("x1", "x2"):
        data = 1 if button == "x1" else 2
        _send([INPUT(type=INPUT_MOUSE, u=_INPUTUNION(mi=MOUSEINPUT(0, 0, data, 0x80, 0, MAGIC))),
               INPUT(type=INPUT_MOUSE, u=_INPUTUNION(mi=MOUSEINPUT(0, 0, data, 0x100, 0, MAGIC)))])


def mods_down() -> set[str]:
    s = set()
    if user32.GetAsyncKeyState(0x11) & 0x8000:
        s.add("ctrl")
    if user32.GetAsyncKeyState(0x10) & 0x8000:
        s.add("shift")
    if user32.GetAsyncKeyState(0x12) & 0x8000:
        s.add("alt")
    if (user32.GetAsyncKeyState(0x5B) | user32.GetAsyncKeyState(0x5C)) & 0x8000:
        s.add("win")
    return s


def vk_to_char(vk: int, scan: int, hwnd=None) -> str:
    """Translate a key to the character the active layout would produce (for recording)."""
    try:
        state = (ctypes.c_ubyte * 256)()
        for m in (0x10, 0x11, 0x12, 0x14):
            ks = user32.GetKeyState(m)
            state[m] = 0x80 if user32.GetAsyncKeyState(m) & 0x8000 else 0
            if m == 0x14:
                state[m] |= ks & 1
        hwnd = hwnd or user32.GetForegroundWindow()
        tid = user32.GetWindowThreadProcessId(hwnd, None)
        hkl = user32.GetKeyboardLayout(tid)
        buf = ctypes.create_unicode_buffer(8)
        n = user32.ToUnicodeEx(vk, scan, state, buf, 8, 0x4, hkl)
        return buf.value[:n] if n > 0 else ""
    except Exception:
        return ""


# ----------------------------------------------------------------- windows
def fg_window() -> int:
    return user32.GetForegroundWindow() or 0


def window_title(hwnd) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def window_class(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def window_pid(hwnd) -> int:
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


_exe_cache: dict[int, str] = {}


def pid_exe(pid: int) -> str:
    if pid in _exe_cache:
        return _exe_cache[pid]
    path = ""
    h = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if h:
        try:
            size = wt.DWORD(1024)
            buf = ctypes.create_unicode_buffer(1024)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                path = buf.value
        finally:
            kernel32.CloseHandle(h)
    if len(_exe_cache) > 500:
        _exe_cache.clear()
    if path:
        _exe_cache[pid] = path
    return path


def window_exe(hwnd) -> str:
    return pid_exe(window_pid(hwnd))


def window_process(hwnd) -> str:
    return os.path.basename(window_exe(hwnd)).lower()


def window_rect(hwnd) -> tuple[int, int, int, int]:
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def top_level(hwnd) -> int:
    return user32.GetAncestor(hwnd, 2) or hwnd  # GA_ROOT


def list_windows() -> list[dict]:
    out = []

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            title = window_title(hwnd)
            if title:
                ex = user32.GetWindowLongPtrW(hwnd, -20)
                if not ex & 0x80:  # skip WS_EX_TOOLWINDOW
                    out.append({"hwnd": int(hwnd), "title": title, "cls": window_class(hwnd),
                                "process": window_process(hwnd), "exe": window_exe(hwnd)})
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def _match_text(pattern: str, text: str) -> bool:
    if not pattern:
        return True
    if pattern.startswith("re:"):
        try:
            return re.search(pattern[3:], text, re.I) is not None
        except re.error:
            return False
    return pattern.lower() in text.lower()


def find_window(title: str = "", process: str = "", cls: str = "") -> int:
    process = (process or "").lower().strip()
    if process and not process.endswith(".exe"):
        process += ".exe"
    for w in list_windows():
        if process and w["process"] != process:
            continue
        if cls and w["cls"] != cls:
            continue
        if _match_text(title, w["title"]):
            return w["hwnd"]
    return 0


def wait_window(title="", process="", timeout=10.0, cancel=None) -> int:
    end = time.time() + timeout
    while time.time() < end:
        h = find_window(title, process)
        if h:
            return h
        if cancel is not None and cancel.is_set():
            return 0
        time.sleep(0.15)
    return 0


def focus_window(hwnd) -> bool:
    """Bring a window to the front despite Windows' foreground-lock rules."""
    if not hwnd or not IS_WIN:
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    fg = user32.GetForegroundWindow()
    if fg == hwnd:
        return True
    cur_tid = kernel32.GetCurrentThreadId()
    for attempt in range(2):
        fg = user32.GetForegroundWindow()
        fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        if attempt == 1:
            # Tapping ALT unlocks SetForegroundWindow for our process (fallback only:
            # some apps flash their menu bar on a lone Alt).
            _send([_key_input(0x12, False), _key_input(0x12, True)])
        attached = False
        if fg_tid and fg_tid != cur_tid:
            attached = bool(user32.AttachThreadInput(cur_tid, fg_tid, True))
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(cur_tid, fg_tid, False)
        for _ in range(12):
            if user32.GetForegroundWindow() == hwnd:
                return True
            time.sleep(0.02)
    return user32.GetForegroundWindow() == hwnd


def window_op(hwnd, op: str):
    if not hwnd:
        return
    if op == "minimize":
        user32.ShowWindow(hwnd, 6)
    elif op == "maximize":
        user32.ShowWindow(hwnd, 3)
    elif op == "restore":
        user32.ShowWindow(hwnd, 9)
    elif op == "close":
        user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
    elif op == "topmost":
        ex = user32.GetWindowLongPtrW(hwnd, -20)
        on_top = bool(ex & 0x8)
        user32.SetWindowPos(hwnd, -2 if on_top else -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
    elif op == "toggle_max":
        user32.ShowWindow(hwnd, 9 if user32.IsZoomed(hwnd) else 3)


def set_noactivate(hwnd):
    """WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST - overlay never steals focus."""
    if not IS_WIN:
        return
    ex = user32.GetWindowLongPtrW(hwnd, -20)
    user32.SetWindowLongPtrW(hwnd, -20, ex | 0x08000000 | 0x80 | 0x8)


def set_clickthrough(hwnd):
    """Overlay that is seen but never hit: clicks and UIA hit-tests pass through it."""
    if not IS_WIN:
        return
    ex = user32.GetWindowLongPtrW(hwnd, -20)
    user32.SetWindowLongPtrW(hwnd, -20, ex | 0x20 | 0x80000 | 0x08000000 | 0x80 | 0x8)


def dark_titlebar(hwnd):
    if not IS_WIN:
        return
    try:
        dwm = ctypes.WinDLL("dwmapi")
        val = ctypes.c_int(1)
        dwm.DwmSetWindowAttribute(wt.HWND(hwnd), 20, ctypes.byref(val), 4)
        backdrop = ctypes.c_int(2)  # Mica on Win11
        dwm.DwmSetWindowAttribute(wt.HWND(hwnd), 38, ctypes.byref(backdrop), 4)
    except Exception:
        pass


# --------------------------------------------------------------- processes
if IS_WIN:
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
    advapi32.GetTokenInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD)]
    shell32.IsUserAnAdmin.restype = wt.BOOL

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                    ("th32DefaultHeapID", ULONG_PTR), ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                    ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wt.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

    kernel32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wt.HANDLE
    kernel32.Process32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]


def _exe_name(name: str) -> str:
    name = (name or "").lower().strip()
    return name if name.endswith(".exe") else name + ".exe"


def process_list() -> list[tuple[int, str]]:
    """(pid, exe name) for every process - works for admin processes too."""
    out = []
    snap = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snap or snap == wt.HANDLE(-1).value:
        return out
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            out.append((e.th32ProcessID, e.szExeFile.lower()))
            ok = kernel32.Process32NextW(snap, ctypes.byref(e))
    finally:
        kernel32.CloseHandle(snap)
    return out


def running_processes() -> set[str]:
    try:
        return {n for _, n in process_list()}
    except Exception:
        return set()


def process_running(name: str) -> bool:
    return _exe_name(name) in running_processes()


def pids_of(name: str) -> list[int]:
    name = _exe_name(name)
    return [pid for pid, n in process_list() if n == name]


def process_path(name: str) -> str:
    """Full exe path of a running process by name (also for elevated ones)."""
    for pid in pids_of(name):
        p = pid_exe(pid)
        if p:
            return p
    return ""


# ----------------------------------------------------- elevation (UIPI)
def self_elevated() -> bool:
    try:
        return bool(shell32.IsUserAnAdmin())
    except Exception:
        return False


_elev: dict[int, bool] = {}


def pid_elevated(pid: int) -> bool:
    """True when `pid` runs as administrator (or we can't even inspect it)."""
    if pid in _elev:
        return _elev[pid]
    res = True
    h = kernel32.OpenProcess(0x1000, False, pid)
    if h:
        tok = wt.HANDLE()
        try:
            if advapi32.OpenProcessToken(h, 0x8, ctypes.byref(tok)):  # TOKEN_QUERY
                val, ret = wt.DWORD(), wt.DWORD()
                if advapi32.GetTokenInformation(tok, 20, ctypes.byref(val), 4, ctypes.byref(ret)):
                    res = bool(val.value)
                kernel32.CloseHandle(tok)
        finally:
            kernel32.CloseHandle(h)
    if len(_elev) > 300:
        _elev.clear()
    _elev[pid] = res
    return res


def uipi_blocked(hwnd) -> bool:
    """Windows hides/blocks input & UI info of admin windows from non-admin apps."""
    if not hwnd or self_elevated():
        return False
    pid = window_pid(hwnd)
    return bool(pid) and pid != os.getpid() and pid_elevated(pid)


# ----------------------------------------------------------- window sets
def is_popup(hwnd) -> bool:
    style = user32.GetWindowLongPtrW(hwnd, -16) & 0xFFFFFFFF
    return bool(style & 0x80000000) and (style & 0x00C00000) != 0x00C00000  # WS_POPUP w/o caption


def _cloaked(hwnd) -> bool:
    try:
        dwm = ctypes.WinDLL("dwmapi")
        val = ctypes.c_int(0)
        dwm.DwmGetWindowAttribute(wt.HWND(hwnd), 14, ctypes.byref(val), 4)  # DWMWA_CLOAKED
        return bool(val.value)
    except Exception:
        return False


def window_at(x: int, y: int, skip_pid: int = 0) -> int:
    """Top-most real window under (x, y), ignoring our own overlays."""
    found = []

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
            l, t, r, b = window_rect(hwnd)
            if l <= x < r and t <= y < b and window_pid(hwnd) != skip_pid and not _cloaked(hwnd):
                ex = user32.GetWindowLongPtrW(hwnd, -20)
                if not (ex & 0x20 and ex & 0x80000):  # skip click-through layered overlays
                    found.append(int(hwnd))
                    return False
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found[0] if found else 0


def windows_of(process: str = "", pid: int = 0) -> list[int]:
    """Visible top-level windows of a process in z-order - *including* untitled popups
    (dropdown lists, context menus, tray menus)."""
    process = _exe_name(process) if process else ""
    out = []

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            p = window_pid(hwnd)
            if (pid and p == pid) or (process and os.path.basename(pid_exe(p)).lower() == process):
                l, t, r, b = window_rect(hwnd)
                if r - l > 2 and b - t > 2:
                    out.append(int(hwnd))
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def kill_process(name: str, tree: bool = True, force: bool = True) -> bool:
    args = ["taskkill", "/IM", _exe_name(name)]
    if tree:
        args.append("/T")
    if force:
        args.append("/F")
    r = subprocess.run(args, capture_output=True, creationflags=CREATE_NO_WINDOW)
    return r.returncode == 0


def shell_open(target: str, args: str = "", cwd: str = "", admin: bool = False) -> bool:
    verb = "runas" if admin else "open"
    r = shell32.ShellExecuteW(None, verb, target, args or None, cwd or None, 1)
    return (r or 0) > 32


def run_unelevated(target: str, args: str = "", cwd: str = "") -> bool:
    """Start something as the normal user even when PieGuy runs as admin
    (so Firefox etc. don't end up elevated)."""
    if not self_elevated():
        return shell_open(target, args, cwd)
    if not args:
        # explorer hands the launch to the already-running (non-admin) shell
        subprocess.Popen(["explorer.exe", target], creationflags=CREATE_NO_WINDOW)
        return True
    cmd = subprocess.list2cmdline([target]) + " " + args
    subprocess.Popen(["runas", "/trustlevel:0x20000", cmd], creationflags=CREATE_NO_WINDOW)
    return True


def instance_mutex(name: str = "Local\\PieGuy-instance"):
    """(handle, 'ok') if we are the only PieGuy, else (None, 'exists').
    Works across admin/non-admin instances (access-denied also means 'exists')."""
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
    kernel32.CreateMutexW.restype = wt.HANDLE
    h = kernel32.CreateMutexW(None, False, name)
    err = ctypes.get_last_error()
    if not h:
        return None, "exists"
    if err == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(h)
        return None, "exists"
    return h, "ok"


IPC_SHOW, IPC_QUIT = 1, 2


def ipc_title() -> str:
    return f"PieGuy-IPC-{os.environ.get('USERNAME', 'u')}"


def ipc_message() -> int:
    user32.RegisterWindowMessageW.argtypes = [wt.LPCWSTR]
    user32.RegisterWindowMessageW.restype = wt.UINT
    return user32.RegisterWindowMessageW("PieGuy-IPC-v1")


def ipc_listen(hwnd) -> int:
    """Let lower-integrity instances (non-admin) post to us when we run as admin."""
    msg = ipc_message()
    user32.ChangeWindowMessageFilterEx.argtypes = [wt.HWND, wt.UINT, wt.DWORD, ctypes.c_void_p]
    user32.ChangeWindowMessageFilterEx(hwnd, msg, 1, None)  # MSGFLT_ALLOW
    return msg


def ipc_send(cmd: int) -> bool:
    user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
    user32.FindWindowW.restype = wt.HWND
    h = user32.FindWindowW(None, ipc_title())
    if not h:
        return False
    return bool(user32.PostMessageW(h, ipc_message(), cmd, 0))


def release_handle(h):
    if h:
        kernel32.CloseHandle(h)


def relaunch_admin(extra: str = "--replace") -> bool:
    exe = sys.executable
    if getattr(sys, "frozen", False):
        return shell_open(exe, extra, admin=True)
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    main = os.path.abspath(sys.argv[0])
    return shell_open(pyw if os.path.exists(pyw) else exe, f'"{main}" {extra}', os.path.dirname(main), admin=True)


def find_app(name: str) -> str:
    """Locate an installed exe via App Paths registry, then common folders."""
    import winreg
    exe = name if name.lower().endswith(".exe") else name + ".exe"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as k:
                val, _ = winreg.QueryValueEx(k, "")
                if val and os.path.exists(val.strip('"')):
                    return val.strip('"')
        except OSError:
            pass
    guesses = {
        "firefox.exe": [r"%ProgramFiles%\Mozilla Firefox\firefox.exe", r"%ProgramFiles(x86)%\Mozilla Firefox\firefox.exe"],
        "chrome.exe": [r"%ProgramFiles%\Google\Chrome\Application\chrome.exe", r"%LocalAppData%\Google\Chrome\Application\chrome.exe"],
        "msedge.exe": [r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"],
    }
    for g in guesses.get(exe.lower(), []):
        p = os.path.expandvars(g)
        if os.path.exists(p):
            return p
    return ""


# --------------------------------------------------------------- clipboard
CF_UNICODETEXT, CF_HDROP = 13, 15


def _open_clipboard(retries=20):
    for _ in range(retries):
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.03)
    raise RuntimeError("Clipboard is busy")


def _set_global(fmt, data: bytes):
    h = kernel32.GlobalAlloc(0x0042, len(data))  # GMEM_MOVEABLE | GMEM_ZEROINIT
    p = kernel32.GlobalLock(h)
    ctypes.memmove(p, data, len(data))
    kernel32.GlobalUnlock(h)
    if not user32.SetClipboardData(fmt, h):
        kernel32.GlobalFree(h)


def set_clipboard_text(text: str):
    _open_clipboard()
    try:
        user32.EmptyClipboard()
        _set_global(CF_UNICODETEXT, (text + "\0").encode("utf-16-le"))
    finally:
        user32.CloseClipboard()


def set_clipboard_files(paths: list[str]):
    """Real file copy (CF_HDROP) - pasting into Explorer, Telegram Web, chats... works."""
    paths = [os.path.abspath(p) for p in paths if p]
    if not paths:
        raise RuntimeError("No files to copy")
    files = ("\0".join(paths) + "\0\0").encode("utf-16-le")
    header = ctypes.c_uint32(20).value.to_bytes(4, "little") + b"\0" * 12 + (1).to_bytes(4, "little")
    _open_clipboard()
    try:
        user32.EmptyClipboard()
        _set_global(CF_HDROP, header + files)
        effect = user32.RegisterClipboardFormatW("Preferred DropEffect")
        _set_global(effect, (1).to_bytes(4, "little"))  # DROPEFFECT_COPY
    finally:
        user32.CloseClipboard()


def get_clipboard_text() -> str:
    if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
        return ""
    _open_clipboard()
    try:
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return ""
        p = kernel32.GlobalLock(h)
        try:
            return ctypes.wstring_at(p)
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


def get_clipboard_files() -> list[str]:
    if not user32.IsClipboardFormatAvailable(CF_HDROP):
        return []
    _open_clipboard()
    try:
        h = user32.GetClipboardData(CF_HDROP)
        if not h:
            return []
        n = shell32.DragQueryFileW(h, 0xFFFFFFFF, None, 0)
        out = []
        for i in range(n):
            ln = shell32.DragQueryFileW(h, i, None, 0)
            buf = ctypes.create_unicode_buffer(ln + 1)
            shell32.DragQueryFileW(h, i, buf, ln + 1)
            out.append(buf.value)
        return out
    finally:
        user32.CloseClipboard()


# ------------------------------------------------------------ explorer sel
def explorer_selection(hwnd) -> list[str]:
    """Files selected in the Explorer window `hwnd`."""
    if not hwnd:
        return []
    cls = window_class(hwnd)
    if cls not in ("CabinetWClass", "ExploreWClass"):
        return []
    try:
        import comtypes
        import comtypes.client
        try:
            comtypes.CoInitialize()  # per worker thread; intentionally never uninitialized
        except OSError:
            pass
        paths = _explorer_selection_com(int(hwnd))
        if paths:
            return paths
    except ImportError:
        pass
    return _explorer_selection_ps(int(hwnd))


def _explorer_selection_com(hwnd: int) -> list[str]:
    import comtypes.client
    shell = comtypes.client.CreateObject("Shell.Application", dynamic=True)
    wins = shell.Windows()
    for i in range(wins.Count):
        try:
            w = wins.Item(i)
            if w is None or int(w.HWND) != hwnd:
                continue
            items = w.Document.SelectedItems()
            paths = [items.Item(j).Path for j in range(items.Count)]
            if paths:
                return paths
        except Exception:
            continue
    return []


def _explorer_selection_ps(hwnd: int) -> list[str]:
    ps = ("$s=New-Object -ComObject Shell.Application; foreach($w in $s.Windows()){ if($w.HWND -eq %d){"
          " $w.Document.SelectedItems() | %% { $_.Path } } }") % hwnd
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                             creationflags=CREATE_NO_WINDOW, timeout=10).stdout
        return [l.strip() for l in out.splitlines() if l.strip()]
    except Exception:
        return []


# ------------------------------------------------------------------- proxy
_INET_KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
DEFAULT_BYPASS = "localhost;127.*;10.*;172.16.*;172.17.*;172.18.*;172.19.*;172.20.*;172.21.*;172.22.*;172.23.*;172.24.*;172.25.*;172.26.*;172.27.*;172.28.*;172.29.*;172.30.*;172.31.*;192.168.*;<local>"


def _refresh_inet():
    wininet.InternetSetOptionW(None, 39, None, 0)  # SETTINGS_CHANGED
    wininet.InternetSetOptionW(None, 37, None, 0)  # REFRESH


def set_system_proxy(enable: bool, server: str = "127.0.0.1:10808", bypass: str = DEFAULT_BYPASS):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _INET_KEY, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 1 if enable else 0)
        if enable:
            winreg.SetValueEx(k, "ProxyServer", 0, winreg.REG_SZ, server)
            winreg.SetValueEx(k, "ProxyOverride", 0, winreg.REG_SZ, bypass or DEFAULT_BYPASS)
    _refresh_inet()


def system_proxy_state() -> tuple[bool, str]:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _INET_KEY) as k:
            en = winreg.QueryValueEx(k, "ProxyEnable")[0]
            try:
                srv = winreg.QueryValueEx(k, "ProxyServer")[0]
            except OSError:
                srv = ""
            return bool(en), srv
    except OSError:
        return False, ""


def port_open(host: str, port: int, timeout: float = 0.4) -> bool:
    import socket
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


# ----------------------------------------------------------------- startup
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def startup_command() -> str:
    exe = sys.executable
    if getattr(sys, "frozen", False):
        return f'"{exe}" --background'
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if not os.path.exists(pyw):
        pyw = exe
    main = os.path.abspath(sys.argv[0])
    return f'"{pyw}" "{main}" --background'


def _task_exists(name="PieGuy") -> bool:
    r = subprocess.run(["schtasks", "/Query", "/TN", name], capture_output=True, creationflags=CREATE_NO_WINDOW)
    return r.returncode == 0


def set_startup(enable: bool, admin: bool = False, name: str = "PieGuy") -> str:
    """Normal: HKCU Run key. Admin: a 'highest privileges' logon task (needs admin to create)."""
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        try:
            winreg.DeleteValue(k, name)
        except OSError:
            pass
        if enable and not admin:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, startup_command())
    if _task_exists(name):
        r = subprocess.run(["schtasks", "/Delete", "/TN", name, "/F"], capture_output=True, creationflags=CREATE_NO_WINDOW)
        if r.returncode != 0:
            shell_open("schtasks.exe", f"/Delete /TN {name} /F", admin=True)
    if enable and admin:
        if not self_elevated():
            return "needs-admin"
        r = subprocess.run(["schtasks", "/Create", "/F", "/TN", name, "/SC", "ONLOGON", "/RL", "HIGHEST",
                            "/TR", startup_command()], capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
        if r.returncode != 0:
            return (r.stderr or r.stdout or "schtasks failed").strip()
    return "ok"


def registered_startup(name: str = "PieGuy") -> str:
    """The command currently in the Run key ('' if none)."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return ""


def get_startup(name: str = "PieGuy") -> bool:
    if not IS_WIN:
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            winreg.QueryValueEx(k, name)
            return True
    except OSError:
        pass
    try:
        return _task_exists(name)
    except Exception:
        return False
