import ctypes
import sys
import tkinter as tk
from PIL import ImageGrab, ImageTk, Image
import time
from pynput import keyboard
import pystray
import win32api, win32con, winerror, win32event
import os
import ocr
import gTTSfun
import pygame
import io
from concurrent.futures import ThreadPoolExecutor
import config

Single_mutex = None

class SnippingTool:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()

        self.canvas = None
        self.rect = None
        self.start_x = self.start_y = 0
        self.tray_icon = None
        self.listener = None
        self.mocr = ocr.MangaOcr()
        self.ocr_timer = None
        self.ocr_text_id = None
        self.ocr_bg_id = None
        self.ocr_bg_photo = None
        self.translate_text_id = None
        self.translate_bg_id = None
        self.translate_bg_photo = None
        self.fullscreen_img = None
        self.last_result = ""
        pygame.mixer.init()
        self.last_sound = None
        self.executor = ThreadPoolExecutor(max_workers=1)
    def start_snip(self):
        if self.fullscreen_img is not None:
            # 已经在截图中就结束截图
            self.on_cancel(None)
            return  
        if self.canvas:
            self.canvas.destroy()
        self.fullscreen_img = ImageGrab.grab()
        # 全屏显示窗口
        
        #self.root.attributes('-fullscreen', True)
        #self.root.attributes('-alpha', 0.3)  # 半透明
        self.root.attributes('-topmost', True)
        self.root.overrideredirect(True)  # 去掉标题栏

        # 将全屏原图转为 tkinter 可用的 PhotoImage
        self.photo = ImageTk.PhotoImage(self.fullscreen_img)
        self.root.geometry(f"{self.photo.width()}x{self.photo.height()}+0+0")

        # 创建 canvas 并显示原图作为背景
        self.canvas = tk.Canvas(self.root, width=self.photo.width(), height=self.photo.height(),
                                highlightthickness=0, cursor="cross")
        self.canvas.create_image(0, 0, anchor='nw', image=self.photo)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.root.deiconify()
        # 绑定鼠标事件
        self.canvas.bind("<ButtonPress-1>", self.on_button_press)
        self.canvas.bind("<B1-Motion>", self.on_move_press)
        self.canvas.bind("<ButtonRelease-1>", self.on_button_release)
        self.canvas.bind("<Button-3>", self.on_cancel)

    def on_button_press(self, event):
        self.start_x = event.x
        self.start_y = event.y
        self.rect = self.canvas.create_rectangle(self.start_x, self.start_y, self.start_x, self.start_y,
                                                 outline='red', width=2, dash=(4, 4))

    def on_move_press(self, event):
        cur_x, cur_y = event.x, event.y
        self.canvas.coords(self.rect, self.start_x, self.start_y, cur_x, cur_y)
        # 取消之前的定时器
        if self.ocr_timer:
            self.root.after_cancel(self.ocr_timer)
        # 启动新的定时器，500ms 后执行 OCR
        self.ocr_timer = self.root.after(500, self.perform_ocr)

    def perform_ocr(self):
        # 获取当前矩形坐标
        coords = self.canvas.coords(self.rect)
        x1, y1, x2, y2 = coords
        # 裁剪截图
        screenshot = self.fullscreen_img.crop((x1, y1, x2, y2))
        # 执行 OCR
        result = self.mocr(screenshot)
        if self.last_result == result:
            return  # 结果未变化，跳过更新
        self.last_result = result
        # 删除之前的文本和背景
        if self.ocr_text_id:
            self.canvas.delete(self.ocr_text_id)
        if self.ocr_bg_id:
            self.canvas.delete(self.ocr_bg_id)
        # 显示文本在矩形下方靠左
        text_x = x1
        text_y = y2 + 10
        # 临时创建文本获取 bbox
        temp_id = self.canvas.create_text(text_x, text_y, text=result, anchor='nw', fill='red', font=('Arial', 12))
        bbox = self.canvas.bbox(temp_id)
        self.canvas.delete(temp_id)
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        # 创建半透明灰色背景
        bg_image = Image.new('RGBA', (width, height), (0, 0, 0, 255))
        self.ocr_bg_photo = ImageTk.PhotoImage(bg_image)
        self.ocr_bg_id = self.canvas.create_image(text_x, text_y, anchor='nw', image=self.ocr_bg_photo)
        # 创建文本
        self.ocr_text_id = self.canvas.create_text(text_x, text_y, text=result, anchor='nw', fill='white', font=('Arial', 12))
        # 放到剪贴板
        self.root.clipboard_clear()
        self.root.clipboard_append(result)
        self.start_translate(result)
        print("OCR 结果：", result)
    def start_translate(self, text):
        res=self.executor.submit(self.go_translate, text)
        res.add_done_callback(self._on_translate_done)
    def _on_translate_done(self, future):
        try:
            translated_text = future.result()
            self.root.after(0, self.on_translate_done, translated_text)
        except Exception as e:
            print("翻译失败:", e)
    def on_translate_done(self,text):
        if self.fullscreen_img is None:
            return
        #取得ocr_text_id的位置，翻译结果显示在其下方左对齐
        ocr_text_id_bbox = self.canvas.bbox(self.ocr_text_id)
        text_x = ocr_text_id_bbox[0]
        text_y = ocr_text_id_bbox[3] + 12
        #删除之前的翻译文本和背景
        if self.translate_text_id:
            self.canvas.delete(self.translate_text_id)
        if self.translate_bg_id:
            self.canvas.delete(self.translate_bg_id)
        #创建临时文本获取 bbox
        temp_id = self.canvas.create_text(text_x, text_y, text=text, anchor='nw', fill='red', font=('Arial', 12))
        bbox = self.canvas.bbox(temp_id)
        self.canvas.delete(temp_id)
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        #创建半透明灰色背景
        bg_image = Image.new('RGBA', (width, height), (0, 0, 0, 255))
        self.translate_bg_photo = ImageTk.PhotoImage(bg_image)
        self.translate_bg_id = self.canvas.create_image(text_x, text_y, anchor='nw', image=self.translate_bg_photo)
        #创建翻译文本
        self.translate_text_id = self.canvas.create_text(text_x, text_y, text=text, anchor='nw', fill='white', font=('Arial', 12))
        print("翻译结果：", text)
    def go_translate(self, text):
        try:
            translated_text = gTTSfun.translate_with_api_key(text=text, target="zh-CN", api_key=config.gcloud_api_key)
            return translated_text
        except Exception as e:
            print("翻译失败:", e)
            return text
    def on_button_release(self, event):
        self.cleanup_controls()
        if self.last_result == "":
            self.perform_ocr()
        self.root.withdraw()
        if self.last_result != "":
            self.executor.submit(self.goPlaySound, self.last_result)
        self.last_result = ""
        self.fullscreen_img = None
    def goPlaySound(self, sound_text):
        try:
            fp = gTTSfun.japanese_tts(text=sound_text)
            sound = fp
            sound.seek(0)

            pygame.mixer.music.load(sound)
            pygame.mixer.music.play()
        except Exception as e:
            print("语音播放失败:", e)
    def replay_sound(self):
        try:
            pygame.mixer.music.rewind()
            pygame.mixer.music.play()
        except Exception as e:
            print("语音播放失败:", e)
    def on_replay(self, event):
        self.executor.submit(self.replay_sound)
    def on_cancel(self, event):
        self.cleanup_controls()
        self.last_result = ""
        self.root.withdraw()
        self.fullscreen_img = None
    def cleanup_controls(self):
        # 取消定时器
        if self.ocr_timer:
            self.root.after_cancel(self.ocr_timer)
            self.ocr_timer = None
        # 删除文本和背景
        if self.ocr_text_id:
            self.canvas.delete(self.ocr_text_id)
            self.ocr_text_id = None
        if self.translate_text_id:
            self.canvas.delete(self.translate_text_id)
            self.translate_text_id = None
        if self.ocr_bg_id:
            self.canvas.delete(self.ocr_bg_id)
            self.ocr_bg_id = None
            self.ocr_bg_photo = None
        if self.translate_bg_id:
            self.canvas.delete(self.translate_bg_id)
            self.translate_bg_id = None
            self.translate_bg_photo = None
    def create_tray_icon(self):
        """Create system tray icon."""
        image = Image.open("data/tray.png")
        menu = pystray.Menu(
            pystray.MenuItem("截图", self.on_tray_activate),
            pystray.MenuItem("退出", self.on_tray_exit)
        )
        self.tray_icon = pystray.Icon("SnippingTool", image, "截图工具", menu)
        self.tray_icon.run_detached()

    def on_tray_activate(self):
        """Tray menu item to start snipping."""
        self.start_snip()

    def on_tray_exit(self):
        """Tray menu item to exit the application."""
        if self.tray_icon:
            self.tray_icon.stop()
        if self.listener:
            self.listener.stop()
        self.executor.shutdown(wait=True)
        self.root.after(0, self.root.quit)
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
def enable_dpi_awareness():
    """Ensure Windows reports real pixel sizes; otherwise Tk coords and PIL screenshot diverge."""
    if not sys.platform.startswith("win"):
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-monitor DPI aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()  # Fallback for older Windows
        except Exception:
            pass
if __name__ == "__main__":
    if not check_single_instance():
        print("另一个实例已在运行。")
        sys.exit(1)
    enable_dpi_awareness()
    tool = SnippingTool()
    tool.create_tray_icon()
    def on_activate():
        tool.start_snip()
    
    def on_exit():
        tool.on_tray_exit()
    def on_replay():
        tool.on_replay(None)
    
    """listener = keyboard.GlobalHotKeys({
        '<alt>+q': on_activate,
        '<alt>+w': on_replay,
    })
    tool.listener = listener"""
    def on_press(key):
        global alt_pressed
        try:
            if key == keyboard.Key.alt or key == keyboard.Key.alt_l or key == keyboard.Key.alt_r:
                alt_pressed = True
                return  # 不 suppress，继续正常行为
            elif alt_pressed:
                if key.char == 'q':
                    print("Alt + q 触发！")
                    on_activate()
                elif key.char == 'w':
                    print("Alt + w 触发！")
                    on_replay()
                alt_pressed = False  # 重置（简化，按需调整）
        except AttributeError:
            pass
        return True  # 允许事件通过
    alt_pressed = False
    def on_release(key):
        global alt_pressed
        if key == keyboard.Key.alt or key == keyboard.Key.alt_l or key == keyboard.Key.alt_r:
            alt_pressed = False
    listener=keyboard.Listener(on_press=on_press, on_release=on_release, suppress=False)
    listener.start()
    
    tool.root.mainloop()