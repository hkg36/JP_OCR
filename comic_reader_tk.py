"""Tk comic reader with worker-owned archives and main-thread-only Tk access."""

from __future__ import annotations

from dataclasses import dataclass
import io
import logging
from pathlib import Path
import queue
import sys
import sysconfig
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox
from typing import Literal
import warnings

import natsort as ns
from PIL import Image, ImageOps, ImageTk
import pybit7z
from send2trash import send2trash
import yaml


logger = logging.getLogger(__name__)
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}
ARCHIVE_EXTENSIONS = {".zip", ".rar", ".7z", ".cbz", ".cbr"}
NATURAL_KEY = ns.natsort_keygen(alg=ns.IGNORECASE | ns.PATH)


@dataclass(frozen=True)
class SourceInfo:
    path: Path
    names: tuple[str, ...]
    archives: tuple[Path, ...]
    initial_index: int

    @property
    def is_archive(self) -> bool:
        return self.path.suffix.lower() in ARCHIVE_EXTENSIONS


class ComicSource:
    """Created, read and closed exclusively by the reader worker."""

    def __init__(self, path: Path):
        self.library: pybit7z.Bit7zLibrary | None = None
        self.reader: pybit7z.BitArchiveReader | None = None
        self.indices: tuple[int, ...] = ()
        if path.suffix.lower() in ARCHIVE_EXTENSIONS:
            self.library = pybit7z.Bit7zLibrary(pybit7z.default_lib7zip())
            self.reader = pybit7z.BitArchiveReader(self.library, str(path))
            items = sorted(
                (item for item in self.reader.items()
                 if not item.is_dir() and Path(item.path()).suffix.lower() in IMAGE_EXTENSIONS),
                key=lambda item: NATURAL_KEY(item.path()),
            )
            names = tuple(item.path() for item in items)
            self.indices = tuple(item.index() for item in items)
            archives = tuple(sorted(
                (item for item in path.parent.iterdir()
                 if item.is_file() and item.suffix.lower() in ARCHIVE_EXTENSIONS),
                key=lambda item: NATURAL_KEY(str(item)),
            ))
            self.info = SourceInfo(path, names, archives, 0)
        else:
            if path.suffix.lower() not in IMAGE_EXTENSIONS or not path.is_file():
                raise ValueError(f"不支持的图片文件: {path}")
            folder = path.parent
            files: list[Path] = []

            def walk_error(error: OSError) -> None:
                raise error

            for root, _, filenames in folder.walk(on_error=walk_error):
                files.extend(root / name for name in filenames
                             if Path(name).suffix.lower() in IMAGE_EXTENSIONS)
            files.sort(key=lambda item: (
                item.parent != folder, NATURAL_KEY(str(item.relative_to(folder))),
            ))
            self.info = SourceInfo(path, tuple(str(item) for item in files), (), files.index(path))

    def read(self, index: int) -> Image.Image:
        if self.reader is not None:
            data = self.reader.extract_to(self.indices[index])
            with Image.open(io.BytesIO(data)) as original:
                return self._decode(original)
        with Image.open(self.info.names[index]) as original:
            return self._decode(original)

    @staticmethod
    def _decode(original: Image.Image) -> Image.Image:
        oriented = ImageOps.exif_transpose(original)
        try:
            has_alpha = "A" in oriented.getbands() or "transparency" in oriented.info
            image = oriented.convert("RGBA" if has_alpha else "RGB")
            image.load()
            return image
        finally:
            oriented.close()

    def close(self) -> None:
        # pybit7z releases the archive handle on destruction; the DLL must outlive it.
        self.reader = None
        self.library = None


@dataclass(frozen=True)
class Request:
    kind: Literal["open", "pages", "trash"]
    generation: int
    path: Path
    index: int = 0
    cached: frozenset[int] = frozenset()


@dataclass(frozen=True)
class Result:
    kind: Literal["opened", "image", "trashed", "error"]
    generation: int
    index: int = -1
    info: SourceInfo | None = None
    image: Image.Image | None = None
    error: str = ""


class ReaderWorker:
    """One replaceable pending request; running native calls finish before switching."""

    def __init__(self):
        self.results: queue.SimpleQueue[Result] = queue.SimpleQueue()
        self._condition = threading.Condition()
        self._pending: Request | None = None
        self._version = 0
        self._closed = False
        self._source: ComicSource | None = None
        self._generation = -1
        self.thread = threading.Thread(target=self._run, name="comic-reader-worker")
        self.thread.start()

    def submit(self, request: Request) -> None:
        with self._condition:
            if self._closed:
                raise RuntimeError("Reader worker is closed")
            self._pending = request
            self._version += 1
            self._condition.notify()

    def shutdown(self, wait: bool = True) -> None:
        with self._condition:
            self._closed = True
            self._pending = None
            self._version += 1
            self._condition.notify()
        if wait:
            self.thread.join()

    def _active(self, version: int) -> bool:
        with self._condition:
            return not self._closed and version == self._version

    def _close_source(self) -> None:
        if self._source is not None:
            self._source.close()
            self._source = None
        self._generation = -1

    def _run(self) -> None:
        try:
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: self._closed or self._pending is not None)
                    if self._closed:
                        return
                    request = self._pending
                    self._pending = None
                    version = self._version
                assert request is not None
                try:
                    self._execute(request, version)
                except Exception as error:
                    logger.exception("漫画后台操作失败: %s", request.path)
                    self.results.put(Result("error", request.generation, error=str(error)))
        finally:
            self._close_source()

    def _execute(self, request: Request, version: int) -> None:
        if request.kind == "trash":
            self._close_source()
            if request.path.parent == request.path:
                raise ValueError("不能删除根目录")
            send2trash(str(request.path))
            self.results.put(Result("trashed", request.generation))
            return
        if request.kind == "open":
            self._close_source()
            self._source = ComicSource(request.path)
            self._generation = request.generation
            if self._active(version):
                self.results.put(Result("opened", request.generation, info=self._source.info))
            return
        source = self._source
        if source is None or self._generation != request.generation:
            raise RuntimeError("图片请求没有对应的已打开文件")
        indices = (request.index, request.index + 1, request.index - 1)
        for index in indices:
            if not self._active(version):
                return
            if index in request.cached or not 0 <= index < len(source.info.names):
                continue
            try:
                image = source.read(index)
            except Exception as error:
                logger.exception("加载图片失败: %s", source.info.names[index])
                self.results.put(Result("error", request.generation, index=index,
                                        error=f"{source.info.names[index]}\n{error}"))
            else:
                if self._active(version):
                    self.results.put(Result("image", request.generation, index=index, image=image))
                else:
                    image.close()


class ComicReader:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.config_path = Path(__file__).with_name("reader.yaml")
        self.config: dict = {}
        self.initial_dir = str(Path.home() / "Downloads") if sys.platform != "win32" else "E:\\baks"
        self.info: SourceInfo | None = None
        self.current_index = 0
        self.generation = 0
        self.cache: dict[int, Image.Image] = {}
        self.errors: dict[int, str] = {}
        self._photo: ImageTk.PhotoImage | None = None
        self._closing = False
        self._deleting = False
        self._updating_progress = False
        self._resize_after: str | None = None
        self.last_wheel_time = 0.0
        self.scroll_start_time = 0.0
        root.title("漫画阅读器")
        root.attributes("-fullscreen", True)
        root.configure(background="black")
        self.image_label = tk.Label(root, text="请右键点击 -> 打开压缩包或图片",
                                    background="black", foreground="white", borderwidth=0)
        self.image_label.pack(fill="both", expand=True)
        self.overlay = tk.Frame(root, background="#202020")
        self.filename_label = tk.Label(self.overlay, background="#202020", foreground="white",
                                       wraplength=300, justify="left")
        self.filename_label.pack(fill="x")
        self.progress = tk.Scale(self.overlay, from_=1, to=1, orient="horizontal",
                                 length=300, showvalue=False, highlightthickness=0,
                                 background="#202020", foreground="white", takefocus=False,
                                 command=self.on_progress_changed)
        self.progress.pack()
        self.menu = tk.Menu(root, tearoff=False)
        self.menu.add_command(label="打开 ZIP / RAR / 7z 或图片 (Ctrl+O)", command=self.open_file_dialog)
        self.menu.add_command(label="删除到回收站 (Del)", command=self.delete_current_file)
        self.menu.add_separator()
        self.menu.add_command(label="退出 (Esc)", command=self.close)
        self.image_label.bind("<Button-1>", self._left_click)
        self.image_label.bind("<Button-2>", lambda event: self.open_file_dialog())
        root.bind("<Button-3>", self._context_menu)
        root.bind("<Control-o>", lambda event: self.open_file_dialog())
        for key in ("<Left>", "<Prior>"):
            root.bind(key, lambda event: self.change_page(-1))
        for key in ("<Right>", "<Next>"):
            root.bind(key, lambda event: self.change_page(1))
        root.bind("<Up>", lambda event: self.change_archive(-1))
        root.bind("<Down>", lambda event: self.change_archive(1))
        root.bind("<Delete>", lambda event: self.delete_current_file())
        root.bind("<Escape>", lambda event: self.close())
        root.bind("<MouseWheel>", self._wheel)
        root.bind("<Button-4>", self._wheel)
        root.bind("<Button-5>", self._wheel)
        self.image_label.bind("<Configure>", self._resize)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.load_config()
        self.worker = ReaderWorker()
        self._poll_after: str | None = root.after(20, self._poll)

    def load_config(self) -> None:
        try:
            if self.config_path.exists():
                with self.config_path.open(encoding="utf-8") as file:
                    config = yaml.safe_load(file)
                if config is None:
                    config = {}
                if not isinstance(config, dict):
                    raise ValueError("reader.yaml 必须是配置映射")
                if "last_dir" in config and not isinstance(config["last_dir"], str):
                    raise ValueError("last_dir 必须是目录字符串")
                self.config = config
                self.initial_dir = config.get("last_dir", self.initial_dir)
        except (OSError, ValueError, yaml.YAMLError) as error:
            logger.exception("读取配置失败")
            messagebox.showerror("读取配置失败", str(error), parent=self.root)

    def save_config(self) -> None:
        try:
            self.config["last_dir"] = self.initial_dir
            with self.config_path.open("w", encoding="utf-8") as file:
                yaml.safe_dump(self.config, file, allow_unicode=True)
        except (OSError, yaml.YAMLError) as error:
            logger.exception("保存配置失败")
            messagebox.showerror("保存配置失败", str(error), parent=self.root)

    def open_file_dialog(self) -> None:
        if self._closing or self._deleting:
            return
        extensions = " ".join(f"*{ext}" for ext in sorted(IMAGE_EXTENSIONS | ARCHIVE_EXTENSIONS))
        path = filedialog.askopenfilename(
            parent=self.root, title="选择漫画压缩包或图片", initialdir=self.initial_dir,
            filetypes=[("压缩包和图片", extensions), ("所有文件", "*")],
        )
        if path:
            self.open_path(Path(path))

    def _clear_cache(self) -> None:
        for image in self.cache.values():
            image.close()
        self.cache.clear()
        self.errors.clear()

    def _message(self, text: str) -> None:
        self.image_label.configure(image="", text=text)
        self._photo = None

    def open_path(self, path: Path) -> None:
        if self._closing or self._deleting:
            return
        path = path.resolve()
        self.generation += 1
        self.info = None
        self.current_index = 0
        self._clear_cache()
        self.overlay.place_forget()
        self._message("正在加载...")
        self.initial_dir = str(path.parent)
        self.worker.submit(Request("open", self.generation, path))

    def _wanted(self) -> set[int]:
        if self.info is None:
            return set()
        return set(range(max(0, self.current_index - 1),
                         min(len(self.info.names), self.current_index + 2)))

    def show_current_page(self) -> None:
        info = self.info
        if info is None or not info.names:
            return
        wanted = self._wanted()
        for index in list(self.cache):
            if index not in wanted:
                self.cache.pop(index).close()
        for index in list(self.errors):
            if index not in wanted:
                del self.errors[index]
        name = info.path.name if info.is_archive else Path(info.names[self.current_index]).name
        self.filename_label.configure(text=f"{name}\n{self.current_index + 1} / {len(info.names)}")
        self._updating_progress = True
        try:
            self.progress.configure(to=len(info.names))
            self.progress.set(self.current_index + 1)
        finally:
            self._updating_progress = False
        self.overlay.place(x=10, y=10)
        self.overlay.lift()
        self._render()
        self.worker.submit(Request("pages", self.generation, info.path, self.current_index,
                                   frozenset(self.cache)))

    def _render(self) -> None:
        image = self.cache.get(self.current_index)
        if image is None:
            if self.info is not None and self.info.names:
                error = self.errors.get(self.current_index)
                self._message(f"无法加载图片:\n{error}" if error else "正在加载图片...")
            return
        size = (max(1, self.image_label.winfo_width()), max(1, self.image_label.winfo_height()))
        scaled = ImageOps.contain(image, size, Image.Resampling.LANCZOS)
        try:
            self._photo = ImageTk.PhotoImage(scaled, master=self.root)
        finally:
            scaled.close()
        self.image_label.configure(image=self._photo, text="")

    def _poll(self) -> None:
        self._poll_after = None
        while True:
            try:
                result = self.worker.results.get_nowait()
            except queue.Empty:
                break
            if self._closing or result.generation != self.generation:
                if result.image is not None:
                    result.image.close()
                continue
            self._accept(result)
        if self._closing:
            if self.worker.thread.is_alive():
                self._poll_after = self.root.after(20, self._poll)
            else:
                self.root.destroy()
        else:
            self._poll_after = self.root.after(20, self._poll)

    def _accept(self, result: Result) -> None:
        if result.kind == "opened":
            assert result.info is not None
            self.info = result.info
            self.current_index = result.info.initial_index
            if self.info.names:
                self.show_current_page()
            else:
                self._message("未找到有效图片")
                self.change_archive(1)
        elif result.kind == "image":
            assert result.image is not None
            if result.index in self._wanted():
                previous = self.cache.get(result.index)
                if previous is not None:
                    previous.close()
                self.cache[result.index] = result.image
                self.errors.pop(result.index, None)
                if result.index == self.current_index:
                    self._render()
            else:
                result.image.close()
        elif result.kind == "trashed":
            self._deleting = False
            info = self.info
            assert info is not None
            self.info = None
            if info.is_archive:
                remaining = [path for path in info.archives if path != info.path]
                if remaining:
                    index = min(info.archives.index(info.path), len(remaining) - 1)
                    self.open_path(remaining[index])
                    return
            self.overlay.place_forget()
            self._message("没有文件了")
        elif result.kind == "error":
            if self._deleting:
                self._deleting = False
                info = self.info
                if info is not None:
                    self.open_path(info.path)
                messagebox.showerror("删除失败", result.error, parent=self.root)
            elif result.index < 0:
                self._message(f"打开失败:\n{result.error}")
            elif result.index in self._wanted():
                self.errors[result.index] = result.error
                if result.index == self.current_index:
                    self._render()

    def change_page(self, delta: int) -> None:
        if self._closing or self._deleting or self.info is None:
            return
        index = self.current_index + delta
        if 0 <= index < len(self.info.names):
            self.current_index = index
            self.show_current_page()

    def change_archive(self, delta: int) -> None:
        if self._closing or self._deleting or self.info is None or not self.info.is_archive:
            return
        index = self.info.archives.index(self.info.path) + delta
        if 0 <= index < len(self.info.archives):
            self.open_path(self.info.archives[index])

    def on_progress_changed(self, value: str) -> None:
        if self._updating_progress or self._closing or self._deleting or self.info is None:
            return
        index = int(float(value)) - 1
        if index != self.current_index and 0 <= index < len(self.info.names):
            self.current_index = index
            self.show_current_page()

    def _left_click(self, event: tk.Event) -> None:
        if self.info is None:
            self.open_file_dialog()
        else:
            self.change_page(1)

    def _context_menu(self, event: tk.Event) -> None:
        self.menu.entryconfigure(1, state="normal" if self.info is not None
                                 and not self._deleting else "disabled")
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def _wheel(self, event: tk.Event) -> str:
        now = time.monotonic()
        if now - self.last_wheel_time > 0.8:
            self.scroll_start_time = now
        speed = 4 if now - self.scroll_start_time < 0.8 else 10
        if now - self.last_wheel_time >= 1 / speed:
            if event.num == 4 or event.delta > 0:
                self.change_page(-1)
            elif event.num == 5 or event.delta < 0:
                self.change_page(1)
            self.last_wheel_time = now
        return "break"

    def _resize(self, event: tk.Event) -> None:
        if self._closing:
            return
        if self._resize_after is not None:
            self.root.after_cancel(self._resize_after)
        self._resize_after = self.root.after(60, self._render_resized)

    def _render_resized(self) -> None:
        self._resize_after = None
        if not self._closing:
            self._render()

    def delete_current_file(self) -> None:
        if self._closing or self._deleting or self.info is None:
            return
        target = self.info.path if self.info.is_archive else self.info.path.parent
        if target.parent == target:
            messagebox.showerror("错误", "不能删除根目录", parent=self.root)
            return
        if not messagebox.askyesno("确认删除", f"确认移动到回收站?\n{target}",
                                  parent=self.root, default=messagebox.NO):
            return
        self._deleting = True
        self.generation += 1
        self._clear_cache()
        self._message("正在移动到回收站...")
        self.worker.submit(Request("trash", self.generation, target))

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        if self._resize_after is not None:
            self.root.after_cancel(self._resize_after)
            self._resize_after = None
        self.save_config()
        self.overlay.place_forget()
        self._message("正在等待后台操作结束...")
        self._clear_cache()
        self.worker.shutdown(wait=False)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    if sysconfig.get_config_var("Py_GIL_DISABLED") and sys._is_gil_enabled():
        warnings.warn("某个依赖启用了 GIL；请安装与 free-threaded Python 匹配的依赖。",
                      RuntimeWarning, stacklevel=1)
    root = tk.Tk()
    reader = ComicReader(root)
    try:
        if len(sys.argv) > 1:
            reader.open_path(Path(sys.argv[1]))
        root.mainloop()
    finally:
        reader.worker.shutdown()
        reader._clear_cache()
        while not reader.worker.results.empty():
            result = reader.worker.results.get_nowait()
            if result.image is not None:
                result.image.close()


if __name__ == "__main__":
    main()
