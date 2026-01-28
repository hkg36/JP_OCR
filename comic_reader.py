import sys
import os
import time
import zipfile
import natsort as ns
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
        
        self.last_wheel_time = 0  # 上次滚轮翻页的时间
        self.scroll_start_time = 0  # 连续滚动开始时间
        
        # 初始内容
        self.image_label = QLabel("请右键点击 -> 打开 ZIP 加载漫画")
        self.image_label.setAlignment(Qt.AlignCenter)
        # 允许 Label 调整大小
        self.image_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)

        self.setCentralWidget(self.image_label)

        # 文件名显示 Label
        self.filename_label = QLabel(self)
        self.filename_label.setFixedWidth(300)
        self.filename_label.setWordWrap(True)
        self.filename_label.setStyleSheet("QLabel { background-color: rgba(0, 0, 0, 160); color: white; padding: 8px; border-radius: 4px; font-size: 14px; }")
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
        
        open_action = QAction("打开 ZIP", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.open_zip_dialog)
        menu.addAction(open_action)

        exit_action = QAction("退出", self)
        exit_action.triggered.connect(self.close)
        menu.addAction(exit_action)
        
        menu.exec(event.globalPos())

    def closeEvent(self, event):
        self.cleanup()
        super().closeEvent(event)

    def resizeEvent(self, event):
        self.show_current_page()
        self.filename_label.raise_()
        self.progress_bar.raise_()
        super().resizeEvent(event)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MiddleButton:
            self.open_zip_dialog()
            event.accept()
        elif event.button() == Qt.LeftButton:
            if self.current_zip:
                self.next_page()
            else:
                self.open_zip_dialog()
            event.accept()
        else:
            super().mousePressEvent(event)

    def cleanup(self):
        self.pixmap_cache = {}
        self.image_files = []
        if self.current_zip:
            self.current_zip.close()
            self.current_zip = None

    def delete_current_file(self):
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
                # 列表空了
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

    def open_zip_dialog(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "选择漫画压缩包", "E:/baks", "ZIP Files (*.zip);;All Files (*)")
        if file_path:
            # 获取同目录下的所有 ZIP 文件
            try:
                folder = os.path.dirname(file_path)
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
            image_files = [f for f in self.current_zip.namelist() if f.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.gif'))]
            
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
        self.load_images_around_current()
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
                img_name = self.image_files[idx]
                try:
                    data = self.current_zip.read(img_name)
                    pixmap = QPixmap()
                    if pixmap.loadFromData(data):
                        self.pixmap_cache[idx] = pixmap
                except Exception as e:
                    print(f"加载图片出错 {img_name}: {e}")

    def show_current_page(self):
        if not self.image_files:
            return
        
        self.progress_bar.setRange(0, len(self.image_files))
        self.progress_bar.setValue(self.current_page_index + 1)
        
        if 0 <= self.current_page_index < len(self.image_files):
            original_pixmap = self.pixmap_cache.get(self.current_page_index)
            
            if not original_pixmap or original_pixmap.isNull():
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

    def handle_wheel_event(self, event: QWheelEvent):
        if not self.image_files:
            return

        current_time = time.time()

        # 如果距离上次翻页时间超过0.5秒，视为新的滚动操作，重置开始时间
        if current_time - self.last_wheel_time > 0.5:
            self.scroll_start_time = current_time

        # 计算连续滚动的持续时间
        duration = current_time - self.scroll_start_time

        # 根据 duration 动态设置速率限制
        if duration < 1.0:
            limit = 1.0 / 3  # 第一秒：每秒最多3页 (约0.33s间隔)
        elif duration < 2.0:
            limit = 1.0 / 6  # 第二秒：每秒最多6页 (约0.16s间隔)
        else:
            limit = 1.0 / 10 # 第三秒及以后：每秒最多10页 (0.1s间隔)

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
        if self.current_page_index > 0:
            self.current_page_index -= 1
            self.load_images_around_current()
            self.show_current_page()

    def next_page(self):
        if self.current_page_index < len(self.image_files) - 1:
            self.current_page_index += 1
            self.load_images_around_current()
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
                self.load_prev_zip()
                event.accept()
            case Qt.Key_Down:
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