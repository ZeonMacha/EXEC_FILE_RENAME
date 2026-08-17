# -*- coding: utf-8 -*-
"""EXEC_FILE_RENAME — batch filename replacer (left = current names, right = new names)."""

from __future__ import annotations

import os
import re
import sys
import uuid
from pathlib import Path
import ctypes
from ctypes import wintypes
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

WM_DROPFILES = 0x0233
WM_NULL = 0x0000
WH_GETMESSAGE = 3
PM_REMOVE = 1
GWL_EXSTYLE = -20
WS_EX_ACCEPTFILES = 0x00000010
INVALID_CHARS = '<>:"/\\|?*'
EXT_RE = re.compile(r"\.[A-Za-z0-9]{1,5}$")
MAX_ITEMS = 500
NUMBER_IN_PATTERN = re.compile(r"(\d+)")
SERIAL_PRESETS = (
    ("01. ", "01. "),
    ("01-", "01-"),
    ("01_", "01_"),
    ("001", "001"),
)

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    _TKDND = True
except Exception:
    DND_FILES = "DND_Files"
    TkinterDnD = None
    _TKDND = False


def normalize_drop_path(raw: str) -> str:
    path = raw.strip().strip("{}")
    if path.lower().startswith("file:"):
        from urllib.parse import unquote, urlparse
        parsed = urlparse(path)
        path = unquote(parsed.path or path)
        if re.match(r"^/[A-Za-z]:", path):
            path = path[1:]
        path = path.replace("/", "\\")
    return path


def query_dropped_files(hdrop) -> list[str]:
    shell32 = ctypes.windll.shell32
    count = shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
    paths: list[str] = []
    for index in range(count):
        length = shell32.DragQueryFileW(hdrop, index, None, 0)
        buffer = ctypes.create_unicode_buffer(length + 1)
        shell32.DragQueryFileW(hdrop, index, buffer, length + 1)
        if buffer.value:
            paths.append(buffer.value)
    shell32.DragFinish(hdrop)
    return paths


def hook_windows_file_drop(widget: tk.Misc, callback) -> object | None:
    if os.name != "nt":
        return None

    user32 = ctypes.windll.user32
    shell32 = ctypes.windll.shell32
    is_64 = ctypes.sizeof(ctypes.c_void_p) == 8
    LRESULT = ctypes.c_int64 if is_64 else ctypes.c_long
    get_long = user32.GetWindowLongPtrW if is_64 else user32.GetWindowLongW
    set_long = user32.SetWindowLongPtrW if is_64 else user32.SetWindowLongW
    get_long.restype = ctypes.c_void_p
    set_long.restype = ctypes.c_void_p
    HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    class POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    class MSG(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("message", wintypes.UINT),
            ("wParam", wintypes.WPARAM),
            ("lParam", wintypes.LPARAM),
            ("time", wintypes.DWORD),
            ("pt", POINT),
        ]

    hook = {"id": None, "proc": None, "enum": None}

    def toplevel_hwnd() -> int:
        try:
            frame = widget.wm_frame()
            if frame:
                return int(str(frame), 16)
        except Exception:
            pass
        hwnd = widget.winfo_id()
        parent = user32.GetParent(hwnd)
        return parent or hwnd

    def enable_accept(hwnd: int) -> None:
        if not hwnd:
            return
        try:
            shell32.DragAcceptFiles(hwnd, True)
            style = get_long(hwnd, GWL_EXSTYLE) or 0
            set_long(hwnd, GWL_EXSTYLE, style | WS_EX_ACCEPTFILES)
        except Exception:
            pass

    def enable_tree() -> None:
        root_hwnd = toplevel_hwnd()
        enable_accept(root_hwnd)
        enable_accept(widget.winfo_id())

        def enum_cb(hwnd, _lparam):
            enable_accept(hwnd)
            return True

        hook["enum"] = WNDENUMPROC(enum_cb)
        user32.EnumChildWindows(root_hwnd, hook["enum"], 0)

    @HOOKPROC
    def getmsg_proc(ncode, wparam, lparam):
        if ncode >= 0 and int(wparam) == PM_REMOVE:
            msg = ctypes.cast(lparam, ctypes.POINTER(MSG)).contents
            if msg.message == WM_DROPFILES:
                try:
                    paths = query_dropped_files(msg.wParam)
                    widget.after(0, lambda p=paths: callback(p))
                except Exception:
                    try:
                        shell32.DragFinish(msg.wParam)
                    except Exception:
                        pass
                msg.message = WM_NULL
        return user32.CallNextHookEx(hook["id"], ncode, wparam, lparam)

    def attach() -> None:
        enable_tree()
        hook["proc"] = getmsg_proc
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        hook["id"] = user32.SetWindowsHookExW(
            WH_GETMESSAGE,
            hook["proc"],
            None,
            ctypes.windll.kernel32.GetCurrentThreadId(),
        )
        widget.after(400, enable_tree)

    widget.after_idle(attach)
    return hook


def collect_files(paths: list[str]) -> list[str]:
    found: list[str] = []
    for raw in paths:
        item = Path(raw)
        if item.is_dir():
            found.extend(str(p) for p in sorted(item.iterdir()) if p.is_file())
        elif item.is_file():
            found.append(str(item))
    return found


def ask_open_filenames(parent: tk.Misc, initialdir: str) -> list[str]:
    """Windows multi-select with a large buffer so 500 files are not truncated."""
    if os.name != "nt":
        return list(filedialog.askopenfilenames(title="EXEC_FILE_RENAME — 选择文件", initialdir=initialdir) or [])

    OFN_EXPLORER = 0x00080000
    OFN_ALLOWMULTISELECT = 0x00000200
    OFN_FILEMUSTEXIST = 0x00001000
    OFN_HIDEREADONLY = 0x00000004
    OFN_PATHMUSTEXIST = 0x00000800

    class OPENFILENAMEW(ctypes.Structure):
        _fields_ = [
            ("lStructSize", wintypes.DWORD),
            ("hwndOwner", wintypes.HWND),
            ("hInstance", wintypes.HINSTANCE),
            ("lpstrFilter", wintypes.LPCWSTR),
            ("lpstrCustomFilter", wintypes.LPWSTR),
            ("nMaxCustFilter", wintypes.DWORD),
            ("nFilterIndex", wintypes.DWORD),
            ("lpstrFile", wintypes.LPWSTR),
            ("nMaxFile", wintypes.DWORD),
            ("lpstrFileTitle", wintypes.LPWSTR),
            ("nMaxFileTitle", wintypes.DWORD),
            ("lpstrInitialDir", wintypes.LPCWSTR),
            ("lpstrTitle", wintypes.LPCWSTR),
            ("Flags", wintypes.DWORD),
            ("nFileOffset", wintypes.WORD),
            ("nFileExtension", wintypes.WORD),
            ("lpstrDefExt", wintypes.LPCWSTR),
            ("lCustData", wintypes.LPARAM),
            ("lpfnHook", ctypes.c_void_p),
            ("lpTemplateName", wintypes.LPCWSTR),
            ("pvReserved", ctypes.c_void_p),
            ("dwReserved", wintypes.DWORD),
            ("FlagsEx", wintypes.DWORD),
        ]

    buffer_chars = 256 * 1024
    file_buf = ctypes.create_unicode_buffer(buffer_chars)
    filter_buf = ctypes.create_unicode_buffer("所有文件\0*.*\0")
    title_buf = ctypes.create_unicode_buffer(f"EXEC_FILE_RENAME — 选择文件（最多 {MAX_ITEMS} 个）")
    dir_buf = ctypes.create_unicode_buffer(initialdir)

    ofn = OPENFILENAMEW()
    ofn.lStructSize = ctypes.sizeof(OPENFILENAMEW)
    try:
        frame = parent.winfo_toplevel().wm_frame()
        ofn.hwndOwner = int(str(frame), 16) if frame else parent.winfo_id()
    except Exception:
        ofn.hwndOwner = 0
    ofn.lpstrFilter = filter_buf
    ofn.nFilterIndex = 1
    ofn.lpstrFile = file_buf
    ofn.nMaxFile = buffer_chars
    ofn.lpstrInitialDir = dir_buf
    ofn.lpstrTitle = title_buf
    ofn.Flags = (
        OFN_EXPLORER
        | OFN_ALLOWMULTISELECT
        | OFN_FILEMUSTEXIST
        | OFN_HIDEREADONLY
        | OFN_PATHMUSTEXIST
    )

    if not ctypes.windll.comdlg32.GetOpenFileNameW(ctypes.byref(ofn)):
        return []
    parts = [part for part in file_buf[:].split("\0") if part]
    if not parts:
        return []
    if len(parts) == 1:
        return parts
    folder = Path(parts[0])
    return [str(folder / name) for name in parts[1:]]


def text_lines(widget: tk.Text, strip_trailing_blank: bool = True) -> list[str]:
    raw = widget.get("1.0", "end-1c")
    lines = raw.split("\n")
    if strip_trailing_blank:
        while lines and lines[-1].strip() == "":
            lines.pop()
    return lines


def validate_filename(name: str) -> str | None:
    if not name:
        return "文件名为空"
    if name in {".", ".."}:
        return "文件名非法"
    if any(ch in INVALID_CHARS or ord(ch) < 32 for ch in name):
        return "含有非法字符 \\ / : * ? \" < > |"
    if name.endswith(" ") or name.endswith("."):
        return "不能以空格或点结尾"
    if len(name) > 240:
        return "文件名过长"
    return None


def apply_extension(new_name: str, original: Path, keep_ext: bool) -> str:
    if not keep_ext:
        return new_name
    if EXT_RE.search(new_name):
        return new_name
    suffix = original.suffix
    if suffix and not new_name.lower().endswith(suffix.lower()):
        return new_name + suffix
    return new_name


def format_serial(n: int, example: str) -> str:
    """Build a prefix from an example like '01. ' or '01-'."""
    example = example or ""
    match = NUMBER_IN_PATTERN.search(example)
    if not match:
        return example
    width = len(match.group(1))
    return example[: match.start()] + str(n).zfill(width) + example[match.end() :]


def apply_serial_prefix(name: str, n: int, example: str) -> str:
    prefix = format_serial(n, example)
    if not prefix:
        return name
    if name.startswith(prefix):
        return name
    return prefix + name


def compose_new_name(base: str, n: int, pattern: str, extra: str) -> str:
    """Combine extra prefix + serial (01. / 001 / custom) + optional right-side name."""
    extra = extra or ""
    numbered = apply_serial_prefix(base, n, pattern) if pattern else base
    if extra and not numbered.startswith(extra):
        numbered = extra + numbered
    if not base.strip():
        numbered = numbered.rstrip(" .")
    return numbered


_BaseApp = TkinterDnD.Tk if _TKDND else tk.Tk


class App(_BaseApp):
    def __init__(self) -> None:
        super().__init__()
        self.title("EXEC_FILE_RENAME")
        self.geometry("1100x680")
        self.minsize(780, 480)

        self.files: list[Path] = []
        self.last_dir = str(Path.home())
        self.undo_pairs: list[tuple[Path, Path]] = []
        self.keep_ext = tk.BooleanVar(value=True)
        self.use_format = tk.BooleanVar(value=False)
        self.use_prefix = tk.BooleanVar(value=False)
        self.serial_choice = tk.StringVar(value="01. ")
        self.serial_extra = tk.StringVar(value="")
        self.serial_start = tk.StringVar(value="1")
        self.serial_hint = tk.StringVar()
        self.status_var = tk.StringVar(value="选择文件后，在右侧大框按行填写新文件名")
        self.count_var = tk.StringVar(value=f"左侧 0/{MAX_ITEMS} 个  ·  右侧 0 行")
        self._syncing_scroll = False
        self._ln_count = 0
        self._status_job = None
        self._drop_hook = None

        self._build()
        self._enable_file_drop()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after_idle(self._on_serial_change)

    def _build(self) -> None:
        ui_font = ("Microsoft YaHei UI", 10)
        text_font = ("Microsoft YaHei UI", 11)
        self.option_add("*Font", ui_font)

        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)
        self.bind_all("<MouseWheel>", self._on_global_mousewheel)
        self.bind_all("<Button-4>", self._on_global_mousewheel)
        self.bind_all("<Button-5>", self._on_global_mousewheel)

        hint = ttk.Label(
            root,
            text=f"EXEC_FILE_RENAME：左边当前文件名，右边可留空。格式（01. / 001）和自定义前缀是两个独立选项，可单独用也可一起用。一次最多 {MAX_ITEMS} 条。",
        )
        hint.pack(anchor="w", pady=(0, 8))

        toolbar = ttk.Frame(root)
        toolbar.pack(fill="x", pady=(0, 8))
        ttk.Button(toolbar, text="添加文件", command=self.add_files).pack(side="left")
        ttk.Button(toolbar, text="添加文件夹", command=self.add_folder).pack(side="left", padx=(6, 0))
        ttk.Button(toolbar, text="清空", command=self.clear_all).pack(side="left", padx=(6, 0))
        ttk.Button(toolbar, text="右侧填入当前名", command=self.fill_right_from_left).pack(side="left", padx=(16, 0))
        ttk.Button(toolbar, text="复制左侧", command=self.copy_left).pack(side="left", padx=(6, 0))
        ttk.Checkbutton(
            toolbar,
            text="新名无扩展名时保留原扩展名",
            variable=self.keep_ext,
            command=self._refresh_status,
        ).pack(side="left", padx=(16, 0))

        serial = ttk.LabelFrame(root, text="可选功能（可单独勾选，也可同时勾选）", padding=6)
        serial.pack(fill="x", pady=(0, 8))

        row_fmt = ttk.Frame(serial)
        row_fmt.pack(fill="x")
        ttk.Checkbutton(
            row_fmt,
            text="格式",
            variable=self.use_format,
            command=self._on_serial_change,
        ).pack(side="left")
        self.serial_combo = ttk.Combobox(
            row_fmt,
            textvariable=self.serial_choice,
            values=[label for label, _pattern in SERIAL_PRESETS],
            state="readonly",
            width=8,
        )
        self.serial_combo.pack(side="left", padx=(8, 0))
        self.serial_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_serial_change())
        ttk.Label(row_fmt, text="起始").pack(side="left", padx=(12, 4))
        self.serial_start_spin = ttk.Spinbox(
            row_fmt,
            from_=0,
            to=9999,
            textvariable=self.serial_start,
            width=5,
            command=self._on_serial_change,
        )
        self.serial_start_spin.pack(side="left")
        ttk.Label(row_fmt, text="例如 01.  01-  001").pack(side="left", padx=(10, 0))

        row_pre = ttk.Frame(serial)
        row_pre.pack(fill="x", pady=(6, 0))
        ttk.Checkbutton(
            row_pre,
            text="自定义前缀",
            variable=self.use_prefix,
            command=self._on_serial_change,
        ).pack(side="left")
        self.serial_extra_entry = ttk.Entry(row_pre, textvariable=self.serial_extra, width=16)
        self.serial_extra_entry.pack(side="left", padx=(8, 0))
        ttk.Button(row_pre, text="写入右侧", command=self.write_serial_to_right).pack(side="left", padx=(12, 0))
        ttk.Label(row_pre, textvariable=self.serial_hint, foreground="#555").pack(side="left", padx=(12, 0))

        footer = ttk.Frame(root)
        footer.pack(side="bottom", fill="x")
        actions = ttk.Frame(footer)
        actions.pack(fill="x", pady=(10, 6))
        ttk.Label(actions, textvariable=self.count_var).pack(side="left")
        ttk.Button(actions, text="撤销上次替换", command=self.undo_last).pack(side="right")
        ttk.Button(actions, text="预览对应关系", command=self.preview).pack(side="right", padx=(0, 8))
        ttk.Button(actions, text="一键替换", command=self.rename_now).pack(side="right", padx=(0, 8))
        ttk.Label(footer, textvariable=self.status_var, foreground="#333").pack(anchor="w")
        log_frame = ttk.LabelFrame(footer, text="结果", padding=4)
        log_frame.pack(fill="x", pady=(8, 0))
        self.log_frame = log_frame
        self.log = tk.Text(log_frame, height=4, wrap="word", font=("Microsoft YaHei UI", 9), state="disabled")
        self.log_scroll = tk.Scrollbar(log_frame, orient="vertical", width=16, command=self.log.yview)
        self.log.configure(yscrollcommand=self.log_scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        self.log_scroll.pack(side="right", fill="y")

        panes = ttk.Frame(root)
        panes.pack(fill="both", expand=True)
        panes.columnconfigure(0, weight=1)
        panes.columnconfigure(1, weight=1)
        panes.rowconfigure(0, weight=1)

        left_box = ttk.LabelFrame(panes, text="当前文件名", padding=6)
        left_box.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        right_box = ttk.LabelFrame(panes, text="新文件名（每行一个，整列粘贴即可）", padding=6)
        right_box.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        self.left_box = left_box
        self.right_box = right_box

        self.left_ln, self.left, self.left_scroll, self.left_xscroll = self._make_text_pane(
            left_box, text_font, readonly=True
        )
        self.right_ln, self.right, self.right_scroll, self.right_xscroll = self._make_text_pane(
            right_box, text_font, readonly=False
        )

        self.left.configure(yscrollcommand=lambda *a: self._on_text_scroll("left", *a))
        self.right.configure(yscrollcommand=lambda *a: self._on_text_scroll("right", *a))
        self.left_scroll.configure(command=self._yview_pair)
        self.right_scroll.configure(command=self._yview_pair)

        self.right.bind("<<Modified>>", self._on_right_modified)
        self.right.bind("<KeyRelease>", self._on_right_key)
        self.serial_extra.trace_add("write", lambda *_: self._on_serial_change())
        self.serial_start.trace_add("write", lambda *_: self._on_serial_change())

    def _on_close(self) -> None:
        try:
            self.unbind_all("<MouseWheel>")
            self.unbind_all("<Button-4>")
            self.unbind_all("<Button-5>")
        except Exception:
            pass
        self.destroy()

    def _on_right_key(self, _event=None) -> None:
        if self._status_job:
            self.after_cancel(self._status_job)
        self._status_job = self.after(150, self._refresh_status)
        self._refresh_linenumbers()

    def _wheel_steps(self, event) -> int:
        delta = int(getattr(event, "delta", 0) or 0)
        if delta == 0:
            num = getattr(event, "num", 0)
            if num == 4:
                return -1
            if num == 5:
                return 1
            return 0
        steps = int(-delta / 120)
        if steps == 0:
            steps = -1 if delta > 0 else 1
        return steps

    def _xy_in_widget(self, x: int, y: int, widget) -> bool:
        if widget is None:
            return False
        try:
            x0 = widget.winfo_rootx()
            y0 = widget.winfo_rooty()
            return x0 <= x < x0 + widget.winfo_width() and y0 <= y < y0 + widget.winfo_height()
        except Exception:
            return False

    def _on_global_mousewheel(self, event):
        try:
            x, y = self.winfo_pointerxy()
        except Exception:
            return
        if not self._xy_in_widget(x, y, self):
            return
        steps = self._wheel_steps(event)
        if not steps:
            return "break"
        if self._xy_in_widget(x, y, getattr(self, "left_box", None)) or self._xy_in_widget(
            x, y, getattr(self, "right_box", None)
        ):
            self._scroll_name_boxes(steps)
            return "break"
        if self._xy_in_widget(x, y, getattr(self, "log_frame", None)):
            self.log.yview_scroll(steps, "units")
            return "break"
        self._scroll_name_boxes(steps)
        return "break"

    def _scroll_name_boxes(self, steps: int) -> None:
        self.left.yview_scroll(steps, "units")
        self.right.yview_scroll(steps, "units")
        self.left_ln.yview_scroll(steps, "units")
        self.right_ln.yview_scroll(steps, "units")

    def _make_text_pane(self, parent, font, readonly: bool):
        pane = ttk.Frame(parent)
        pane.pack(fill="both", expand=True)
        pane.columnconfigure(1, weight=1)
        pane.rowconfigure(0, weight=1)
        pane.columnconfigure(2, minsize=18)
        pane.rowconfigure(1, minsize=18)

        linenum = tk.Text(
            pane,
            width=5,
            font=font,
            padx=4,
            pady=6,
            wrap="none",
            takefocus=0,
            relief="flat",
            background="#f0f0f0",
            foreground="#888",
            state="disabled",
        )
        text = tk.Text(
            pane,
            font=font,
            padx=8,
            pady=6,
            wrap="none",
            undo=not readonly,
            relief="solid",
            borderwidth=1,
            height=8,
            width=24,
        )
        yscroll = tk.Scrollbar(pane, orient="vertical", width=18)
        xscroll = tk.Scrollbar(pane, orient="horizontal", width=18)
        text.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        yscroll.configure(command=text.yview)
        xscroll.configure(command=text.xview)
        linenum.grid(row=0, column=0, sticky="ns")
        text.grid(row=0, column=1, sticky="nsew")
        yscroll.grid(row=0, column=2, rowspan=2, sticky="ns")
        xscroll.grid(row=1, column=0, columnspan=2, sticky="ew")
        if readonly:
            text.configure(state="disabled", background="#f7f7f7")
        return linenum, text, yscroll, xscroll

    def _set_text(self, widget: tk.Text, content: str, readonly: bool) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        if content:
            widget.insert("1.0", content)
        if readonly:
            widget.configure(state="disabled")

    def _line_count(self, widget: tk.Text) -> int:
        return int(widget.index("end-1c").split(".")[0])

    def _refresh_linenumbers(self) -> None:
        count = max(self._line_count(self.left), self._line_count(self.right), len(self.files), 1)
        if count == self._ln_count:
            return
        pos = self.left.yview()[0]
        self._ln_count = count
        numbers = "\n".join(f"{i:>4}" for i in range(1, count + 1))
        for ln in (self.left_ln, self.right_ln):
            self._set_text(ln, numbers, readonly=True)
            ln.yview_moveto(pos)

    def _on_text_scroll(self, side: str, first, last) -> None:
        self.left_scroll.set(first, last)
        self.right_scroll.set(first, last)
        if self._syncing_scroll:
            return
        self._syncing_scroll = True
        try:
            other = self.right if side == "left" else self.left
            other.yview_moveto(first)
            self.left_ln.yview_moveto(first)
            self.right_ln.yview_moveto(first)
        finally:
            self._syncing_scroll = False

    def _yview_pair(self, *args) -> None:
        self.left.yview(*args)
        self.right.yview(*args)
        self.left_ln.yview(*args)
        self.right_ln.yview(*args)

    def _on_right_modified(self, _event=None) -> None:
        self.right.edit_modified(False)
        self._refresh_linenumbers()
        self._refresh_status()

    def _enable_file_drop(self) -> None:
        if _TKDND:
            self._register_tkdnd(self)
            return
        self._drop_hook = hook_windows_file_drop(self, self._on_drop)

    def _register_tkdnd(self, widget) -> None:
        try:
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", self._on_tkdnd)
        except Exception:
            pass
        for child in widget.winfo_children():
            self._register_tkdnd(child)

    def _on_tkdnd(self, event) -> str:
        data = getattr(event, "data", "") or ""
        try:
            paths = [p.strip().strip("{}") for p in self.tk.splitlist(data) if p]
        except tk.TclError:
            paths = [data.strip().strip("{}")] if data.strip() else []
        self._on_drop(paths)
        return "copy"

    def _on_drop(self, paths: list[str]) -> None:
        cleaned = [normalize_drop_path(p) for p in paths if p]
        files = collect_files(cleaned)
        self._append_files(files)

    def add_files(self) -> None:
        paths = ask_open_filenames(self, self.last_dir)
        if paths:
            self.last_dir = str(Path(paths[0]).parent)
            self._append_files(list(paths))

    def add_folder(self) -> None:
        folder = filedialog.askdirectory(title="EXEC_FILE_RENAME — 选择文件夹（仅添加该层文件）", initialdir=self.last_dir)
        if not folder:
            return
        self.last_dir = folder
        files = [str(p) for p in sorted(Path(folder).iterdir()) if p.is_file()]
        self._append_files(files)

    def _append_files(self, paths: list[str]) -> None:
        room = MAX_ITEMS - len(self.files)
        if room <= 0:
            messagebox.showwarning("已满", f"一次最多 {MAX_ITEMS} 条，请先清空或先完成当前替换。")
            return
        existing = {p.resolve() for p in self.files}
        added: list[Path] = []
        overflow = 0
        for raw in paths:
            item = Path(raw)
            if not item.is_file():
                continue
            key = item.resolve()
            if key in existing:
                continue
            if len(added) >= room:
                overflow += 1
                continue
            existing.add(key)
            added.append(item)
        if not added:
            self.status_var.set(f"没有新文件可添加（可能已在列表中，或已满 {MAX_ITEMS} 条）")
            return
        self.files.extend(added)
        self._reload_left()
        self._refresh_linenumbers()
        self._refresh_status()
        self._log(f"已添加 {len(added)} 个文件，当前共 {len(self.files)}/{MAX_ITEMS} 个")
        if overflow:
            messagebox.showwarning(
                "超出上限",
                f"一次最多 {MAX_ITEMS} 条，已加入 {len(added)} 个，另有 {overflow} 个未加入。",
            )

    def _reload_left(self) -> None:
        names = "\n".join(p.name for p in self.files)
        self._set_text(self.left, names, readonly=True)

    def clear_all(self) -> None:
        if self.files and not messagebox.askyesno("清空", "清空左右两侧列表？"):
            return
        self.files.clear()
        self._reload_left()
        self._set_text(self.right, "", readonly=False)
        self._refresh_linenumbers()
        self._refresh_status()
        self.status_var.set("已清空")

    def fill_right_from_left(self) -> None:
        names = "\n".join(p.name for p in self.files)
        self._set_text(self.right, names, readonly=False)
        self._refresh_linenumbers()
        self._refresh_status()

    def copy_left(self) -> None:
        names = "\n".join(p.name for p in self.files)
        self.clipboard_clear()
        self.clipboard_append(names)
        self.status_var.set("已复制左侧文件名，可粘贴到记事本或表格里改")

    def _serial_start_n(self) -> int:
        try:
            return int(self.serial_start.get().strip() or "1")
        except ValueError:
            return 1

    def _serial_pattern(self) -> str:
        if not self.use_format.get():
            return ""
        choice = self.serial_choice.get()
        for label, pattern in SERIAL_PRESETS:
            if label == choice:
                return pattern
        return choice

    def _serial_extra(self) -> str:
        if not self.use_prefix.get():
            return ""
        return self.serial_extra.get().strip()

    def _can_autoname(self) -> bool:
        return bool(self._serial_pattern() or self._serial_extra())

    def _on_serial_change(self, *_args) -> None:
        fmt_on = self.use_format.get()
        pre_on = self.use_prefix.get()
        self.serial_combo.configure(state="readonly" if fmt_on else "disabled")
        self.serial_start_spin.configure(state="normal" if fmt_on else "disabled")
        self.serial_extra_entry.configure(state="normal" if pre_on else "disabled")
        pattern = self._serial_pattern()
        extra = self._serial_extra()
        if not fmt_on and not pre_on:
            self.serial_hint.set("未勾选时不加序号、不加前缀")
        elif pre_on and not extra:
            self.serial_hint.set("已勾选自定义前缀，请填写前缀内容，例如 封面_")
        else:
            start = self._serial_start_n()
            named = compose_new_name("歌名", start, pattern, extra)
            empty = compose_new_name("", start, pattern, extra)
            empty2 = compose_new_name("", start + 1, pattern, extra)
            self.serial_hint.set(f"有名：{named}.mp3　留空：{empty}.mp3 、 {empty2}.mp3")
        if self._status_job:
            self.after_cancel(self._status_job)
        self._status_job = self.after(150, self._refresh_status)

    def write_serial_to_right(self) -> None:
        if not self.use_format.get() and not self.use_prefix.get():
            messagebox.showinfo("可选功能", "请先勾选「格式」或「自定义前缀」。")
            return
        pattern = self._serial_pattern()
        extra = self._serial_extra()
        if self.use_prefix.get() and not extra:
            messagebox.showwarning("前缀为空", "已勾选「自定义前缀」，请填写前缀内容。")
            return
        if not pattern and not extra:
            messagebox.showwarning("没有可写入的内容", "请选择一种格式，或填写自定义前缀。")
            return
        names = self._new_names()
        if not names:
            if not self.files:
                messagebox.showwarning("没有文件", "请先在左侧添加文件。右侧可以留空，将按格式/前缀生成。")
                return
            names = [""] * len(self.files)
        start = self._serial_start_n()
        numbered = [
            compose_new_name(name, start + index, pattern, extra)
            for index, name in enumerate(names)
        ]
        self._set_text(self.right, "\n".join(numbered), readonly=False)
        self._refresh_linenumbers()
        self._refresh_status()
        self._log(f"已按勾选的格式/前缀写入右侧 {len(numbered)} 行")

    def _new_names(self) -> list[str]:
        names: list[str] = []
        for line in text_lines(self.right):
            name = line.strip().strip('"')
            if "/" in name or "\\" in name:
                name = Path(name).name
            names.append(name)
        return names

    def _refresh_status(self) -> None:
        left_n = len(self.files)
        right_n = len(self._new_names())
        if left_n and right_n == 0 and self._can_autoname():
            self.count_var.set(f"左侧 {left_n}/{MAX_ITEMS} 个  ·  右侧未命名  ·  将自动按序号生成")
        elif left_n == right_n:
            self.count_var.set(f"左侧 {left_n}/{MAX_ITEMS} 个  ·  右侧 {right_n} 行  ·  行数一致")
        else:
            self.count_var.set(f"左侧 {left_n}/{MAX_ITEMS} 个  ·  右侧 {right_n} 行  ·  行数不一致")
        dirs = {str(p.parent) for p in self.files}
        if len(dirs) == 1:
            folder = next(iter(dirs))
            self.status_var.set(f"目录：{folder}")
        elif dirs:
            self.status_var.set(f"来自 {len(dirs)} 个目录")
        else:
            self.status_var.set("选择文件后，在右侧大框按行填写新文件名")

    def _build_plan(self) -> tuple[list[tuple[Path, Path]], list[str]]:
        errors: list[str] = []
        names = self._new_names()
        if not self.files:
            errors.append("还没有选择文件")
            return [], errors
        if len(self.files) > MAX_ITEMS:
            errors.append(f"超过上限：当前 {len(self.files)} 个，最多 {MAX_ITEMS} 条")
            return [], errors
        pattern = self._serial_pattern()
        extra = self._serial_extra()
        if not names and self._can_autoname():
            names = [""] * len(self.files)
        elif len(names) != len(self.files):
            if not names:
                errors.append("右侧未命名：请填写新文件名，或勾选「格式」/「自定义前缀」自动生成")
            else:
                errors.append(f"行数不一致：左边 {len(self.files)} 个文件，右边 {len(names)} 行")
            return [], errors

        plan: list[tuple[Path, Path]] = []
        seen: dict[str, int] = {}
        keep_ext = self.keep_ext.get()
        start = self._serial_start_n()
        for index, (src, raw_name) in enumerate(zip(self.files, names), start=1):
            numbered = compose_new_name(raw_name, start + index - 1, pattern, extra)
            new_name = apply_extension(numbered, src, keep_ext)
            reason = validate_filename(new_name)
            if reason:
                errors.append(f"第 {index} 行：{reason}  →  {new_name or '（空）'}")
                continue
            dest = src.with_name(new_name)
            key = str(dest.resolve()).lower()
            if key in seen:
                errors.append(f"第 {index} 行与第 {seen[key]} 行重名：{new_name}")
            else:
                seen[key] = index
            plan.append((src, dest))

        sources = {str(src.resolve()).lower() for src, _dest in plan}
        for index, (src, dest) in enumerate(plan, start=1):
            if not src.exists():
                errors.append(f"第 {index} 行：找不到原文件 {src}")
                continue
            if src.resolve() == dest.resolve():
                continue
            if dest.exists() and str(dest.resolve()).lower() not in sources:
                errors.append(f"第 {index} 行：目标已存在 {dest.name}")
        return plan, errors

    def _show_text_dialog(self, title: str, body: str, confirm: bool = False) -> bool:
        win = tk.Toplevel(self)
        win.title(title)
        win.geometry("820x560")
        win.transient(self)
        win.grab_set()
        result = {"ok": not confirm}

        text = tk.Text(win, wrap="none", font=("Microsoft YaHei UI", 10), padx=8, pady=8)
        yscroll = ttk.Scrollbar(win, command=text.yview)
        xscroll = ttk.Scrollbar(win, orient="horizontal", command=text.xview)
        text.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        text.insert("1.0", body)
        text.configure(state="disabled")
        text.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        win.columnconfigure(0, weight=1)
        win.rowconfigure(0, weight=1)

        btns = ttk.Frame(win, padding=8)
        btns.grid(row=2, column=0, columnspan=2, sticky="e")

        def close(ok: bool) -> None:
            result["ok"] = ok
            win.destroy()

        if confirm:
            ttk.Button(btns, text="取消", command=lambda: close(False)).pack(side="right")
            ttk.Button(btns, text="确定替换", command=lambda: close(True)).pack(side="right", padx=(0, 8))
        else:
            ttk.Button(btns, text="关闭", command=lambda: close(True)).pack(side="right")
        win.protocol("WM_DELETE_WINDOW", lambda: close(False if confirm else True))
        win.wait_window()
        return result["ok"]

    def preview(self) -> None:
        plan, errors = self._build_plan()
        if errors:
            self._show_text_dialog("无法预览", "\n".join(errors))
            return
        lines = []
        changed = 0
        for index, (src, dest) in enumerate(plan, start=1):
            mark = "→" if src.name != dest.name else "="
            if src.name != dest.name:
                changed += 1
            lines.append(f"{index:>3}.  {src.name}  {mark}  {dest.name}")
        body = f"共 {len(plan)} 条，其中 {changed} 条会改名：\n\n" + ("\n".join(lines) if lines else "（空）")
        self._show_text_dialog("对应关系", body)

    def rename_now(self) -> None:
        plan, errors = self._build_plan()
        if errors:
            self._show_text_dialog("无法替换", "\n".join(errors))
            return
        changing = [(src, dest) for src, dest in plan if src.resolve() != dest.resolve()]
        if not changing:
            messagebox.showinfo("无需替换", "左右文件名完全相同，没有需要改的。")
            return
        lines = [f"{index:>3}.  {src.name}  →  {dest.name}" for index, (src, dest) in enumerate(changing, start=1)]
        body = f"将重命名 {len(changing)} 个文件（最多 {MAX_ITEMS} 条）：\n\n" + "\n".join(lines)
        if not self._show_text_dialog("一键替换", body, confirm=True):
            return

        token = uuid.uuid4().hex[:8]
        temps: list[tuple[Path, Path]] = []
        try:
            for src, dest in changing:
                temp = src.with_name(f".__ren_{token}_{src.name}")
                src.rename(temp)
                temps.append((temp, dest))
            for temp, dest in temps:
                temp.rename(dest)
        except Exception as exc:
            restored = 0
            for temp, dest in reversed(temps):
                current = dest if dest.exists() else temp
                original_name = temp.name.split(f".__ren_{token}_", 1)[-1]
                original = temp.with_name(original_name)
                try:
                    if current.exists() and current.resolve() != original.resolve():
                        current.rename(original)
                        restored += 1
                except Exception:
                    pass
            messagebox.showerror("替换失败", f"{exc}\n已尝试回滚 {restored} 个文件。")
            self._log(f"[失败] {exc}")
            return

        self.undo_pairs = [(dest, src) for src, dest in changing]
        changed_map = {src.resolve(): dest for src, dest in changing}
        self.files = [changed_map.get(src.resolve(), src) for src, _dest in plan]
        self._reload_left()
        self._refresh_linenumbers()
        self._refresh_status()
        self._log(f"[完成] 已替换 {len(changing)} 个文件")
        self.status_var.set(f"已替换 {len(changing)} 个文件，可用「撤销上次替换」还原")
        messagebox.showinfo("完成", f"已替换 {len(changing)} 个文件。")

    def undo_last(self) -> None:
        if not self.undo_pairs:
            messagebox.showinfo("撤销", "没有可撤销的替换。")
            return
        pairs = list(self.undo_pairs)
        if not messagebox.askyesno("撤销", f"把上次的 {len(pairs)} 个文件名改回去？"):
            return
        token = uuid.uuid4().hex[:8]
        temps: list[tuple[Path, Path]] = []
        try:
            for current, original in pairs:
                temp = current.with_name(f".__und_{token}_{current.name}")
                current.rename(temp)
                temps.append((temp, original))
            for temp, original in temps:
                temp.rename(original)
        except Exception as exc:
            messagebox.showerror("撤销失败", str(exc))
            self._log(f"[撤销失败] {exc}")
            return
        restored_map = {current.resolve(): original for current, original in pairs}
        self.files = [restored_map.get(p.resolve(), p) for p in self.files]
        self.undo_pairs = []
        self._reload_left()
        self._refresh_linenumbers()
        self._refresh_status()
        self._log(f"[撤销] 已还原 {len(pairs)} 个文件")
        messagebox.showinfo("完成", f"已还原 {len(pairs)} 个文件。")

    def _log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass
    main()
