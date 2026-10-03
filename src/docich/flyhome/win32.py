"""Windows API (ctypes) によるウィンドウ探索・画面取得・キー入力。

- 画面取得: GDI の StretchBlt (COLORONCOLOR = 最近傍) でゲーム描画領域を直接 320x180 へ縮小して
  読む。ピクセルアートなので縮小はほぼ無劣化で、Python 側の処理量が 1/30 になる。
- 入力: SendInput のスキャンコード入力 (ゲームエンジンの生入力にも届く)。前面ウィンドウに
  しか届かないため、送る前に必ずゲームが前面かを確認する (他アプリへの誤入力防止)。
- PostMessage モードは前面でなくても届くエンジンがある場合の代替 (要実機確認)。

このモジュールは Windows 以外では import できるが、関数を呼ぶと RuntimeError になる。
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from dataclasses import dataclass

from .image import Image, game_area

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:  # pragma: no cover - Windows 実機でのみ実行
    from ctypes import wintypes as wt

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    try:
        winmm = ctypes.WinDLL("winmm")
    except OSError:
        winmm = None

    ULONG_PTR = ctypes.c_size_t

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wt.LONG),
            ("dy", wt.LONG),
            ("mouseData", wt.DWORD),
            ("dwFlags", wt.DWORD),
            ("time", wt.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wt.DWORD),
            ("biWidth", wt.LONG),
            ("biHeight", wt.LONG),
            ("biPlanes", wt.WORD),
            ("biBitCount", wt.WORD),
            ("biCompression", wt.DWORD),
            ("biSizeImage", wt.DWORD),
            ("biXPelsPerMeter", wt.LONG),
            ("biYPelsPerMeter", wt.LONG),
            ("biClrUsed", wt.DWORD),
            ("biClrImportant", wt.DWORD),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]

    WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    user32.SendInput.argtypes = (wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    user32.SendInput.restype = wt.UINT
    user32.GetWindowTextW.argtypes = (wt.HWND, wt.LPWSTR, ctypes.c_int)
    user32.GetClassNameW.argtypes = (wt.HWND, wt.LPWSTR, ctypes.c_int)
    user32.EnumWindows.argtypes = (WNDENUMPROC, wt.LPARAM)
    user32.GetForegroundWindow.restype = wt.HWND
    user32.GetDC.restype = wt.HDC
    user32.GetDC.argtypes = (wt.HWND,)
    user32.ReleaseDC.argtypes = (wt.HWND, wt.HDC)
    user32.PostMessageW.argtypes = (wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
    user32.PrintWindow.argtypes = (wt.HWND, wt.HDC, wt.UINT)
    user32.GetAsyncKeyState.restype = ctypes.c_short
    gdi32.CreateCompatibleDC.restype = wt.HDC
    gdi32.CreateCompatibleDC.argtypes = (wt.HDC,)
    gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
    gdi32.CreateCompatibleBitmap.argtypes = (wt.HDC, ctypes.c_int, ctypes.c_int)
    gdi32.CreateDIBSection.restype = wt.HBITMAP
    gdi32.CreateDIBSection.argtypes = (wt.HDC, ctypes.POINTER(BITMAPINFO), wt.UINT, ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD)
    gdi32.SelectObject.restype = wt.HGDIOBJ
    gdi32.SelectObject.argtypes = (wt.HDC, wt.HGDIOBJ)
    gdi32.DeleteObject.argtypes = (wt.HGDIOBJ,)
    gdi32.DeleteDC.argtypes = (wt.HDC,)
    gdi32.StretchBlt.argtypes = (wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.DWORD)
    gdi32.SetStretchBltMode.argtypes = (wt.HDC, ctypes.c_int)
    kernel32.OpenProcess.restype = wt.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = (wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD))
    kernel32.CloseHandle.argtypes = (wt.HANDLE,)

INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
SRCCOPY, CAPTUREBLT = 0x00CC0020, 0x40000000
COLORONCOLOR = 3
PW_RENDERFULLCONTENT = 2
VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN = 0x25, 0x26, 0x27, 0x28
VK_RETURN, VK_ESCAPE, VK_SPACE, VK_MENU = 0x0D, 0x1B, 0x20, 0x12
SCAN_TO_VK = {0x4B: VK_LEFT, 0x4D: VK_RIGHT, 0x48: VK_UP, 0x50: VK_DOWN, 0x1C: VK_RETURN, 0x01: VK_ESCAPE, 0x39: VK_SPACE}


def _require() -> None:
    if not IS_WINDOWS:
        raise RuntimeError("この操作は Windows でのみ実行できます (docich.flyhome.win32)")


def set_dpi_aware() -> None:
    """高 DPI 環境で座標が拡大縮小されないようにする (取得座標 = 実ピクセル)。"""
    _require()
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        user32.SetProcessDPIAware()


def precise_timer(on: bool = True) -> None:
    """time.sleep の分解能を 1ms にする (既定 15.6ms だとフレーム間隔がぶれる)。"""
    if IS_WINDOWS and winmm is not None:
        (winmm.timeBeginPeriod if on else winmm.timeEndPeriod)(1)


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    cls: str
    pid: int
    exe: str
    rect: tuple[int, int, int, int]  # クライアント領域 (スクリーン座標 x0, y0, x1, y1)


def _exe_of(pid: int) -> str:
    h = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = wt.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(h)


def client_rect(hwnd: int) -> tuple[int, int, int, int]:
    _require()
    rc = wt.RECT()
    user32.GetClientRect(wt.HWND(hwnd), ctypes.byref(rc))
    pt = wt.POINT(0, 0)
    user32.ClientToScreen(wt.HWND(hwnd), ctypes.byref(pt))
    return pt.x, pt.y, pt.x + rc.right, pt.y + rc.bottom


def list_windows(visible_only: bool = True) -> list[WindowInfo]:
    _require()
    found: list[WindowInfo] = []

    def cb(raw, _lparam):
        h = int(raw) if raw else 0
        hwnd = wt.HWND(h)  # 64bit ハンドルを既定の c_int 変換に通さない
        if visible_only and not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        title = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, title, n + 1)
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        found.append(WindowInfo(h, title.value, cls.value, pid.value, _exe_of(pid.value), client_rect(h)))
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found


def find_window(titles: tuple[str, ...], exe_names: tuple[str, ...] = ()) -> WindowInfo | None:
    """タイトルの部分一致 (大文字小文字無視) か実行ファイル名で探す。"""
    wins = list_windows()
    lowered = [t.lower() for t in titles]
    exes = [e.lower() for e in exe_names]
    for w in wins:
        if exes and os.path.basename(w.exe).lower() in exes:
            return w
    for w in wins:
        if any(t in w.title.lower() for t in lowered) and "steam" not in os.path.basename(w.exe).lower():
            return w
    return None


def foreground_hwnd() -> int:
    _require()
    h = user32.GetForegroundWindow()
    return int(h) if h else 0


def _send_inputs(items: list[tuple[int, int, int]]) -> None:
    """(vk, scan, flags) の列を 1 回の SendInput で送る。"""
    arr = (INPUT * len(items))()
    for i, (vk, scan, flags) in enumerate(items):
        arr[i].type = INPUT_KEYBOARD
        arr[i].ki = KEYBDINPUT(vk, scan, flags, 0, 0)
    sent = user32.SendInput(len(items), arr, ctypes.sizeof(INPUT))
    if sent != len(items):
        raise OSError(ctypes.get_last_error(), "SendInput was blocked (UIPI: 管理者権限で動くゲームには同じ権限で実行する)")


def bring_to_front(hwnd: int) -> bool:
    """前面化。Windows の前面化制限を Alt キーの空打ちで回避する定番手法。"""
    _require()
    h = wt.HWND(hwnd)
    if user32.IsIconic(h):
        user32.ShowWindow(h, 9)  # SW_RESTORE
    _send_inputs([(VK_MENU, 0, 0), (VK_MENU, 0, KEYEVENTF_KEYUP)])
    user32.SetForegroundWindow(h)
    time.sleep(0.05)
    return foreground_hwnd() == hwnd


def key_down(pressed_vk: int) -> bool:
    _require()
    return bool(user32.GetAsyncKeyState(pressed_vk) & 0x8000)


class Keyboard:
    """押しっぱなし状態を管理し、変化分だけ送る。終了時は必ず release_all() する。"""

    def __init__(self, hwnd: int, extended_scans: set[int], mode: str = "sendinput"):
        _require()
        self.hwnd = hwnd
        self.extended = extended_scans
        self.mode = mode
        self.held: set[int] = set()

    def _event(self, scan: int, down: bool) -> None:
        ext = scan in self.extended
        if self.mode == "postmessage":
            vk = SCAN_TO_VK.get(scan, 0)
            lparam = 1 | (scan << 16) | ((1 << 24) if ext else 0)
            if not down:
                lparam |= 0xC0000000
            user32.PostMessageW(wt.HWND(self.hwnd), WM_KEYDOWN if down else WM_KEYUP, vk, lparam)
            return
        flags = KEYEVENTF_SCANCODE | (KEYEVENTF_EXTENDEDKEY if ext else 0) | (0 if down else KEYEVENTF_KEYUP)
        _send_inputs([(0, scan, flags)])

    def set(self, scan: int, down: bool) -> None:
        if down and scan not in self.held:
            self._event(scan, True)
            self.held.add(scan)
        elif not down and scan in self.held:
            self._event(scan, False)
            self.held.discard(scan)

    def tap(self, scan: int, hold_s: float = 0.06) -> None:
        self.set(scan, True)
        time.sleep(hold_s)
        self.set(scan, False)

    def release_all(self) -> None:
        for scan in list(self.held):
            try:
                self._event(scan, False)
            finally:
                self.held.discard(scan)


class Capturer:
    """ゲーム描画領域を (out_w, out_h) の RGB で取得する。DIB は使い回す。"""

    def __init__(self, hwnd: int, out_w: int, out_h: int, *, aspect=(16, 9), mode: str = "screen"):
        _require()
        self.hwnd = hwnd
        self.out_w, self.out_h = out_w, out_h
        self.aspect = aspect
        self.mode = mode
        self._screen_dc = user32.GetDC(None)
        self._mem_dc = gdi32.CreateCompatibleDC(self._screen_dc)
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = out_w
        bmi.bmiHeader.biHeight = -out_h  # top-down
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        self._bits = ctypes.c_void_p()
        self._dib = gdi32.CreateDIBSection(self._mem_dc, ctypes.byref(bmi), 0, ctypes.byref(self._bits), None, 0)
        if not self._dib:
            raise OSError(ctypes.get_last_error(), "CreateDIBSection failed")
        self._old = gdi32.SelectObject(self._mem_dc, self._dib)
        gdi32.SetStretchBltMode(self._mem_dc, COLORONCOLOR)

    def source_rect(self) -> tuple[int, int, int, int]:
        """ゲーム描画領域 (黒帯を除く) のスクリーン座標。"""
        x0, y0, x1, y1 = client_rect(self.hwnd)
        gx0, gy0, gx1, gy1 = game_area(x1 - x0, y1 - y0, self.aspect)
        return x0 + gx0, y0 + gy0, x0 + gx1, y0 + gy1

    def grab(self) -> Image:
        x0, y0, x1, y1 = self.source_rect()
        w, h = x1 - x0, y1 - y0
        if w <= 0 or h <= 0:
            raise RuntimeError("game window has no client area (minimized?)")
        if self.mode == "printwindow":
            self._grab_printwindow(x0, y0, w, h)
        else:
            gdi32.StretchBlt(self._mem_dc, 0, 0, self.out_w, self.out_h, self._screen_dc, x0, y0, w, h, SRCCOPY | CAPTUREBLT)
        buf = ctypes.string_at(self._bits, self.out_w * self.out_h * 4)
        return Image.from_bgra(self.out_w, self.out_h, buf)

    def _grab_printwindow(self, gx0: int, gy0: int, w: int, h: int) -> None:
        # ウィンドウ全体を一時ビットマップへ描かせ、そこからゲーム領域を縮小コピーする。
        rc = wt.RECT()
        user32.GetWindowRect(wt.HWND(self.hwnd), ctypes.byref(rc))
        ww, wh = rc.right - rc.left, rc.bottom - rc.top
        tmp_dc = gdi32.CreateCompatibleDC(self._screen_dc)
        bmp = gdi32.CreateCompatibleBitmap(self._screen_dc, ww, wh)
        old = gdi32.SelectObject(tmp_dc, bmp)
        try:
            user32.PrintWindow(wt.HWND(self.hwnd), tmp_dc, PW_RENDERFULLCONTENT)
            gdi32.StretchBlt(self._mem_dc, 0, 0, self.out_w, self.out_h, tmp_dc, gx0 - rc.left, gy0 - rc.top, w, h, SRCCOPY)
        finally:
            gdi32.SelectObject(tmp_dc, old)
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(tmp_dc)

    def close(self) -> None:
        if getattr(self, "_mem_dc", None):
            gdi32.SelectObject(self._mem_dc, self._old)
            gdi32.DeleteObject(self._dib)
            gdi32.DeleteDC(self._mem_dc)
            user32.ReleaseDC(None, self._screen_dc)
            self._mem_dc = None


def grab_full(hwnd: int) -> Image:
    """クライアント領域を等倍で取得する (スクリーンショット保存用)。"""
    x0, y0, x1, y1 = client_rect(hwnd)
    w, h = x1 - x0, y1 - y0
    cap = Capturer(hwnd, w, h, aspect=(w, h))  # aspect をクライアントと同じにして黒帯除去を無効化
    try:
        return cap.grab()
    finally:
        cap.close()
