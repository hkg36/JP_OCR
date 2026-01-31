# Win32 dependencies for mutex/single instance check
import win32api, winerror, win32event
import sys
def check_single_instance():
    """Check if another instance is running and prevent multiple instances."""
    global Single_mutex
    MUTEX_NAME = "Global\\SnippingTool_SingleInstance_v1.0" 
    try:
        Single_mutex = win32event.CreateMutex(None, False, MUTEX_NAME)
        if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
            return False
    except Exception as ae:
        print("检查单实例失败:", ae)
        return False
    return True
if __name__ == "__main__":
    if not check_single_instance():
        print("另一个实例已在运行。")
        sys.exit(1)
import os
import ctypes
import traceback
import datetime
import io
from concurrent.futures import ThreadPoolExecutor

# PySide6 imports
from PySide6.QtWidgets import (QApplication, QWidget, QSystemTrayIcon, QMenu, 
                               QToolTip)
from PySide6.QtCore import (Qt, QTimer, Signal, QObject, QPoint, QRect, 
                            QSize, Slot)
from PySide6.QtGui import (QPainter, QPixmap, QColor, QPen, QFont, QAction, 
                           QIcon, QImage, QCursor, QGuiApplication, QClipboard)

from PIL import ImageGrab, Image
from pynput import keyboard
import pygame
import ocr
import gTTSfun
import config

# Fix pythonw output issues
if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w')

fontname = "微软雅黑"
Single_mutex = None

class Signaller(QObject):
    """Signal bridge for non-GUI threads."""
    start_snip_signal = Signal()
    replay_sound_signal = Signal()
    translation_done_signal = Signal(str)

class SnippingOverlay(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        
        # Window setup
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_OpaquePaintEvent) # 防止窗口显示时先绘制白色背景
        self.setMouseTracking(True)
        # self.setAttribute(Qt.WA_TranslucentBackground) # Not needed as we draw full screenshot
        
        self.original_image = None # PIL Image
        self.original_pixmap = None # QPixmap
        
        self.start_pos = QPoint()
        self.end_pos = QPoint()
        self.is_selecting = False
        self.rect_selection = QRect()
        
        self.ocr_result = ""
        self.translate_result = ""
        
        # Debounce timer for OCR
        self.ocr_timer = QTimer(self)
        self.ocr_timer.setSingleShot(True)
        self.ocr_timer.setInterval(500)
        self.ocr_timer.timeout.connect(self.perform_ocr)

    def start_capture(self):
        if self.isVisible():
            self.close_overlay()
            return

        # Grab screenshot using PIL (consistent with original logic)
        try:
            self.original_image = ImageGrab.grab()
        except OSError:
            # Fallback if grab fails
            self.close_overlay()
            return
            
        # Get device pixel ratio for correct scaling
        screen = QApplication.primaryScreen()
        dpr = screen.devicePixelRatio()

        # Convert PIL to QPixmap
        self.original_pixmap = self.pil2pixmap(self.original_image)
        self.original_pixmap.setDevicePixelRatio(dpr)
        
        # Setup window geometry to cover the captured area (Logical pixels)
        self.setGeometry(0, 0, int(self.original_image.width / dpr), int(self.original_image.height / dpr))
        
        # Reset state
        self.start_pos = QPoint()
        self.end_pos = QPoint()
        self.is_selecting = False
        self.ocr_result = ""
        self.translate_result = ""
        self.rect_selection = QRect()
        
        self.show()
        self.setCursor(Qt.CrossCursor)
        self.activateWindow()

    def pil2pixmap(self, im):
        if im.mode == "RGB":
            data = im.tobytes("raw", "RGB")
            qim = QImage(data, im.size[0], im.size[1], QImage.Format_RGB888)
        elif im.mode == "RGBA":
            data = im.tobytes("raw", "RGBA")
            qim = QImage(data, im.size[0], im.size[1], QImage.Format_RGBA888)
        else:
             # Fallback
             buf = io.BytesIO()
             im.save(buf, format="PNG")
             qim = QImage.fromData(buf.getvalue())
        
        return QPixmap.fromImage(qim)

    def paintEvent(self, event):
        if not self.original_pixmap:
            return
            
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self.original_pixmap)
        
        # Draw selection rectangle
        if self.is_selecting or not self.rect_selection.isNull():
            pen = QPen(Qt.red, 2, Qt.DashLine)
            painter.setPen(pen)
            painter.drawRect(self.rect_selection)
            
            # Draw Results
            self.draw_overlays(painter)

    def draw_overlays(self, painter):
        if not self.rect_selection.isValid() or (self.rect_selection.width() < 10 and self.rect_selection.height() < 10):
            return

        font = QFont(fontname, 14)
        painter.setFont(font)
        
        # Draw below the selection rectangle
        x_base = self.rect_selection.left()
        y_base = self.rect_selection.bottom() + 10
        max_w = max(100, self.width() - x_base) 
        
        last_y = y_base
        
        # OCR Text
        if self.ocr_result:
            rect = painter.boundingRect(QRect(x_base, last_y, max_w, 0), Qt.TextWordWrap | Qt.AlignLeft, self.ocr_result)
            painter.fillRect(rect, QColor(0, 0, 0, 255))
            painter.setPen(Qt.white)
            painter.drawText(rect, Qt.TextWordWrap | Qt.AlignLeft, self.ocr_result)
            last_y = rect.bottom() + 3
            
        # Translation Text
        if self.translate_result:
            rect = painter.boundingRect(QRect(x_base, last_y, max_w, 0), Qt.TextWordWrap | Qt.AlignLeft, self.translate_result)
            painter.fillRect(rect, QColor(0, 0, 0, 255))
            painter.setPen(Qt.white)
            painter.drawText(rect, Qt.TextWordWrap | Qt.AlignLeft, self.translate_result)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.start_pos = event.pos()
            self.end_pos = event.pos()
            self.is_selecting = True
            self.rect_selection = QRect(self.start_pos, self.end_pos)
            
            # Clear previous results
            self.ocr_result = ""
            self.translate_result = ""
            if self.ocr_timer.isActive():
                self.ocr_timer.stop()
            self.update()
        elif event.button() == Qt.RightButton:
            self.close_overlay()

    def mouseMoveEvent(self, event):
        if self.is_selecting:
            self.end_pos = event.pos()
            self.rect_selection = QRect(self.start_pos, self.end_pos).normalized()
            self.update()
            
            # Restart timer for debounce
            self.ocr_timer.start()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.is_selecting = False
            self.end_pos = event.pos()
            self.rect_selection = QRect(self.start_pos, self.end_pos).normalized()
            
            # If timer is running, stop it and run OCR immediately
            if self.ocr_timer.isActive():
                self.ocr_timer.stop()
                self.perform_ocr()
            elif not self.ocr_result:
                # If no result yet, run it
                self.perform_ocr()
                
            # As per original behavior: close on release, play sound
            self.close_overlay()
            if self.ocr_result:
                 self.controller.play_sound(self.ocr_result)

    def perform_ocr(self):
        if self.rect_selection.width() < 10 or self.rect_selection.height() < 10:
            return
            
        # Scale to physical pixels for cropping
        dpr = self.devicePixelRatio()
        rect = self.rect_selection
        
        x = int(rect.x() * dpr)
        y = int(rect.y() * dpr)
        w = int(rect.width() * dpr)
        h = int(rect.height() * dpr)
        
        try:
            crop = self.original_image.crop((x, y, x + w, y + h))
            
            # Run OCR (blocking main thread briefly)
            result = self.controller.mocr(crop)
            self.ocr_result = result
            print("OCR Result:", result)
            
            # Copy to clipboard
            QApplication.clipboard().setText(result)
            
            self.update()
            
            # Start translation
            self.controller.start_translate(result)
            
        except Exception as e:
            print("OCR Error:", e)

    def set_translation(self, text):
        self.translate_result = text
        self.update()

    def close_overlay(self):
        self.hide()
        # Clear large images to free memory
        self.original_image = None
        self.original_pixmap = None

class SnippingTool(QObject):
    def __init__(self):
        super().__init__()
        
        # Initialize Logic
        self.mocr = ocr.MangaOcr(force_cpu=True)
        pygame.mixer.init()
        self.executor = ThreadPoolExecutor(max_workers=1)
        
        # Signal bridge
        self.signaller = Signaller()
        self.signaller.start_snip_signal.connect(self.start_snip)
        self.signaller.replay_sound_signal.connect(self.replay_sound)
        self.signaller.translation_done_signal.connect(self.on_translate_done)
        
        # UI Overlay
        self.overlay = SnippingOverlay(self)
        
        # System Tray Icon
        self.tray_icon = QSystemTrayIcon(self)
        if os.path.exists("data/tray.png"):
             self.tray_icon.setIcon(QIcon("data/tray.png"))
        else:
             # Fallback icon if file missing
             pix = QPixmap(16, 16)
             pix.fill(Qt.blue)
             self.tray_icon.setIcon(QIcon(pix))
        
        tray_menu = QMenu()
        action_snip = QAction("截图", self)
        action_snip.triggered.connect(self.start_snip)
        tray_menu.addAction(action_snip)
        
        action_exit = QAction("退出", self)
        action_exit.triggered.connect(self.exit_app)
        tray_menu.addAction(action_exit)
        
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.show()
        # Tray left click to capture
        self.tray_icon.activated.connect(self.on_tray_activated)

        # Keyboard Listener
        self.listener = None
        self.alt_pressed = False
        self.start_listener()

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.start_snip()

    @Slot()
    def start_snip(self):
        self.overlay.start_capture()
        
    def start_translate(self, text):
        self.executor.submit(self.go_translate, text)
        
    def go_translate(self, text):
        try:
            # Running in thread
            translated = gTTSfun.translate_with_api_key(text=text, target="zh-CN", api_key=config.gcloud_api_key)
            self.signaller.translation_done_signal.emit(translated)
        except Exception as e:
            print("Translation failed:", e)
            self.signaller.translation_done_signal.emit(f"翻译失败: {str(e)}")

    @Slot(str)
    def on_translate_done(self, text):
        print("翻译结果：", text)
        if self.overlay.isVisible():
            self.overlay.set_translation(text)

    def play_sound(self, text):
        self.executor.submit(self.goPlaySound, text)
        
    def goPlaySound(self, sound_text):
        try:
            fp = gTTSfun.japanese_tts(text=sound_text)
            sound = fp
            sound.seek(0)
            pygame.mixer.music.load(sound)
            pygame.mixer.music.play()
        except Exception as e:
            print("语音播放失败:", e)

    @Slot()
    def replay_sound(self):
        try:
            pygame.mixer.music.rewind()
            pygame.mixer.music.play()
        except Exception as e:
            print("语音播放失败:", e)

    def start_listener(self):
        def on_press(key):
            try:
                if key == keyboard.Key.alt or key == keyboard.Key.alt_l or key == keyboard.Key.alt_r:
                    self.alt_pressed = True
                    return
                elif self.alt_pressed:
                    if hasattr(key, 'char'):
                        if key.char == 'q':
                            print("Alt + q 触发！")
                            self.signaller.start_snip_signal.emit()
                        elif key.char == 'w':
                            print("Alt + w 触发！")
                            self.signaller.replay_sound_signal.emit()
                    self.alt_pressed = False
            except AttributeError:
                pass

        def on_release(key):
            if key == keyboard.Key.alt or key == keyboard.Key.alt_l or key == keyboard.Key.alt_r:
                self.alt_pressed = False

        self.listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        self.listener.start()

    def exit_app(self):
        if self.listener:
            self.listener.stop()
        self.executor.shutdown(wait=False)
        QApplication.quit()

def enable_dpi_awareness():
    """Ensure Windows reports real pixel sizes; otherwise Qt and PIL coords diverge."""
    if not sys.platform.startswith("win"):
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-monitor DPI aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()  # Fallback
        except Exception:
            pass

if __name__ == "__main__":
    enable_dpi_awareness()
    
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False) # Important for tray-only apps

    try:
        tool = SnippingTool()
        sys.exit(app.exec())
    except Exception as e:
        print("\n" + "="*60)
        print("发生未捕获异常！")
        print(traceback.format_exc())
        print("="*60)
        with open("error.log", "a", encoding="utf-8") as f:
            f.write(f"\n{datetime.datetime.now()}\n{traceback.format_exc()}\n")
