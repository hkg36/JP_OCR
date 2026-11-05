import binascii
import ctypes
from ctypes import wintypes
import gc
from pathlib import Path
import queue
import struct
import sys
import sysconfig
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from PIL import Image
import pybit7z

from comic_reader_tk import (
    ComicReader, ComicSource, ReaderWorker, Request, Result, enable_native_dpi,
)


def make_image(path, color="red"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", (40, 60), color) as image:
        image.save(path)
    return path


def make_archive(path, files, archive_format=pybit7z.FormatZip):
    library = pybit7z.Bit7zLibrary(pybit7z.default_lib7zip())
    compressor = pybit7z.BitFileCompressor(library, archive_format)
    compressor.compress({str(source): name for source, name in files}, str(path))
    return path


def make_stored_rar(path, name, data):
    """Generate an original, single-file RAR4 fixture without an external archiver."""
    def header(kind, flags, body):
        raw = struct.pack("<BHH", kind, flags, 7 + len(body)) + body
        return struct.pack("<H", binascii.crc32(raw) & 0xFFFF) + raw

    encoded_name = name.encode("ascii")
    file_body = struct.pack(
        "<IIBIIBBHI", len(data), len(data), 2, binascii.crc32(data),
        0, 20, 0x30, len(encoded_name), 0x20,
    ) + encoded_name
    path.write_bytes(
        b"Rar!\x1a\x07\x00" + header(0x73, 0, b"\x00" * 6)
        + header(0x74, 0x8000, file_body) + data + header(0x7B, 0, b"")
    )
    return path


class TempTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)

    def source(self, path):
        source = ComicSource(path)
        self.addCleanup(source.close)
        return source


class SourceTests(TempTests):
    def test_folder_natural_order_direct_children_first_and_selected_page(self):
        for name in ("10.png", "2.png", "sub/1.png", "sub/10.png", "sub/2.png"):
            make_image(self.folder / name)
        source = self.source(self.folder / "10.png")
        self.assertEqual(
            [Path(name).relative_to(self.folder).as_posix() for name in source.info.names],
            ["2.png", "10.png", "sub/1.png", "sub/2.png", "sub/10.png"],
        )
        self.assertEqual(source.info.initial_index, 1)
        self.assertFalse(source.info.is_archive)

    def test_zip_7z_cbz_sorting_and_memory_extraction(self):
        first = make_image(self.folder / "first.png", "red")
        second = make_image(self.folder / "second.png", "blue")
        for suffix, archive_format in (
            (".zip", pybit7z.FormatZip),
            (".7z", pybit7z.FormatSevenZip),
            (".cbz", pybit7z.FormatZip),
        ):
            with self.subTest(suffix=suffix):
                archive = make_archive(self.folder / f"book{suffix}",
                                       [(second, "pages/10.png"), (first, "pages/2.png")],
                                       archive_format)
                source = self.source(archive)
                self.assertTrue(source.info.is_archive)
                self.assertEqual([Path(name).name for name in source.info.names],
                                 ["2.png", "10.png"])
                with source.read(0) as image:
                    self.assertEqual(image.getpixel((0, 0)), (255, 0, 0))
                with source.read(1) as image:
                    self.assertEqual(image.getpixel((0, 0)), (0, 0, 255))
                source.close()
                archive.unlink()

    def test_real_rar_and_cbr_decode(self):
        page = make_image(self.folder / "page.png", "green")
        for suffix in (".rar", ".cbr"):
            archive = make_stored_rar(self.folder / f"book{suffix}", "page.png", page.read_bytes())
            source = self.source(archive)
            self.assertEqual(source.info.names, ("page.png",))
            with source.read(0) as image:
                self.assertEqual(image.size, (40, 60))
                self.assertEqual(image.getpixel((0, 0)), (0, 128, 0))
            source.close()
            archive.unlink()

    def test_archive_list_includes_rar_and_uses_natural_order(self):
        page = make_image(self.folder / "page.png")
        for name in ("book10.rar", "book2.zip", "book1.7z"):
            make_archive(self.folder / name, [(page, "page.png")])
        (self.folder / "not-a-file.zip").mkdir()
        source = self.source(self.folder / "book2.zip")
        self.assertEqual([path.name for path in source.info.archives],
                         ["book1.7z", "book2.zip", "book10.rar"])

    def test_exif_orientation_is_applied_and_image_is_detached(self):
        path = self.folder / "rotated.jpg"
        with Image.new("RGB", (20, 40)) as image:
            exif = Image.Exif()
            exif[274] = 6
            image.save(path, exif=exif)
        source = self.source(path)
        with source.read(0) as image:
            self.assertEqual(image.size, (40, 20))
            path.unlink()
            image.load()

    def test_invalid_archive_and_unsupported_file_raise(self):
        invalid = self.folder / "broken.rar"
        invalid.write_bytes(b"not an archive")
        with self.assertRaises(pybit7z.BitException):
            ComicSource(invalid)
        with self.assertRaisesRegex(ValueError, "不支持"):
            ComicSource(self.folder / "unknown.txt")

    def test_palette_transparency_is_preserved(self):
        path = self.folder / "transparent.png"
        with Image.new("P", (20, 20)) as image:
            image.putpalette([255, 0, 0] + [0] * 765)
            image.save(path, transparency=0)
        source = self.source(path)
        with source.read(0) as image:
            self.assertEqual(image.mode, "RGBA")
            self.assertEqual(image.getpixel((0, 0)), (255, 0, 0, 0))

    def test_free_threaded_imports_do_not_enable_gil(self):
        if not sysconfig.get_config_var("Py_GIL_DISABLED"):
            self.skipTest("Requires a free-threaded interpreter")
        self.assertFalse(sys._is_gil_enabled())


class WorkerTests(TempTests):
    def setUp(self):
        super().setUp()
        for index in range(5):
            make_image(self.folder / f"{index}.png")
        self.worker = ReaderWorker()
        self.release = threading.Event()
        self.addCleanup(self.cleanup_worker)
        self.worker.submit(Request("open", 1, self.folder / "0.png"))
        opened = self.worker.results.get(timeout=5)
        self.assertEqual(opened.kind, "opened")

    def cleanup_worker(self):
        self.release.set()
        self.worker.shutdown()
        while True:
            try:
                result = self.worker.results.get_nowait()
            except queue.Empty:
                break
            if result.image is not None:
                result.image.close()

    def test_current_then_neighbors_decode_only_on_worker(self):
        calls = []
        original = ComicSource.read

        def read(source, index):
            calls.append((threading.get_ident(), index))
            return original(source, index)

        with patch.object(ComicSource, "read", read):
            self.worker.submit(Request("pages", 1, self.folder / "0.png", 2))
            results = [self.worker.results.get(timeout=5) for _ in range(3)]
        for result in results:
            result.image.close()
        self.assertEqual([result.index for result in results], [2, 3, 1])
        self.assertEqual(calls, [(self.worker.thread.ident, index) for index in (2, 3, 1)])
        self.assertNotEqual(self.worker.thread.ident, threading.get_ident())

    def test_cached_pages_are_not_decoded_again(self):
        self.worker.submit(Request("pages", 1, self.folder / "0.png", 2, frozenset({2, 3})))
        result = self.worker.results.get(timeout=5)
        self.assertEqual(result.index, 1)
        result.image.close()

    def test_newest_pending_page_replaces_old_and_stops_obsolete_preload(self):
        started = threading.Event()
        calls = []
        original = ComicSource.read

        def read(source, index):
            calls.append(index)
            if len(calls) == 1:
                started.set()
                if not self.release.wait(5):
                    raise TimeoutError("Test did not release worker")
            return original(source, index)

        with patch.object(ComicSource, "read", read):
            self.worker.submit(Request("pages", 1, self.folder / "0.png", 0))
            self.assertTrue(started.wait(5))
            self.worker.submit(Request("pages", 1, self.folder / "0.png", 1))
            self.worker.submit(Request("pages", 1, self.folder / "0.png", 3))
            self.release.set()
            results = [self.worker.results.get(timeout=5) for _ in range(3)]
        self.assertEqual(calls, [0, 3, 4, 2])
        self.assertEqual([result.index for result in results], [3, 4, 2])
        for result in results:
            result.image.close()

    def test_close_cancels_pending_and_waits_for_running_decode(self):
        started = threading.Event()
        original = ComicSource.read

        def read(source, index):
            started.set()
            if not self.release.wait(5):
                raise TimeoutError("Test did not release worker")
            return original(source, index)

        with patch.object(ComicSource, "read", read):
            self.worker.submit(Request("pages", 1, self.folder / "0.png"))
            self.assertTrue(started.wait(5))
            self.worker.submit(Request("open", 2, self.folder / "1.png"))
            self.worker.shutdown(wait=False)
            self.assertTrue(self.worker.thread.is_alive())
            with self.assertRaisesRegex(RuntimeError, "closed"):
                self.worker.submit(Request("pages", 1, self.folder / "0.png"))
            self.release.set()
            self.worker.shutdown()
        self.assertFalse(self.worker.thread.is_alive())
        self.assertTrue(self.worker.results.empty())
        self.assertIsNone(self.worker._source)

    def test_corrupt_current_page_reports_error_but_preloads_neighbor(self):
        (self.folder / "0.png").write_bytes(b"bad image")
        with self.assertLogs("comic_reader_tk", level="ERROR"):
            self.worker.submit(Request("pages", 1, self.folder / "0.png"))
            failed = self.worker.results.get(timeout=5)
            neighbor = self.worker.results.get(timeout=5)
        self.assertEqual((failed.kind, failed.index), ("error", 0))
        self.assertIn("0.png", failed.error)
        self.assertEqual((neighbor.kind, neighbor.index), ("image", 1))
        neighbor.image.close()

    def test_archive_handle_released_before_recycle_on_worker_thread(self):
        archive = make_archive(self.folder / "book.zip",
                               [(self.folder / "0.png", "page.png")])
        self.worker.submit(Request("open", 2, archive))
        self.assertEqual(self.worker.results.get(timeout=5).kind, "opened")
        called_on = []

        def recycle(path):
            called_on.append(threading.get_ident())
            Path(path).rename(self.folder / "recycled.zip")

        with patch("comic_reader_tk.send2trash", recycle):
            self.worker.submit(Request("trash", 3, archive))
            self.assertEqual(self.worker.results.get(timeout=5).kind, "trashed")
        self.assertEqual(called_on, [self.worker.thread.ident])
        self.assertFalse(archive.exists())

    def test_recycle_failure_is_reported(self):
        with patch("comic_reader_tk.send2trash", side_effect=OSError("recycle failed")):
            with self.assertLogs("comic_reader_tk", level="ERROR"):
                self.worker.submit(Request("trash", 2, self.folder / "0.png"))
                result = self.worker.results.get(timeout=5)
        self.assertEqual(result.kind, "error")
        self.assertIn("recycle failed", result.error)

    def test_concurrent_producers_keep_only_latest_pending_request(self):
        started = threading.Event()
        calls = []
        original = ComicSource.read

        def read(source, index):
            calls.append(index)
            if len(calls) == 1:
                started.set()
                if not self.release.wait(5):
                    raise TimeoutError("Test did not release worker")
            return original(source, index)

        def produce():
            for index in range(50):
                self.worker.submit(Request("pages", 1, self.folder / "0.png", index % 5))

        with patch.object(ComicSource, "read", read):
            self.worker.submit(Request("pages", 1, self.folder / "0.png"))
            self.assertTrue(started.wait(5))
            producers = [threading.Thread(target=produce) for _ in range(4)]
            for producer in producers:
                producer.start()
            for producer in producers:
                producer.join(5)
                self.assertFalse(producer.is_alive())
            self.worker.submit(Request("pages", 1, self.folder / "0.png", 2, frozenset({2, 3})))
            self.release.set()
            result = self.worker.results.get(timeout=5)
        self.assertEqual(calls, [0, 1])
        self.assertEqual((result.kind, result.index), ("image", 1))
        result.image.close()


class TkReaderTests(TempTests):
    def setUp(self):
        super().setUp()
        for index in range(5):
            make_image(self.folder / f"{index}.png")
        enable_native_dpi()
        self.root = tk.Tk()
        self.root.withdraw()
        with patch.object(ComicReader, "load_config"):
            self.reader = ComicReader(self.root)
        self.addCleanup(self.cleanup_reader)

    def cleanup_reader(self):
        self.reader.worker.shutdown()
        self.reader._clear_cache()
        while not self.reader.worker.results.empty():
            result = self.reader.worker.results.get_nowait()
            if result.image is not None:
                result.image.close()
        for token in (self.reader._poll_after, self.reader._resize_after):
            if token is not None:
                self.root.after_cancel(token)
        self.reader._photo = None
        self.root.destroy()
        del self.reader
        del self.root
        # Collect Tk reference cycles here, never in the next test's worker thread.
        gc.collect()

    def pump_until(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate():
            if time.monotonic() > deadline:
                self.fail("Tk reader did not reach expected state")
            self.root.update()
            time.sleep(0.005)

    @unittest.skipUnless(sys.platform == "win32", "Windows window geometry")
    def test_startup_is_maximized_with_fixed_scaling_and_taskbar_space(self):
        self.root.deiconify()
        self.pump_until(lambda: self.root.state() == "zoomed"
                        and self.root.winfo_width() > 300)
        self.assertFalse(self.root.attributes("-fullscreen"))
        self.assertAlmostEqual(float(self.root.tk.call("tk", "scaling")), 96 / 72, delta=0.01)
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        user32.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        self.assertEqual(user32.GetAwarenessFromDpiAwarenessContext(
            user32.GetThreadDpiAwarenessContext()), 2)

        class MonitorInfo(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.MonitorFromWindow.restype = wintypes.HANDLE
        user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
        monitor = user32.MonitorFromWindow(self.root.winfo_id(), 2)
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        self.assertTrue(user32.GetMonitorInfoW(monitor, ctypes.byref(info)))
        self.assertGreaterEqual(self.root.winfo_rootx(), info.rcWork.left)
        self.assertGreaterEqual(self.root.winfo_rooty(), info.rcWork.top)
        self.assertLessEqual(self.root.winfo_rootx() + self.root.winfo_width(), info.rcWork.right)
        self.assertLessEqual(self.root.winfo_rooty() + self.root.winfo_height(), info.rcWork.bottom)

    def test_tk_render_seek_and_bounded_cache(self):
        photo_threads = []
        original = __import__("PIL.ImageTk", fromlist=["PhotoImage"]).PhotoImage

        def photo(*args, **kwargs):
            photo_threads.append(threading.get_ident())
            return original(*args, **kwargs)

        with patch("comic_reader_tk.ImageTk.PhotoImage", photo):
            self.reader.open_path(self.folder / "0.png")
            self.pump_until(lambda: set(self.reader.cache) == {0, 1})
            self.reader.on_progress_changed("4")
            self.pump_until(lambda: set(self.reader.cache) == {2, 3, 4})
        self.assertEqual(self.reader.current_index, 3)
        self.assertTrue(photo_threads)
        self.assertEqual(set(photo_threads), {threading.get_ident()})
        self.assertIsNotNone(self.reader._photo)
        self.reader.change_page(1)
        self.pump_until(lambda: set(self.reader.cache) == {3, 4})
        self.reader.change_page(1)
        self.assertEqual(self.reader.current_index, 4)

    def test_old_generation_images_cannot_replace_new_source(self):
        self.reader.open_path(self.folder / "0.png")
        self.pump_until(lambda: 0 in self.reader.cache)
        generation = self.reader.generation
        self.reader.open_path(self.folder / "3.png")
        obsolete = Image.new("RGB", (1, 1))
        self.reader.worker.results.put(Result("image", generation, index=3, image=obsolete))
        self.pump_until(lambda: self.reader.current_index == 3 and 3 in self.reader.cache)
        self.assertEqual(self.reader.cache[3].size, (40, 60))
        with self.assertRaises(ValueError):
            obsolete.getpixel((0, 0))

    def test_empty_archive_skips_to_next_and_up_down_switch_archives(self):
        text = self.folder / "readme.txt"
        text.write_text("no images", encoding="utf-8")
        first = make_archive(self.folder / "1.zip", [(text, "readme.txt")])
        second = make_stored_rar(self.folder / "2.rar", "page.png",
                                 (self.folder / "0.png").read_bytes())
        self.reader.open_path(first)
        self.pump_until(lambda: self.reader.info is not None
                        and self.reader.info.path == second and 0 in self.reader.cache)
        self.reader.change_archive(1)
        self.assertEqual(self.reader.info.path, second)
        self.assertEqual(len(self.reader.cache), 1)

    def test_delete_image_mode_targets_whole_folder_after_confirmation(self):
        self.reader.open_path(self.folder / "0.png")
        self.pump_until(lambda: 0 in self.reader.cache)
        with patch("comic_reader_tk.messagebox.askyesno", return_value=False):
            with patch.object(self.reader.worker, "submit") as submit:
                self.reader.delete_current_file()
                submit.assert_not_called()
        with patch("comic_reader_tk.messagebox.askyesno", return_value=True):
            with patch("comic_reader_tk.send2trash") as trash:
                self.reader.delete_current_file()
                self.pump_until(lambda: not self.reader._deleting)
                trash.assert_called_once_with(str(self.folder))
        self.assertIsNone(self.reader.info)

    def test_config_preserves_other_keys_and_rejects_bad_last_dir(self):
        self.reader.config_path = self.folder / "reader.yaml"
        self.reader.config_path.write_text("last_dir: 'D:\\comics'\nother: 123\n", encoding="utf-8")
        self.reader.load_config()
        self.assertEqual(self.reader.initial_dir, "D:\\comics")
        self.reader.save_config()
        self.assertIn("other: 123", self.reader.config_path.read_text(encoding="utf-8"))
        self.reader.config_path.write_text("last_dir: 42\n", encoding="utf-8")
        with patch("comic_reader_tk.messagebox.showerror") as error:
            with self.assertLogs("comic_reader_tk", level="ERROR"):
                self.reader.load_config()
        error.assert_called_once()

    def test_close_does_not_block_tk_and_worker_finishes(self):
        started = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)
        original = ComicSource.read

        def read(source, index):
            started.set()
            if not release.wait(5):
                raise TimeoutError("Test did not release worker")
            return original(source, index)

        with patch.object(ComicSource, "read", read):
            self.reader.open_path(self.folder / "0.png")
            self.pump_until(started.is_set)
            with patch.object(self.reader, "save_config"):
                self.reader.close()
            self.assertTrue(self.reader._closing)
            responsive = threading.Event()
            self.root.after(0, responsive.set)
            self.root.update()
            self.assertTrue(responsive.is_set())
            self.assertTrue(self.reader.worker.thread.is_alive())
            release.set()
            self.reader.worker.thread.join(5)
        self.assertFalse(self.reader.worker.thread.is_alive())
        self.assertFalse(self.reader.cache)

    def test_bad_image_error_survives_resize_and_can_navigate_away(self):
        (self.folder / "0.png").write_bytes(b"bad image")
        with self.assertLogs("comic_reader_tk", level="ERROR"):
            self.reader.open_path(self.folder / "0.png")
            self.pump_until(lambda: 0 in self.reader.errors and 1 in self.reader.cache)
        self.reader._render_resized()
        self.assertIn("无法加载图片", self.reader.image_label.cget("text"))
        with self.assertLogs("comic_reader_tk", level="ERROR") as errors:
            self.reader.change_page(1)
            self.pump_until(lambda: bool(errors.output) and 2 in self.reader.cache)
        self.assertIsNotNone(self.reader._photo)
        self.assertEqual(self.reader.image_label.cget("text"), "")

    def test_recycle_failure_reopens_original_archive(self):
        archive = make_archive(self.folder / "book.zip",
                               [(self.folder / "0.png", "page.png")])
        self.reader.open_path(archive)
        self.pump_until(lambda: 0 in self.reader.cache)
        with patch("comic_reader_tk.messagebox.askyesno", return_value=True):
            with patch("comic_reader_tk.messagebox.showerror") as error:
                with patch("comic_reader_tk.send2trash", side_effect=OSError("cannot recycle")):
                    with self.assertLogs("comic_reader_tk", level="ERROR"):
                        self.reader.delete_current_file()
                        self.pump_until(lambda: not self.reader._deleting
                                        and self.reader.info is not None
                                        and 0 in self.reader.cache)
        error.assert_called_once()
        self.assertEqual(self.reader.info.path, archive)

    def test_recycling_archive_opens_next_mixed_format_archive(self):
        first = make_archive(self.folder / "1.zip",
                             [(self.folder / "0.png", "page.png")])
        second = make_stored_rar(self.folder / "2.rar", "page.png",
                                 (self.folder / "0.png").read_bytes())
        self.reader.open_path(first)
        self.pump_until(lambda: 0 in self.reader.cache)
        with patch("comic_reader_tk.messagebox.askyesno", return_value=True):
            with patch("comic_reader_tk.send2trash", side_effect=lambda path: Path(path).unlink()):
                self.reader.delete_current_file()
                self.pump_until(lambda: self.reader.info is not None
                                and self.reader.info.path == second and 0 in self.reader.cache)
        self.assertFalse(first.exists())


if __name__ == "__main__":
    unittest.main()
