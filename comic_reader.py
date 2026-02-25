import sys
import os
import time
import zipfile
import yaml
import natsort as ns
import math
from PySide6.QtWidgets import (QApplication, QMainWindow, QScrollArea, QWidget, 
                               QVBoxLayout, QLabel, QFileDialog, QSizePolicy, QMenu, QProgressBar)
from PySide6.QtGui import QPixmap, QAction, QKeyEvent, QWheelEvent, QMouseEvent, QCursor
from PySide6.QtCore import Qt, QTimer, QEvent, QFile

class ComicReader(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("漫画阅读器")
        
        # 去掉标题栏
        self.setWindowFlags(Qt.FramelessWindowHint)
        
        # 状态变量
        self.pixmap_cache = {}  # 缓存 QPixmap {index: QPixmap}
        self.image_files = []   # 所有图片文件名列表
        self.current_page_index = 0
        self.zip_file_list = []
        self.current_zip_index = -1
        self.current_zip = None
        self.current_folder = None
        
        # 文件夹模式状态变量
        self.is_folder_mode = False
        self.folder_image_files = []
        self.current_folder_page_index = 0
        self.folder_pixmap_cache = {}
        
        self.last_wheel_time = 0  # 上次滚轮翻页的时间
        self.scroll_start_time = 0  # 连续滚动开始时间
        
        # 加载配置
        self.config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reader.yaml")
        self.config = {}
        self.initial_dir = "E:/baks"
        if sys.platform.startswith('linux'):
            self.initial_dir = os.path.expanduser('~/Downloads')
            
        self.load_config()

        # 初始内容
        self.image_label = QLabel("请右键点击 -> 打开 ZIP 或 图片 加载漫画")
        self.image_label.setAlignment(Qt.AlignCenter)
        # 允许 Label 调整大小
        self.image_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.setCentralWidget(self.image_label)

        # 文件名显示 Label
        self.filename_label = QLabel(self)
        self.filename_label.setFixedWidth(300)
        self.filename_label.setWordWrap(True)
        self.filename_label.setStyleSheet("QLabel { background-color: rgba(0, 0, 0, 160); color: white; padding: 4px; border-radius: 4px; font-size: 14px; }")
        self.filename_label.move(10, 10)
        self.filename_label.hide()

        # 进度条
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setFixedWidth(300)
        self.progress_bar.setFixedHeight(2)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: none;
                background-color: rgba(0, 0, 0, 160);
            }
            QProgressBar::chunk {
                background-color: rgba(255, 100, 100, 200); 
            }
        """)
        self.progress_bar.hide()

        # 启动时最大化
        self.showMaximized()

    def wheelEvent(self, event: QWheelEvent):
        self.handle_wheel_event(event)
        event.accept()

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        
        open_action = QAction("打开 ZIP 或 图片", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.open_file_dialog)
        menu.addAction(open_action)

        # 添加删除项，效果同按下 Del 键
        delete_action = QAction("删除", self)
        delete_action.triggered.connect(self.delete_current_file)
        # 仅在有文件列表且索引有效时启用
        if self.is_folder_mode:
            delete_action.setEnabled(bool(self.folder_image_files) and self.current_folder_page_index >= 0)
        else:
            delete_action.setEnabled(bool(self.zip_file_list) and self.current_zip_index >= 0)
        menu.addAction(delete_action)

        exit_action = QAction("退出", self)
        exit_action.triggered.connect(self.close)
        menu.addAction(exit_action)

        menu.exec(event.globalPos())

    def closeEvent(self, event):
        self.save_config()
        self.cleanup()
        super().closeEvent(event)

    def load_config(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    self.config = yaml.safe_load(f) or {}
                    if 'last_dir' in self.config:
                        self.initial_dir = self.config['last_dir']
            except Exception as e:
                print(f"读取配合失败: {e}")

    def save_config(self):
        try:
            self.config['last_dir'] = self.initial_dir
            with open(self.config_path, 'w', encoding='utf-8') as f:
                yaml.safe_dump(self.config, f)
        except Exception as e:
            print(f"保存配置失败: {e}")

    def resizeEvent(self, event):
        if self.is_folder_mode:
            self.show_current_folder_page()
        else:
            self.show_current_page()
        self.filename_label.raise_()
        self.progress_bar.raise_()
        super().resizeEvent(event)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MiddleButton:
            self.open_file_dialog()
            event.accept()
        elif event.button() == Qt.LeftButton:
            if self.is_folder_mode:
                if self.folder_image_files:
                    self.next_page()
                else:
                    self.open_file_dialog()
            else:
                if self.current_zip:
                    self.next_page()
                else:
                    self.open_file_dialog()
            event.accept()
        else:
            super().mousePressEvent(event)

    def cleanup(self):
        self.pixmap_cache = {}
        self.image_files = []
        if self.current_zip:
            self.current_zip.close()
            self.current_zip = None

    def cleanup_folder(self):
        self.folder_pixmap_cache = {}
        self.folder_image_files = []

    def delete_current_file(self):
        if self.is_folder_mode:
            self.delete_current_folder_image()
        else:
            self.delete_current_zip_file()

    def delete_current_folder_image(self):
        if not self.folder_image_files:
            return

        if self.current_folder_page_index < 0 or self.current_folder_page_index >= len(self.folder_image_files):
            return

        file_to_delete = self.folder_image_files[self.current_folder_page_index]
        
        # Remove from cache
        if self.current_folder_page_index in self.folder_pixmap_cache:
            del self.folder_pixmap_cache[self.current_folder_page_index]

        try:
            if not QFile.moveToTrash(file_to_delete):
                print(f"移动到回收站失败: {file_to_delete}")
                return

            print(f"已移动到回收站: {file_to_delete}")
            
            del self.folder_image_files[self.current_folder_page_index]
            
            # Rebuild cache keys because indices changed
            new_cache = {}
            for idx, pixmap in self.folder_pixmap_cache.items():
                if idx > self.current_folder_page_index:
                    new_cache[idx - 1] = pixmap
                elif idx < self.current_folder_page_index:
                    new_cache[idx] = pixmap
            self.folder_pixmap_cache = new_cache

            if not self.folder_image_files:
                self.current_folder_page_index = -1
                self.image_label.setText("没有图片了")
                self.image_label.clear()
                self.filename_label.hide()
                self.progress_bar.hide()
            else:
                if self.current_folder_page_index >= len(self.folder_image_files):
                    self.current_folder_page_index = len(self.folder_image_files) - 1
                
                self.show_current_folder_page()

        except Exception as e:
            print(f"删除失败: {e}")

    def delete_current_zip_file(self):
        if not self.zip_file_list:
            return

        if self.current_zip_index < 0 or self.current_zip_index >= len(self.zip_file_list):
            return

        file_to_delete = self.zip_file_list[self.current_zip_index]
        self.cleanup() # 关闭文件句柄

        try:
            # os.remove(file_to_delete)
            # 使用 QFile.moveToTrash 移动到回收站
            if not QFile.moveToTrash(file_to_delete):
                print(f"移动到回收站失败: {file_to_delete}")
                # 恢复加载
                if os.path.exists(file_to_delete):
                     self.load_zip(file_to_delete)
                return

            print(f"已移动到回收站: {file_to_delete}")
            
            # 更新列表
            del self.zip_file_list[self.current_zip_index]
            
            # 计算新的索引
            if not self.zip_file_list:
                # 列表空了，尝试刷新文件夹
                found_new = False
                if self.current_folder and os.path.exists(self.current_folder):
                    try:
                        files = [os.path.join(self.current_folder, f) for f in os.listdir(self.current_folder) if f.lower().endswith('.zip')]
                        if files:
                            self.zip_file_list = ns.natsorted(files, alg=ns.IGNORECASE|ns.PATH)
                            self.current_zip_index = 0
                            self.load_zip(self.zip_file_list[0])
                            found_new = True
                    except Exception as e:
                        print(f"刷新文件夹失败: {e}")

                if not found_new:
                    self.current_zip_index = -1
                    self.image_label.setText("没有文件了")
                    self.filename_label.hide()
                    self.progress_bar.hide()
            else:
                # 如果删除的是最后一个，索引前移，否则索引不变（即指向原来的下一个）
                if self.current_zip_index >= len(self.zip_file_list):
                    self.current_zip_index = len(self.zip_file_list) - 1
                
                # 加载新索引处的文件
                self.load_zip(self.zip_file_list[self.current_zip_index])

        except Exception as e:
            print(f"删除失败: {e}")
            # 恢复（尝试重新加载，如果没删掉的话）
            if os.path.exists(file_to_delete):
                 self.load_zip(file_to_delete)

    def open_file_dialog(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "选择漫画压缩包或图片", self.initial_dir, "Supported Files (*.zip *.png *.jpg *.jpeg *.bmp *.gif *.webp);;ZIP Files (*.zip);;Image Files (*.png *.jpg *.jpeg *.bmp *.gif *.webp);;All Files (*)")
        if file_path:
            # 更新初始目录
            self.initial_dir = os.path.dirname(file_path)
            if file_path.lower().endswith('.zip'):
                self.is_folder_mode = False
                self.setup_zip_list(file_path)
            else:
                self.is_folder_mode = True
                self.setup_folder_list(file_path)

    def setup_zip_list(self, file_path):
        # 获取同目录下的所有 ZIP 文件
        try:
            folder = os.path.dirname(file_path)
            self.current_folder = folder # 记录当前文件夹
            files = [os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith('.zip')]
            # 排序
            self.zip_file_list = ns.natsorted(files, alg=ns.IGNORECASE|ns.PATH)
            
            # 确定当前文件索引
            abs_target = os.path.abspath(file_path)
            abs_list = [os.path.abspath(p) for p in self.zip_file_list]
            
            if abs_target in abs_list:
                self.current_zip_index = abs_list.index(abs_target)
            else:
                self.zip_file_list = [file_path]
                self.current_zip_index = 0
        except Exception as e:
            print(f"列表生成失败: {e}")
            self.zip_file_list = [file_path]
            self.current_zip_index = 0

        self.load_zip(file_path)

    def setup_folder_list(self, file_path):
        self.cleanup_folder()
        try:
            folder = os.path.dirname(file_path)
            self.current_folder = folder
            valid_exts = ('.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp')
            files = [os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(valid_exts)]
            self.folder_image_files = ns.natsorted(files, alg=ns.IGNORECASE|ns.PATH)
            
            abs_target = os.path.abspath(file_path)
            abs_list = [os.path.abspath(p) for p in self.folder_image_files]
            
            if abs_target in abs_list:
                self.current_folder_page_index = abs_list.index(abs_target)
            else:
                self.folder_image_files = [file_path]
                self.current_folder_page_index = 0
        except Exception as e:
            print(f"图片列表生成失败: {e}")
            self.folder_image_files = [file_path]
            self.current_folder_page_index = 0

        self.show_current_folder_page()

    def load_prev_zip(self):
        if self.current_zip_index > 0:
            self.current_zip_index -= 1
            self.load_zip(self.zip_file_list[self.current_zip_index])
        else:
            print("已经是第一个文件")

    def load_next_zip(self):
        if self.current_zip_index < len(self.zip_file_list) - 1:
            self.current_zip_index += 1
            self.load_zip(self.zip_file_list[self.current_zip_index])
        else:
            print("已经是最后一个文件")

    def load_zip(self, file_path):
        # 清理旧状态
        self.cleanup()
        
        # 显示文件名
        self.filename_label.setText(os.path.basename(file_path))
        self.filename_label.adjustSize()
        self.filename_label.show()
        self.filename_label.raise_()

        self.progress_bar.move(10, 12 + self.filename_label.height())
        self.progress_bar.show()
        self.progress_bar.raise_()

        try:
            self.current_zip = zipfile.ZipFile(file_path, 'r')
            image_files = [f for f in self.current_zip.namelist() if f.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.gif',".webp"))]
            
            try:
                image_files = ns.natsorted(image_files, alg=ns.IGNORECASE|ns.PATH)
            except:
                pass
            
            if not image_files:
                self.image_label.setText("未找到有效图片，尝试下一个...")
                # 自动跳转下一个
                if self.current_zip_index < len(self.zip_file_list) - 1:
                    QTimer.singleShot(10, self.load_next_zip) # 使用Timer稍微延后，避免递归过深
                else:
                    self.image_label.setText("没有图片了")
                return

            self.image_files = image_files
            self.progress_bar.setRange(0, len(self.image_files))
            self.progress_bar.setValue(0)
            
        except Exception as e:
            print(f"打开 ZIP 出错: {e}")
            self.image_label.setText(f"出错: {e}")
            if self.current_zip:
                self.current_zip.close()
                self.current_zip = None
            return

        self.current_page_index = 0
        self.show_current_page()

    def load_images_around_current(self):
        if not self.current_zip or not self.image_files:
            return

        # 保留当前及前后2张图片
        start_index = max(0, self.current_page_index - 1)
        end_index = min(len(self.image_files) - 1, self.current_page_index + 1)
        wanted_indices = set(range(start_index, end_index + 1))
        
        # 1. 移除不需要的缓存
        for idx in list(self.pixmap_cache.keys()):
            if idx not in wanted_indices:
                del self.pixmap_cache[idx]
        
        # 2. 加载需要的图片
        for idx in wanted_indices:
            if idx not in self.pixmap_cache:
                self.load_image_at_index(idx)
    def load_image_at_index(self, index):
        if not self.current_zip or not self.image_files:
            return None

        if index < 0 or index >= len(self.image_files):
            return None

        img_name = self.image_files[index]
        try:
            data = self.current_zip.read(img_name)
            pixmap = QPixmap()
            if pixmap.loadFromData(data):
                self.pixmap_cache[index] = pixmap
                return pixmap
        except Exception as e:
            print(f"加载图片出错 {img_name}: {e}")
            return None

    def show_current_page(self):
        if not self.image_files:
            return
        
        self.progress_bar.setRange(0, len(self.image_files))
        self.progress_bar.setValue(self.current_page_index + 1)
        
        if 0 <= self.current_page_index < len(self.image_files):
            original_pixmap = self.pixmap_cache.get(self.current_page_index)
            
            if not original_pixmap or original_pixmap.isNull():
                original_pixmap = self.load_image_at_index(self.current_page_index)
                if not original_pixmap:
                    self.image_label.setText("无法加载图片")
                    return
                
            # 获取当前视口大小
            viewport_size = self.image_label.size()
            
            # 避免视口尺寸无效
            if viewport_size.width() <= 0 or viewport_size.height() <= 0:
                return

            # 保持比例缩放至适应视口
            scaled_pixmap = original_pixmap.scaled(
                viewport_size, 
                Qt.KeepAspectRatio, 
                Qt.SmoothTransformation
            )
            self.image_label.setPixmap(scaled_pixmap)
        #延迟加载前后图片
        QTimer.singleShot(0, self.load_images_around_current)

    def load_folder_image_at_index(self, index):
        if not self.folder_image_files:
            return None
        if index < 0 or index >= len(self.folder_image_files):
            return None

        img_path = self.folder_image_files[index]
        try:
            pixmap = QPixmap(img_path)
            if not pixmap.isNull():
                self.folder_pixmap_cache[index] = pixmap
                return pixmap
        except Exception as e:
            print(f"加载图片出错 {img_path}: {e}")
            return None

    def load_folder_images_around_current(self):
        if not self.folder_image_files:
            return

        start_index = max(0, self.current_folder_page_index - 1)
        end_index = min(len(self.folder_image_files) - 1, self.current_folder_page_index + 1)
        wanted_indices = set(range(start_index, end_index + 1))
        
        for idx in list(self.folder_pixmap_cache.keys()):
            if idx not in wanted_indices:
                del self.folder_pixmap_cache[idx]
        
        for idx in wanted_indices:
            if idx not in self.folder_pixmap_cache:
                self.load_folder_image_at_index(idx)

    def show_current_folder_page(self):
        if not self.folder_image_files:
            return
        
        self.progress_bar.setRange(0, len(self.folder_image_files))
        self.progress_bar.setValue(self.current_folder_page_index + 1)
        
        # 显示文件名
        current_file = self.folder_image_files[self.current_folder_page_index]
        self.filename_label.setText(os.path.basename(current_file))
        self.filename_label.adjustSize()
        self.filename_label.show()
        self.filename_label.raise_()

        self.progress_bar.move(10, 12 + self.filename_label.height())
        self.progress_bar.show()
        self.progress_bar.raise_()

        if 0 <= self.current_folder_page_index < len(self.folder_image_files):
            original_pixmap = self.folder_pixmap_cache.get(self.current_folder_page_index)
            
            if not original_pixmap or original_pixmap.isNull():
                original_pixmap = self.load_folder_image_at_index(self.current_folder_page_index)
                if not original_pixmap:
                    self.image_label.setText("无法加载图片")
                    return
                
            viewport_size = self.image_label.size()
            
            if viewport_size.width() <= 0 or viewport_size.height() <= 0:
                return

            scaled_pixmap = original_pixmap.scaled(
                viewport_size, 
                Qt.KeepAspectRatio, 
                Qt.SmoothTransformation
            )
            self.image_label.setPixmap(scaled_pixmap)
            
        QTimer.singleShot(0, self.load_folder_images_around_current)

    def speed_curve(self,x):
        if x<0.8:
            return 4
        return 10
    def handle_wheel_event(self, event: QWheelEvent):
        if self.is_folder_mode:
            if not self.folder_image_files:
                return
        else:
            if not self.image_files:
                return

        current_time = time.time()

        # 如果距离上次翻页时间超过0.5秒，视为新的滚动操作，重置开始时间
        if current_time - self.last_wheel_time > 0.5:
            self.scroll_start_time = current_time

        # 计算连续滚动的持续时间
        duration = current_time - self.scroll_start_time
        speed=self.speed_curve(duration)

        # 根据 duration 动态设置速率限制
        limit=1.0/speed

        if current_time - self.last_wheel_time < limit:
            return
            
        self.last_wheel_time = current_time
            
        angle = event.angleDelta().y()
        # 向上滚动查看上一页，向下滚动查看下一页
        if angle > 0:
            self.prev_page()
        else:
            self.next_page()

    def prev_page(self):
        if self.is_folder_mode:
            if self.current_folder_page_index > 0:
                self.current_folder_page_index -= 1
                self.show_current_folder_page()
        else:
            if self.current_page_index > 0:
                self.current_page_index -= 1
                self.show_current_page()

    def next_page(self):
        if self.is_folder_mode:
            if self.current_folder_page_index < len(self.folder_image_files) - 1:
                self.current_folder_page_index += 1
                self.show_current_folder_page()
        else:
            if self.current_page_index < len(self.image_files) - 1:
                self.current_page_index += 1
                self.show_current_page()

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        match key:
            case Qt.Key_Left | Qt.Key_PageUp:
                self.prev_page()
                event.accept()
            case Qt.Key_Right | Qt.Key_PageDown:
                self.next_page()
                event.accept()
            case Qt.Key_Up:
                if not self.is_folder_mode:
                    self.load_prev_zip()
                event.accept()
            case Qt.Key_Down:
                if not self.is_folder_mode:
                    self.load_next_zip()
                event.accept()
            case Qt.Key_Delete:
                 self.delete_current_file()
                 event.accept()
            case Qt.Key_Escape:
                self.close()
            case _:
                super().keyPressEvent(event)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = ComicReader()
    window.show()
    sys.exit(app.exec())