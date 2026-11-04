import io
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

from PIL import Image

from screen_tk import LatestWorker, SnippingTool, WindowsAudio


class LatestWorkerTests(unittest.TestCase):
    def setUp(self):
        self.cleanup = Mock()
        self.worker = LatestWorker(self.cleanup)
        self.release = threading.Event()

    def tearDown(self):
        self.release.set()
        self.worker.shutdown()

    def block_worker(self, kind="block"):
        started = threading.Event()

        def block():
            started.set()
            if not self.release.wait(5):
                raise TimeoutError("Test did not release worker")
            return "running"

        future = self.worker.submit(kind, block)
        self.assertTrue(started.wait(5))
        return future

    def test_pending_same_kind_is_cancelled_and_latest_goes_to_queue_tail(self):
        running = self.block_worker()
        executed = []
        old = self.worker.submit("ocr", lambda: executed.append("old"))
        other = self.worker.submit("translate", lambda: executed.append("translate"))
        latest = self.worker.submit("ocr", lambda: executed.append("latest"))
        self.assertTrue(old.cancelled())
        self.release.set()
        self.assertEqual(running.result(5), "running")
        other.result(5)
        latest.result(5)
        self.assertEqual(executed, ["translate", "latest"])

    def test_running_job_is_not_cancelled_by_same_kind(self):
        running = self.block_worker("ocr")
        old = self.worker.submit("ocr", lambda: "old")
        latest = self.worker.submit("ocr", lambda: "latest")
        self.assertFalse(running.cancelled())
        self.assertTrue(running.running())
        self.assertTrue(old.cancelled())
        self.release.set()
        self.assertEqual(running.result(5), "running")
        self.assertEqual(latest.result(5), "latest")

    def test_cancel_only_removes_requested_pending_kind(self):
        running = self.block_worker()
        cancelled = self.worker.submit("ocr", lambda: self.fail("Cancelled job ran"))
        kept = self.worker.submit("audio", lambda: "audio")
        self.worker.cancel("ocr")
        self.assertTrue(cancelled.cancelled())
        self.release.set()
        running.result(5)
        self.assertEqual(kept.result(5), "audio")

    def test_caller_cancelled_future_does_not_run(self):
        self.block_worker()
        cancelled = self.worker.submit("ocr", lambda: self.fail("Cancelled job ran"))
        self.assertTrue(cancelled.cancel())
        final = self.worker.submit("translate", lambda: "ok")
        self.release.set()
        self.assertEqual(final.result(5), "ok")

    def test_errors_are_reported_and_worker_continues_on_single_thread(self):
        def fail():
            raise ValueError("expected failure")

        failed = self.worker.submit("ocr", fail)
        with self.assertRaisesRegex(ValueError, "expected failure"):
            failed.result(5)
        ids = [self.worker.submit("ocr", threading.get_ident).result(5) for _ in range(3)]
        self.assertEqual(ids, [self.worker.thread.ident] * 3)
        self.assertNotEqual(ids[0], threading.get_ident())

    def test_shutdown_cancels_pending_and_finishes_running_before_cleanup(self):
        running = self.block_worker()
        pending = self.worker.submit("ocr", lambda: self.fail("Shutdown job ran"))
        self.worker.shutdown(wait=False)
        self.assertTrue(pending.cancelled())
        self.cleanup.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            self.worker.submit("ocr", lambda: None)
        self.release.set()
        running.result(5)
        self.worker.shutdown()
        self.cleanup.assert_called_once()
        self.assertFalse(self.worker.thread.is_alive())

    def test_concurrent_producers_keep_only_one_pending_job_per_kind(self):
        self.block_worker()
        produced = queue.SimpleQueue()
        executed = []

        def produce():
            for index in range(50):
                produced.put(self.worker.submit(
                    "ocr", lambda value=index: executed.append(value),
                ))

        producers = [threading.Thread(target=produce) for _ in range(4)]
        for producer in producers:
            producer.start()
        for producer in producers:
            producer.join()
        final = self.worker.submit("ocr", lambda: executed.append("final"))
        while not produced.empty():
            self.assertTrue(produced.get().cancelled())
        self.release.set()
        final.result(5)
        self.assertEqual(executed, ["final"])


class TkSnippingToolTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        config_path = Path(self.directory.name) / "conf.yaml"
        config_path.write_text("{}\n", encoding="utf-8")
        self.root = tk.Tk()
        self.root.withdraw()
        self.tool = SnippingTool(self.root, config_path, integrations=False)
        self.tool._configure = Mock()
        self.tool._load_ocr_model = Mock()
        self.tool.play_sound = Mock()
        self.tool.gtts = SimpleNamespace(
            session=None, lookup_translation_cache=Mock(return_value=None),
        )
        self.release = threading.Event()

    def tearDown(self):
        self.release.set()
        if not self.tool.closed:
            self.tool.exit_app()
        self.tool.worker.shutdown()
        self.directory.cleanup()

    def pump_until(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.005)
        self.assertTrue(predicate(), "Tk did not receive the expected completion")

    def show_selection(self):
        self.tool.overlay.show_capture(Image.new("RGB", (100, 100), "white"))
        self.root.update()
        self.tool.overlay.press(SimpleNamespace(x=10, y=10))
        self.tool.overlay.move(SimpleNamespace(x=50, y=50))

    def test_completions_and_ui_run_only_on_main_thread(self):
        received = []
        self.tool._submit(
            "ocr", threading.get_ident,
            lambda worker_id: received.append((worker_id, threading.get_ident())),
        )
        self.pump_until(lambda: bool(received))
        self.assertEqual(received, [(self.tool.worker.thread.ident, threading.get_ident())])

    def test_release_before_ocr_completion_still_copies_and_reads_once(self):
        started = threading.Event()

        def ocr(image):
            self.assertEqual(image.size, (40, 40))
            started.set()
            self.assertTrue(self.release.wait(5))
            return "日本語"

        self.tool.mocr = ocr
        self.show_selection()
        self.tool.overlay.perform_ocr()
        self.assertTrue(started.wait(5))
        self.tool.overlay.release(SimpleNamespace(x=50, y=50))
        self.assertFalse(self.tool.overlay.visible)
        self.assertIsNone(self.tool.overlay.original_image)
        self.release.set()
        self.pump_until(lambda: self.tool.play_sound.called)
        self.tool.play_sound.assert_called_once_with("日本語")
        self.assertEqual(self.root.clipboard_get(), "日本語")

    def test_old_ocr_result_cannot_overwrite_new_selection(self):
        started = threading.Event()
        calls = []

        def ocr(image):
            calls.append(image.size)
            if len(calls) == 1:
                started.set()
                self.assertTrue(self.release.wait(5))
                return "古い"
            return "新しい"

        self.tool.mocr = ocr
        self.show_selection()
        self.tool.overlay.perform_ocr()
        self.assertTrue(started.wait(5))
        self.tool.overlay.move(SimpleNamespace(x=70, y=70))
        self.tool.overlay.release(SimpleNamespace(x=70, y=70))
        self.release.set()
        self.pump_until(lambda: self.tool.play_sound.called)
        self.assertEqual(calls, [(40, 40), (60, 60)])
        self.tool.play_sound.assert_called_once_with("新しい")
        self.assertEqual(self.tool.overlay.ocr_result, "新しい")
        self.assertEqual(self.root.clipboard_get(), "新しい")

    def test_right_click_cancels_result_and_speech(self):
        started = threading.Event()

        def ocr(_image):
            started.set()
            self.assertTrue(self.release.wait(5))
            return "discard"

        self.tool.mocr = ocr
        self.show_selection()
        self.tool.overlay.perform_ocr()
        self.assertTrue(started.wait(5))
        self.tool.overlay.close_overlay()
        self.release.set()
        marker = []
        self.tool._submit("marker", lambda: None, lambda _: marker.append(True))
        self.pump_until(lambda: bool(marker))
        self.tool.play_sound.assert_not_called()
        self.assertEqual(self.tool.overlay.ocr_result, "")

    def test_running_translation_cannot_update_closed_overlay(self):
        started = threading.Event()

        def translate(_text, _config):
            started.set()
            self.assertTrue(self.release.wait(5))
            return "stale translation"

        self.tool.go_translate = translate
        self.show_selection()
        self.tool.overlay._cancel_timer()
        self.tool.start_translate("text")
        self.pump_until(started.is_set)
        self.tool.overlay.close_overlay()
        self.release.set()
        marker = []
        self.tool._submit("marker", lambda: None, lambda _: marker.append(True))
        self.pump_until(lambda: bool(marker))
        self.assertEqual(self.tool.overlay.translate_result, "")

    def test_capture_runs_in_worker_and_toggle_discards_running_capture(self):
        started = threading.Event()
        calls = []

        def grab():
            calls.append(threading.get_ident())
            started.set()
            self.assertTrue(self.release.wait(5))
            return Image.new("RGB", (100, 100))

        with patch("screen_tk.ImageGrab.grab", grab):
            self.tool.start_snip()
            self.assertTrue(started.wait(5))
            self.tool.start_snip()
            self.release.set()
            marker = []
            self.tool._submit("marker", lambda: None, lambda _: marker.append(True))
            self.pump_until(lambda: bool(marker))
        self.assertFalse(self.tool.overlay.visible)
        self.assertFalse(self.tool.capturing)
        self.assertEqual(calls, [self.tool.worker.thread.ident])

    def test_background_errors_are_visible_not_successful_ocr(self):
        self.show_selection()
        self.tool.mocr = Mock(side_effect=ValueError("bad OCR"))
        self.tool.message_overlay.show_message = Mock()
        self.tool.overlay.perform_ocr()
        self.pump_until(lambda: self.tool.message_overlay.show_message.called)
        self.assertIn("bad OCR", self.tool.message_overlay.show_message.call_args.args[0])
        self.assertEqual(self.tool.overlay.ocr_result, "")
        self.tool.play_sound.assert_not_called()

    def test_local_model_reload_and_server_switch_are_worker_operations(self):
        load = SnippingTool._load_ocr_model.__get__(self.tool)
        model = Mock()
        fake_module = SimpleNamespace(MangaOcrCPT=model)
        with patch.dict(sys.modules, {"manga_ocr_cpt_lib": fake_module}):
            config = {"ocr": {"local_model": "owner/custom"}}
            self.tool.worker.submit("config", lambda: load(config)).result(5)
            model.assert_called_once_with("owner/custom", local_files_only=True)
            self.tool.worker.submit("config", lambda: load(config)).result(5)
            self.assertEqual(model.call_count, 1)
            config["ocr"]["local_model"] = "flex"
            self.tool.worker.submit("config", lambda: load(config)).result(5)
            model.assert_called_with("flex", local_files_only=True)
            config["ocr"]["server_url"] = "http://ocr.local"
            self.tool.worker.submit("config", lambda: load(config)).result(5)
            self.assertIsNone(self.tool.mocr)
            self.assertIsNone(self.tool._mocr_model)

    def test_remote_ocr_checks_status_and_response_shape(self):
        self.tool.mocr = None
        response = Mock()
        response.json.return_value = {"result": "remote"}
        config = {"ocr": {"server_url": "http://ocr.local"}}
        with patch("httpx.post", return_value=response) as post:
            result = self.tool.worker.submit(
                "ocr", lambda: self.tool.go_ocr(Image.new("RGB", (20, 20)), config),
            ).result(5)
            self.assertEqual(result, "remote")
            response.raise_for_status.assert_called_once()
            self.assertTrue(post.call_args.kwargs["content"])
            response.json.return_value = {"result": None}
            with self.assertRaisesRegex(ValueError, "字符串"):
                self.tool.worker.submit(
                    "ocr", lambda: self.tool.go_ocr(Image.new("RGB", (20, 20)), config),
                ).result(5)

    def test_settings_preserve_unknown_config_and_apply_in_background(self):
        self.tool.config = {"custom": {"keep": True}}
        self.tool.OcrConfigChange = Mock()
        self.tool.open_settings()
        dialog = self.tool.settings
        dialog.values["voicevox", "speaker_id"].set("20")
        dialog.values["voicevox", "speed_scale"].set("0.9")
        dialog.values["ocr", "server_url"].set("http://ocr.local")
        dialog.save()
        self.assertIsNone(self.tool.settings)
        self.assertEqual(self.tool.config["custom"], {"keep": True})
        self.assertEqual(self.tool.config["voicevox"]["speaker_id"], 20)
        self.assertEqual(self.tool.load_config(), self.tool.config)
        self.tool.OcrConfigChange.assert_called_once()

    def test_cached_translation_is_displayed_without_debounce_or_network(self):
        self.show_selection()
        self.tool.gtts.lookup_translation_cache.return_value = "缓存"
        self.tool.go_translate = Mock()
        self.tool.start_translate("text")
        self.pump_until(lambda: self.tool.overlay.translate_result == "缓存")
        self.assertIsNone(self.tool.translation_timer)
        self.tool.go_translate.assert_not_called()

    def test_manual_ocr_cancels_pending_debounce_timer(self):
        self.tool.mocr = Mock(return_value="text")
        self.show_selection()
        timer = self.tool.overlay.timer
        self.assertIn(timer, self.root.tk.call("after", "info"))
        self.tool.overlay.perform_ocr()
        self.assertNotIn(timer, self.root.tk.call("after", "info"))
        self.assertIsNone(self.tool.overlay.timer)

    def test_translation_fallback_order_and_empty_results(self):
        attempts = []

        def ali(text):
            attempts.append("ali")
            raise RuntimeError("ali unavailable")

        def google(**kwargs):
            attempts.append("google")
            return ""

        def local(text):
            attempts.append("local")
            return "翻译"

        self.tool.gtts.translate_with_ali = ali
        self.tool.gtts.translate_with_api_key = google
        self.tool.gtts.translate_with_local_model = local
        config = {
            "key": {"ali_key": "test", "gcloud": "test"},
            "translate": {"local_model": "http://local"},
        }
        result = self.tool.worker.submit(
            "translate", lambda: self.tool.go_translate("text", config),
        ).result(5)
        self.assertEqual(result, "翻译")
        self.assertEqual(attempts, ["ali", "google", "local"])
        self.tool.gtts.translate_with_local_model = Mock(side_effect=RuntimeError("local unavailable"))
        with self.assertRaisesRegex(RuntimeError, "local unavailable"):
            self.tool.worker.submit(
                "translate", lambda: self.tool.go_translate("text", config),
            ).result(5)

    def test_tts_passes_voicevox_wav_to_worker_owned_player(self):
        wav_stream = io.BytesIO()
        with wave.open(wav_stream, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(24000)
            wav.writeframes(b"\x00\x00" * 10)
        self.tool.voicevox = SimpleNamespace(
            japanese_tts=Mock(return_value=wav_stream),
            stop_voicevox=Mock(),
        )
        self.tool.audio = Mock()
        config = {"voicevox": {"speaker_id": "20", "speed_scale": "0.9"}}
        self.tool.worker.submit(
            "audio", lambda: self.tool.goPlaySound("text", config),
        ).result(5)
        self.tool.voicevox.japanese_tts.assert_called_once_with(
            text="text", speaker=20, speed_scale=0.9, output_sampling_rate=24000,
        )
        self.tool.audio.play.assert_called_once_with(wav_stream.getvalue())

    def test_tts_does_not_fall_back_to_mp3(self):
        config = {"voicevox": {"speaker_id": "20", "speed_scale": "0.9"}}
        google_tts = Mock()
        self.tool._service_config = config
        self.tool.voicevox = SimpleNamespace(
            japanese_tts=Mock(return_value=None),
            stop_voicevox=Mock(),
        )
        self.tool.gtts = SimpleNamespace(japanese_tts=google_tts, session=None)
        with self.assertRaisesRegex(RuntimeError, "VOICEVOX 语音合成失败"):
            self.tool.goPlaySound("text", config)
        google_tts.assert_not_called()

    def test_exit_does_not_wait_for_running_job_or_deliver_to_destroyed_tk(self):
        started = threading.Event()
        received = Mock()

        def block():
            started.set()
            self.assertTrue(self.release.wait(5))

        future = self.tool._submit("ocr", block, received)
        self.assertTrue(started.wait(5))
        self.tool.exit_app()
        self.assertTrue(self.tool.worker.thread.is_alive())
        self.release.set()
        future.result(5)
        self.tool.worker.shutdown()
        received.assert_not_called()


class WindowsAudioTests(unittest.TestCase):
    @patch("screen_tk.sd.play")
    @patch("screen_tk.sd.stop")
    def test_silent_wav_play_replay_and_close_without_file_io(self, stop, play):
        stream = io.BytesIO()
        with wave.open(stream, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(24000)
            wav.writeframes(b"\x00\x00" * 2400)
        audio = WindowsAudio()
        with patch("pathlib.Path.write_bytes") as write_bytes:
            audio.play(stream.getvalue())
            audio.replay()
            self.assertEqual(audio.last_audio_data, stream.getvalue())
            self.assertEqual(play.call_count, 2)
            self.assertEqual(play.call_args.args[1], 24000)
            self.assertEqual(play.call_args.args[0].shape, (2400, 1))
            audio.close()
            write_bytes.assert_not_called()
        self.assertEqual(audio.last_audio_data, b"")
        self.assertEqual(stop.call_count, 3)

    @patch("screen_tk.sd.play")
    def test_rejects_empty_and_non_wav_audio(self, play):
        audio = WindowsAudio()
        with self.assertRaisesRegex(ValueError, "为空"):
            audio.play(b"")
        with self.assertRaisesRegex(RuntimeError, "暂无"):
            audio.replay()
        with self.assertRaisesRegex(ValueError, "有效的 WAV"):
            audio.play(b"ID3 fake mp3")
        play.assert_not_called()


if __name__ == "__main__":
    unittest.main()
