"""Tk screenshot translator with one coalescing background worker."""

from __future__ import annotations

import copy
import ctypes
import io
import os
from pathlib import Path
import queue
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from collections import OrderedDict
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Callable, TypeVar

from loguru import logger
from PIL import Image, ImageGrab, ImageTk
import yaml


T = TypeVar("T")


@dataclass
class _Job:
    start: Callable[[], bool]
    run: Callable[[], None]
    cancel: Callable[[], bool]


class LatestWorker:
    """Replace pending jobs of the same kind; never interrupt a running job."""

    def __init__(self, cleanup: Callable[[], None] | None = None):
        self._condition = threading.Condition()
        self._pending: OrderedDict[str, _Job] = OrderedDict()
        self._closed = False
        self._cleanup = cleanup
        self.thread = threading.Thread(target=self._run, name="snipping-worker")
        self.thread.start()

    def submit(self, kind: str, operation: Callable[[], T]) -> Future[T]:
        future: Future[T] = Future()

        def run():
            try:
                result = operation()
            except Exception as error:
                logger.exception("后台操作 {} 失败", kind)
                future.set_exception(error)
            else:
                future.set_result(result)

        job = _Job(future.set_running_or_notify_cancel, run, future.cancel)
        with self._condition:
            if self._closed:
                raise RuntimeError("Background worker is closed")
            previous = self._pending.pop(kind, None)
            if previous is not None:
                previous.cancel()
            self._pending[kind] = job
            self._condition.notify()
        return future

    def cancel(self, kind: str):
        with self._condition:
            job = self._pending.pop(kind, None)
            if job is not None:
                job.cancel()

    def shutdown(self, wait: bool = True):
        with self._condition:
            self._closed = True
            for job in self._pending.values():
                job.cancel()
            self._pending.clear()
            self._condition.notify()
        if wait:
            self.thread.join()

    def _run(self):
        try:
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: self._closed or bool(self._pending))
                    if self._closed:
                        return
                    _, job = self._pending.popitem(last=False)
                    # Starting and replacing a job share the same lock.
                    started = job.start()
                if started:
                    job.run()
        finally:
            if self._cleanup is not None:
                try:
                    self._cleanup()
                except Exception:
                    logger.exception("后台资源清理失败")


class WindowsAudio:
    """Use Windows MCI for WAV/MP3 without Qt or another audio dependency."""

    def __init__(self):
        self._directory = tempfile.TemporaryDirectory(prefix="jp_ocr_audio_")
        self._winmm = ctypes.WinDLL("winmm")
        self._winmm.mciSendStringW.argtypes = [
            ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p,
        ]
        self._winmm.mciSendStringW.restype = ctypes.c_uint
        self._winmm.mciGetErrorStringW.argtypes = [
            ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_uint,
        ]
        self._winmm.mciGetErrorStringW.restype = ctypes.c_bool
        self._opened = False
        self.last_audio_data = b""

    def _command(self, command: str):
        error = self._winmm.mciSendStringW(command, None, 0, None)
        if error:
            text = ctypes.create_unicode_buffer(512)
            self._winmm.mciGetErrorStringW(error, text, len(text))
            raise RuntimeError(f"音频命令失败 ({error}): {text.value}")

    def play(self, data: bytes):
        if not data:
            raise ValueError("音频数据为空")
        if self._opened:
            self._command("close jp_ocr_tk_audio")
            self._opened = False
        is_wav = data[:4] == b"RIFF" and data[8:12] == b"WAVE"
        path = Path(self._directory.name) / ("tts.wav" if is_wav else "tts.mp3")
        path.write_bytes(data)
        self._command(f'open "{path}" type mpegvideo alias jp_ocr_tk_audio')
        self._opened = True
        self._command("play jp_ocr_tk_audio from 0")
        self.last_audio_data = data

    def replay(self):
        if not self.last_audio_data:
            raise RuntimeError("暂无可重播的语音")
        self._command("play jp_ocr_tk_audio from 0")

    def close(self):
        try:
            if self._opened:
                self._command("close jp_ocr_tk_audio")
                self._opened = False
        finally:
            self._directory.cleanup()


class BottomMessageOverlay:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.window = tk.Toplevel(root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.label = tk.Label(
            self.window, bg="#19191c", fg="white", font=("微软雅黑", 12),
            wraplength=680, padx=18, pady=12,
        )
        self.label.pack()
        self.timer = None

    def show_message(self, text: str, timeout_ms: int = 5000):
        if self.timer is not None:
            self.root.after_cancel(self.timer)
        self.label.configure(text=text)
        self.window.update_idletasks()
        width = self.window.winfo_reqwidth()
        height = self.window.winfo_reqheight()
        x = (self.root.winfo_screenwidth() - width) // 2
        y = self.root.winfo_screenheight() - height - 64
        self.window.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.window.deiconify()
        self.timer = self.root.after(timeout_ms, self.hide)

    def hide(self):
        if self.timer is not None:
            self.root.after_cancel(self.timer)
        self.timer = None
        self.window.withdraw()


class SnippingOverlay:
    def __init__(self, controller: SnippingTool):
        self.controller = controller
        self.window = tk.Toplevel(controller.root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.canvas = tk.Canvas(self.window, highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<ButtonPress-1>", self.press)
        self.canvas.bind("<B1-Motion>", self.move)
        self.canvas.bind("<ButtonRelease-1>", self.release)
        self.canvas.bind("<ButtonPress-3>", lambda _event: self.close_overlay())
        self.window.bind("<Escape>", lambda _event: self.close_overlay())
        self.original_image: Image.Image | None = None
        self.photo: ImageTk.PhotoImage | None = None
        self.selection = (0, 0, 0, 0)
        self.start_pos = (0, 0)
        self.ocr_result = ""
        self.translate_result = ""
        self.timer = None
        self.selecting = False
        self.visible = False
        self.last_submitted = None

    def show_capture(self, image: Image.Image):
        self.original_image = image
        self.photo = ImageTk.PhotoImage(image, master=self.window)
        self.selection = (0, 0, 0, 0)
        self.ocr_result = self.translate_result = ""
        self.last_submitted = None
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self.photo)
        self.window.geometry(f"{image.width}x{image.height}+0+0")
        self.visible = True
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()

    def _cancel_timer(self):
        if self.timer is not None:
            self.controller.root.after_cancel(self.timer)
            self.timer = None

    def press(self, event):
        self._cancel_timer()
        self.controller.invalidate_selection()
        self.start_pos = (event.x, event.y)
        self.selection = (event.x, event.y, event.x, event.y)
        self.selecting = True
        self.last_submitted = None
        self.ocr_result = self.translate_result = ""
        self.redraw()

    def move(self, event):
        x, y = self.start_pos
        self.selection = (
            min(x, event.x), min(y, event.y), max(x, event.x), max(y, event.y),
        )
        self.controller.invalidate_selection()
        self.ocr_result = self.translate_result = ""
        self.last_submitted = None
        self.redraw()
        self._cancel_timer()
        self.timer = self.controller.root.after(500, self.perform_ocr)

    def release(self, event):
        if not self.selecting:
            return
        x, y = self.start_pos
        selection = (
            min(x, event.x), min(y, event.y), max(x, event.x), max(y, event.y),
        )
        if selection != self.selection:
            self.controller.invalidate_selection()
            self.ocr_result = self.translate_result = ""
            self.last_submitted = None
        self.selection = selection
        self.selecting = False
        self._cancel_timer()
        self.controller.read_on_completion = True
        self.perform_ocr()
        self.hide()
        self.controller.cancel_translate()
        if self.ocr_result:
            self.controller.read_on_completion = False
            self.controller.play_sound(self.ocr_result)

    def perform_ocr(self):
        self._cancel_timer()
        x1, y1, x2, y2 = self.selection
        if self.original_image is None or x2 - x1 < 10 or y2 - y1 < 10:
            return
        if self.last_submitted == self.selection:
            return
        self.last_submitted = self.selection
        self.controller.start_ocr(self.original_image.crop(self.selection))

    def set_ocr_done(self, text: str):
        self.ocr_result = text
        self.controller.root.clipboard_clear()
        self.controller.root.clipboard_append(text)
        self.redraw()

    def set_translation(self, text: str):
        self.translate_result = text
        self.redraw()

    def redraw(self):
        self.canvas.delete("result")
        self.canvas.delete("selection")
        if not self.visible:
            return
        x1, y1, x2, y2 = self.selection
        self.canvas.create_rectangle(
            x1, y1, x2, y2, outline="red", width=2, dash=(5, 3), tags="selection",
        )
        above = y2 > self.window.winfo_height() * 0.8 and y1 > self.window.winfo_height() * 0.2
        y = y1 - 10 if above else y2 + 10
        for text in (self.ocr_result, self.translate_result):
            if not text:
                continue
            item = self.canvas.create_text(
                x1, y, text=text, anchor="sw" if above else "nw",
                width=max(100, self.window.winfo_width() - x1 - 10),
                fill="white", font=("微软雅黑", 14), tags="result",
            )
            box = self.canvas.bbox(item)
            if box is not None:
                background = self.canvas.create_rectangle(
                    box, fill="black", outline="black", tags="result",
                )
                self.canvas.tag_lower(background, item)
                y = box[1] - 3 if above else box[3] + 3

    def hide(self):
        self.visible = False
        self.window.withdraw()
        self.canvas.delete("all")
        self.original_image = None
        self.photo = None

    def close_overlay(self):
        self._cancel_timer()
        self.selecting = False
        self.controller.invalidate_selection()
        self.hide()


class SettingsDialog:
    FIELDS = (
        ("Google Cloud Key", "key", "gcloud"),
        ("HuggingFace Token", "key", "hf_token"),
        ("阿里云百炼 Key", "key", "ali_key"),
        ("使用代理", "net", "use_proxy"),
        ("代理地址", "net", "proxy_url"),
        ("VOICEVOX 路径 / URL", "voicevox", "src"),
        ("VOICEVOX 说话人 ID", "voicevox", "speaker_id"),
        ("VOICEVOX 语速", "voicevox", "speed_scale"),
        ("本地翻译模型 URL", "translate", "local_model"),
        ("OCR 服务器 URL", "ocr", "server_url"),
        ("本地 OCR 模型", "ocr", "local_model"),
    )

    def __init__(self, controller: SnippingTool):
        self.controller = controller
        self.window = tk.Toplevel(controller.root)
        self.window.title("设置")
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.values: dict[tuple[str, str], tk.Variable] = {}
        for row, (label, section, key) in enumerate(self.FIELDS):
            ttk.Label(self.window, text=label).grid(row=row, column=0, padx=10, pady=5, sticky="w")
            value = controller.config.get(section, {}).get(key, "")
            if key == "use_proxy":
                variable = tk.BooleanVar(self.window, value=bool(value))
                widget = ttk.Checkbutton(self.window, variable=variable, command=self.update_proxy)
            else:
                variable = tk.StringVar(self.window, value=str(value))
                widget = ttk.Entry(self.window, textvariable=variable, width=48)
                if section == "key":
                    widget.configure(show="*")
                if key == "proxy_url":
                    self.proxy_entry = widget
            self.values[section, key] = variable
            widget.grid(row=row, column=1, padx=10, pady=5, sticky="ew")
        buttons = ttk.Frame(self.window)
        buttons.grid(row=len(self.FIELDS), columnspan=2, pady=10)
        ttk.Button(buttons, text="测试语音", command=self.test_voice).pack(side="left", padx=5)
        ttk.Button(buttons, text="保存", command=self.save).pack(side="left", padx=5)
        ttk.Button(buttons, text="取消", command=self.close).pack(side="left", padx=5)
        self.update_proxy()

    def update_proxy(self):
        self.proxy_entry.configure(
            state="normal" if self.values["net", "use_proxy"].get() else "disabled",
        )

    def collect(self):
        config = copy.deepcopy(self.controller.config)
        for (section, key), variable in self.values.items():
            value = variable.get()
            config.setdefault(section, {})[key] = value.strip() if isinstance(value, str) else value
        voice = config["voicevox"]
        voice["speaker_id"] = int(voice["speaker_id"] or 68)
        voice["speed_scale"] = float(voice["speed_scale"] or 0.9)
        if voice["speaker_id"] < 0 or voice["speed_scale"] <= 0:
            raise ValueError("说话人 ID 必须非负，语速必须大于 0")
        config["ocr"]["local_model"] = config["ocr"]["local_model"] or "flex"
        return config

    def test_voice(self):
        try:
            config = self.collect()
        except (ValueError, TypeError) as error:
            messagebox.showerror("设置错误", str(error), parent=self.window)
            return
        self.controller.play_sound("これは音声合成テストです。", config=config)

    def save(self):
        try:
            config = self.collect()
            with self.controller.config_path.open("w", encoding="utf-8") as stream:
                yaml.safe_dump(config, stream, allow_unicode=True)
        except (OSError, ValueError, TypeError, yaml.YAMLError) as error:
            logger.exception("保存设置失败")
            messagebox.showerror("保存失败", str(error), parent=self.window)
            return
        self.controller.config = config
        self.controller.OcrConfigChange()
        self.close()

    def close(self):
        self.controller.settings = None
        self.window.destroy()


class SnippingTool:
    def __init__(self, root: tk.Tk, config_path: Path = Path("conf.yaml"), *, integrations: bool = True):
        self.root = root
        self.config_path = config_path
        self.config = self.load_config()
        self.events: queue.SimpleQueue[Callable[[], None]] = queue.SimpleQueue()
        self.closed = False
        self.selection_id = 0
        self.capture_id = 0
        self.translation_request_id = 0
        self.translation_timer = None
        self.read_on_completion = False
        self.capturing = False
        self.settings = None
        self.tray_icon = None
        self.listener = None
        # These resources are owned exclusively by the worker.
        self.mocr = None
        self._mocr_model = None
        self._service_config = None
        self.gtts = None
        self.voicevox = None
        self.audio = None
        self.worker = LatestWorker(self._cleanup)
        self.overlay = SnippingOverlay(self)
        self.message_overlay = BottomMessageOverlay(root)
        self.root.title("日语截图翻译工具 (Tk)")
        self.root.protocol("WM_DELETE_WINDOW", self.exit_app)
        for label, callback in (
            ("截图 (Alt+Q)", self.start_snip), ("重播 (Alt+W)", self.replay_sound),
            ("设置", self.open_settings), ("退出", self.exit_app),
        ):
            ttk.Button(root, text=label, command=callback).pack(fill="x", padx=10, pady=5)
        self.poll_timer = root.after(20, self._poll)
        if integrations:
            self.start_integrations()
            self.OcrConfigChange()

    def load_config(self):
        if not self.config_path.exists():
            logger.warning("配置文件不存在，将使用默认配置")
            return {}
        with self.config_path.open(encoding="utf-8") as stream:
            config = yaml.safe_load(stream) or {}
        if not isinstance(config, dict):
            raise ValueError("conf.yaml 必须是映射")
        for section in ("key", "net", "voicevox", "translate", "ocr"):
            if section in config and not isinstance(config[section], dict):
                raise ValueError(f"配置节 {section} 必须是映射")
        return config

    def _submit(self, kind, operation, on_done=None, valid=None):
        future = self.worker.submit(kind, operation)

        def completed(done):
            if done.cancelled():
                return

            def deliver():
                if self.closed or (valid is not None and not valid()):
                    return
                error = done.exception()
                if error is not None:
                    self.message_overlay.show_message(f"{kind} 失败: {error}")
                elif on_done is not None:
                    on_done(done.result())

            self.events.put(deliver)

        future.add_done_callback(completed)
        return future

    def _poll(self):
        while not self.closed:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            try:
                event()
            except Exception as error:
                logger.exception("处理界面事件失败")
                if not self.closed:
                    self.message_overlay.show_message(f"界面操作失败: {error}")
        if not self.closed:
            self.poll_timer = self.root.after(20, self._poll)

    def _configure(self, config):
        if self._service_config == config:
            return
        import gTTSfun
        import voicevox

        self.gtts = gTTSfun
        self.voicevox = voicevox
        net = config.get("net", {})
        proxy = net.get("proxy_url") if net.get("use_proxy") else None
        gTTSfun.get_proxy = lambda: proxy or None
        if gTTSfun.session is not None:
            gTTSfun.session.close()
            gTTSfun.session = None
        key = config.get("key", {})
        if key.get("ali_key"):
            gTTSfun.set_ali_ai_client(api_key=key["ali_key"])
        local_url = config.get("translate", {}).get("local_model")
        if local_url:
            gTTSfun.set_ai_client(base_url=local_url)
        voice_path = config.get("voicevox", {}).get("src", "")
        old_path = (self._service_config or {}).get("voicevox", {}).get("src", "")
        if self._service_config is None or voice_path != old_path:
            if self._service_config is not None:
                voicevox.stop_voicevox()
            if voice_path:
                if not voicevox.start_voicevox_if_needed(voice_path, []):
                    if not voicevox.is_voicevox_running():
                        logger.warning("VOICEVOX 不可用，语音合成将尝试 Google TTS")
            else:
                voicevox.ENGINE_URL = "http://localhost:50021"
        self._service_config = copy.deepcopy(config)

    def _load_ocr_model(self, config):
        ocr = config.get("ocr", {})
        if str(ocr.get("server_url") or "").strip():
            self.mocr = None
            self._mocr_model = None
            return
        model = str(ocr.get("local_model") or "flex").strip() or "flex"
        if self.mocr is None or self._mocr_model != model:
            from manga_ocr_cpt_lib import MangaOcrCPT

            self.mocr = MangaOcrCPT(model, local_files_only=True)
            self._mocr_model = model

    def OcrConfigChange(self):
        config = copy.deepcopy(self.config)
        self.invalidate_selection()
        self.overlay.last_submitted = None
        self.overlay.ocr_result = self.overlay.translate_result = ""
        self.overlay.redraw()

        def configure():
            self._configure(config)
            self._load_ocr_model(config)
            return "启动 / 配置加载完成"

        self._submit("config", configure, self.message_overlay.show_message)

    def start_snip(self):
        if self.overlay.visible or self.capturing:
            self.capture_id += 1
            self.capturing = False
            self.worker.cancel("capture")
            self.overlay.close_overlay()
            return
        self.invalidate_selection()
        self.message_overlay.hide()
        self.capture_id += 1
        request_id = self.capture_id
        self.capturing = True

        def captured(image):
            self.capturing = False
            self.overlay.show_capture(image)

        future = self._submit(
            "capture", ImageGrab.grab, captured,
            valid=lambda: request_id == self.capture_id,
        )
        future.add_done_callback(
            lambda _done: self.events.put(
                lambda: self._capture_finished(request_id),
            ),
        )

    def _capture_finished(self, request_id):
        if request_id == self.capture_id:
            self.capturing = False

    def invalidate_selection(self):
        self.selection_id += 1
        self.read_on_completion = False
        self.worker.cancel("ocr")
        self.cancel_translate()

    def start_ocr(self, image: Image.Image):
        request_id = self.selection_id
        config = copy.deepcopy(self.config)
        self._submit(
            "ocr", lambda: self.go_ocr(image, config),
            lambda text: self.on_ocr_done(request_id, text),
            valid=lambda: request_id == self.selection_id,
        )

    def go_ocr(self, image, config):
        self._configure(config)
        self._load_ocr_model(config)
        if self.mocr is not None:
            result = self.mocr(image)
        else:
            import httpx

            data = io.BytesIO()
            image.save(data, format="WEBP", quality=80)
            response = httpx.post(
                config["ocr"]["server_url"], content=data.getvalue(), timeout=30,
            )
            response.raise_for_status()
            result = response.json()["result"]
        if not isinstance(result, str):
            raise ValueError("OCR 返回的 result 必须是字符串")
        return result

    def on_ocr_done(self, request_id, text):
        if request_id != self.selection_id:
            return
        logger.info("OCR 结果：{}", text)
        self.overlay.set_ocr_done(text)
        if self.read_on_completion:
            self.read_on_completion = False
            if text:
                self.play_sound(text)
        elif self.overlay.visible and text:
            self.start_translate(text)

    def cancel_translate(self):
        self.translation_request_id += 1
        if self.translation_timer is not None:
            self.root.after_cancel(self.translation_timer)
            self.translation_timer = None
        self.worker.cancel("translate")

    def start_translate(self, text):
        self.cancel_translate()
        request_id = self.translation_request_id
        config = copy.deepcopy(self.config)
        valid = lambda: request_id == self.translation_request_id and self.overlay.visible

        def submit():
            self.translation_timer = None
            self._submit(
                "translate", lambda: self.go_translate(text, config),
                self.overlay.set_translation,
                valid=valid,
            )

        def lookup():
            self._configure(config)
            return self.gtts.lookup_translation_cache(text)

        def cache_checked(cached):
            if cached is not None:
                self.overlay.set_translation(cached)
            else:
                self.translation_timer = self.root.after(1000, submit)

        self._submit("translate", lookup, cache_checked, valid=valid)

    def go_translate(self, text, config):
        self._configure(config)
        cached = self.gtts.lookup_translation_cache(text)
        if cached is not None:
            return cached
        key = config.get("key", {})
        backends = []
        if key.get("ali_key"):
            backends.append(("阿里云百炼", lambda: self.gtts.translate_with_ali(text)))
        if key.get("gcloud"):
            backends.append(("Google", lambda: self.gtts.translate_with_api_key(
                text=text, target="zh-CN", api_key=key["gcloud"],
            )))
        if config.get("translate", {}).get("local_model"):
            backends.append(("本地模型", lambda: self.gtts.translate_with_local_model(text)))
        errors = []
        for name, translate in backends:
            try:
                translated = translate()
                if not isinstance(translated, str) or not translated.strip():
                    raise ValueError("翻译结果为空或不是字符串")
                return translated
            except Exception as error:
                logger.warning("{} 翻译失败: {}", name, error)
                errors.append(f"{name}: {error}")
        raise RuntimeError("\n".join(errors) if errors else "请先配置翻译服务")

    def play_sound(self, text, config=None):
        snapshot = copy.deepcopy(self.config if config is None else config)
        self._submit("audio", lambda: self.goPlaySound(text, snapshot))

    def goPlaySound(self, text, config):
        self._configure(config)
        voice = config.get("voicevox", {})
        try:
            stream = self.voicevox.japanese_tts(
                text=text, speaker=int(voice.get("speaker_id") or 68),
                speed_scale=float(voice.get("speed_scale") or 0.9),
                output_sampling_rate=24000,
            )
        except Exception as error:
            logger.warning("VOICEVOX 合成失败，尝试 Google TTS: {}", error)
            stream = None
        if stream is None:
            stream = self.gtts.japanese_tts(text)
        if self.audio is None:
            self.audio = WindowsAudio()
        self.audio.play(stream.getvalue())

    def replay_sound(self):
        def replay():
            if self.audio is None:
                raise RuntimeError("暂无可重播的语音")
            self.audio.replay()

        self._submit("audio", replay)

    def open_settings(self):
        if self.settings is None:
            self.settings = SettingsDialog(self)
        else:
            self.settings.window.lift()

    def start_integrations(self):
        import pystray
        from pynput import keyboard

        def post(callback):
            return lambda *_args: self.events.put(callback)

        icon_path = Path("data") / "tray.png"
        if icon_path.exists():
            with Image.open(icon_path) as source:
                image = source.copy()
        else:
            image = Image.new("RGB", (32, 32), "blue")
        self.tray_icon = pystray.Icon(
            "jp_ocr_tk", image, "日语截图翻译工具",
            menu=pystray.Menu(
                pystray.MenuItem("截图", post(self.start_snip), default=True),
                pystray.MenuItem("重播", post(self.replay_sound)),
                pystray.MenuItem("设置", post(self.open_settings)),
                pystray.MenuItem("退出", post(self.exit_app)),
            ),
        )
        self.tray_icon.run_detached()
        self.listener = keyboard.GlobalHotKeys({
            "<alt>+q": post(self.start_snip),
            "<alt>+w": post(self.replay_sound),
        })
        self.listener.start()
        self.root.withdraw()

    def _cleanup(self):
        try:
            if self.audio is not None:
                self.audio.close()
        finally:
            try:
                if self.gtts is not None and self.gtts.session is not None:
                    self.gtts.session.close()
                    self.gtts.session = None
            finally:
                if self.voicevox is not None:
                    self.voicevox.stop_voicevox()

    def exit_app(self):
        if self.closed:
            return
        self.closed = True
        self.cancel_translate()
        self.overlay._cancel_timer()
        self.message_overlay.hide()
        self.root.after_cancel(self.poll_timer)
        if self.listener is not None:
            self.listener.stop()
        if self.tray_icon is not None:
            self.tray_icon.stop()
        self.worker.shutdown(wait=False)
        self.root.destroy()


def main():
    if sys.platform != "win32":
        raise RuntimeError("Tk 版本的托盘和音频播放目前针对 Windows")
    import checkSingle

    checkSingle.check_single_instance()
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if stream is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
        elif hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.SetProcessDpiAwarenessContext.restype = ctypes.c_bool
    if not user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
        logger.warning("无法设置 DPI 感知（可能已由解释器设置）: {}", ctypes.get_last_error())
    root = tk.Tk()
    root.withdraw()
    tool = None
    try:
        tool = SnippingTool(root, integrations=False)
        tool.start_integrations()
        tool.OcrConfigChange()
        root.mainloop()
    except Exception as error:
        logger.exception("Tk 截图工具启动 / 运行失败")
        messagebox.showerror("截图工具错误", str(error), parent=root)
        raise
    finally:
        if tool is not None:
            if not tool.closed:
                tool.exit_app()
            tool.worker.shutdown()
        else:
            root.destroy()


if __name__ == "__main__":
    main()
