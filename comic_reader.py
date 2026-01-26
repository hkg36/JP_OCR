import sys
import zipfile
import natsort as ns
from PySide6.QtWidgets import (QApplication, QMainWindow, QScrollArea, QWidget, 
                               QVBoxLayout, QLabel, QFileDialog, QSizePolicy, QMenu)
from PySide6.QtGui import QPixmap, QAction, QKeyEvent, QWheelEvent, QMouseEvent, QCursor
from PySide6.QtCore import Qt, QTimer, QEvent

class ComicReader(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("漫画阅读器")
        
        # 去掉标题栏
        self.setWindowFlags(Qt.FramelessWindowHint)
        
        # 状态变量
        self.original_pixmaps = []  # 存储原始 QPixmap
        self.current_page_index = 0
        
        # 异步加载相关
        self.load_timer = QTimer(self)
        self.load_timer.timeout.connect(self.load_process)
        self.current_zip = None
        self.pending_files = [] 

        # 主滚动区域
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setAlignment(Qt.AlignCenter) # 图片居中
        self.scroll_area.setFocusPolicy(Qt.NoFocus)
        
        # 安装事件过滤器以拦截滚轮事件实现翻页
        self.scroll_area.viewport().installEventFilter(self)
        
        self.setCentralWidget(self.scroll_area)

        # 初始内容
        self.image_label = QLabel("请右键点击 -> 打开 ZIP 加载漫画")
        self.image_label.setAlignment(Qt.AlignCenter)
        # 允许 Label 调整大小
        self.image_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.scroll_area.setWidget(self.image_label)

        # 启动时最大化
        self.showMaximized()

    def eventFilter(self, source, event):
        if source == self.scroll_area.viewport() and event.type() == QEvent.Wheel:
            self.handle_wheel_event(event)
            return True # 消耗事件，防止 ScrollArea 滚动
        return super().eventFilter(source, event)

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
        super().resizeEvent(event)

    def cleanup(self):
        self.load_timer.stop()
        self.pending_files = []
        self.original_pixmaps = []
        if self.current_zip:
            self.current_zip.close()
            self.current_zip = None

    def open_zip_dialog(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "选择漫画压缩包", "E:/baks", "ZIP Files (*.zip);;All Files (*)")
        if file_path:
            self.load_zip(file_path)

    def load_zip(self, file_path):
        # 清理旧状态
        self.cleanup()

        try:
            self.current_zip = zipfile.ZipFile(file_path, 'r')
            image_files = [f for f in self.current_zip.namelist() if f.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.gif'))]
            
            try:
                image_files = ns.natsorted(image_files, alg=ns.IGNORECASE|ns.PATH)
            except:
                pass
            
            if not image_files:
                self.image_label.setText("未找到有效图片")
                return

            self.pending_files = image_files
            
        except Exception as e:
            print(f"打开 ZIP 出错: {e}")
            self.image_label.setText(f"出错: {e}")
            if self.current_zip:
                self.current_zip.close()
                self.current_zip = None
            return

        self.current_page_index = 0
        self.load_timer.start(0)

    def load_process(self):
        if not self.pending_files or not self.current_zip:
            self.load_timer.stop()
            if self.current_zip:
                self.current_zip.close()
                self.current_zip = None
            return
        
        img_name = self.pending_files.pop(0)
        
        try:
            data = self.current_zip.read(img_name)
            pixmap = QPixmap()
            if pixmap.loadFromData(data):
                self.original_pixmaps.append(pixmap)
                
                # 如果是第一张图片，或者当前显示为空，立即显示
                if len(self.original_pixmaps) == 1:
                    self.show_current_page()
                
        except Exception as e:
            print(f"加载图片出错 {img_name}: {e}")

    def show_current_page(self):
        if not self.original_pixmaps:
            return
        
        if 0 <= self.current_page_index < len(self.original_pixmaps):
            original_pixmap = self.original_pixmaps[self.current_page_index]
            
            if original_pixmap.isNull():
                return
                
            # 获取当前视口大小
            viewport_size = self.scroll_area.viewport().size()
            
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
            self.image_label.adjustSize()

    def handle_wheel_event(self, event: QWheelEvent):
        if not self.original_pixmaps:
            return
            
        angle = event.angleDelta().y()
        # 向上滚动查看上一页，向下滚动查看下一页
        if angle > 0:
            self.prev_page()
        else:
            self.next_page()

    def prev_page(self):
        if self.current_page_index > 0:
            self.current_page_index -= 1
            self.show_current_page()

    def next_page(self):
        if self.current_page_index < len(self.original_pixmaps) - 1:
            self.current_page_index += 1
            self.show_current_page()

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        if key == Qt.Key_Left:
            self.prev_page()
            event.accept()
        elif key == Qt.Key_Right:
            self.next_page()
            event.accept()
        elif key == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = ComicReader()
    window.show()
    sys.exit(app.exec())