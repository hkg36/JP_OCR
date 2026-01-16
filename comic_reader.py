import sys
import zipfile
from PySide6.QtWidgets import (QApplication, QMainWindow, QScrollArea, QWidget, 
                               QVBoxLayout, QLabel, QFileDialog, QSizePolicy)
from PySide6.QtGui import QPixmap, QAction, QKeyEvent
from PySide6.QtCore import Qt

class ComicReader(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("漫画阅读器")
        
        # 状态变量
        self.scale_factor = 1.0
        self.original_pixmaps = []  # 存储原始 QPixmap
        self.image_labels = []      # 存储 QLabel 引用

        # 主滚动区域
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setAlignment(Qt.AlignCenter) # 图片居中
        # 防止 ScrollArea 抢夺键盘焦点导致无法捕获上下键事件用于缩放
        # 用户可以通过鼠标滚轮滚动，或者我们手动处理其他键盘滚动事件
        self.scroll_area.setFocusPolicy(Qt.NoFocus)
        
        self.setCentralWidget(self.scroll_area)

        # 初始无内容
        self.content_widget = None
        self.setup_empty_content()

        # 菜单栏
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("文件")
        
        open_action = QAction("打开 ZIP", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.open_zip_dialog)
        file_menu.addAction(open_action)

        exit_action = QAction("退出", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        
        # 启动时最大化
        self.showMaximized()

    def setup_empty_content(self):
        # 创建一个空的容器
        self.content_widget = QWidget()
        self.layout = QVBoxLayout(self.content_widget)
        self.layout.setAlignment(Qt.AlignCenter)
        self.layout.addWidget(QLabel("请通过 菜单 -> 打开 ZIP 加载漫画"))
        self.scroll_area.setWidget(self.content_widget)

    def open_zip_dialog(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "选择漫画压缩包", "", "ZIP Files (*.zip);;All Files (*)")
        if file_path:
            self.load_zip(file_path)

    def load_zip(self, file_path):
        # 读取数据
        new_pixmaps = []
        try:
            with zipfile.ZipFile(file_path, 'r') as zf:
                # 筛选图片文件
                # 简单过滤常见扩展名
                image_files = [f for f in zf.namelist() if f.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.gif'))]
                # 排序，保证漫画顺序正确
                try:
                    # 尝试按数字排序 (如果文件名包含数字)
                    # 这里做一个简单的处理，如果只是纯文件名排序用默认 sort
                    image_files.sort()
                except:
                    pass
                
                if not image_files:
                    return

                for img_name in image_files:
                    data = zf.read(img_name)
                    pixmap = QPixmap()
                    if pixmap.loadFromData(data):
                        new_pixmaps.append(pixmap)
        except Exception as e:
            print(f"读取 ZIP 出错: {e}")
            return

        if not new_pixmaps:
            print("未找到有效图片")
            return

        # 更新数据
        self.original_pixmaps = new_pixmaps
        self.scale_factor = 1.0 # 重置缩放
        self.setWindowTitle(f"漫画阅读器 - {file_path}")
        
        # 重建 UI
        self.setup_comic_content()
        self.update_images()

    def setup_comic_content(self):
        # 销毁旧 widget，创建新 widget
        # 这样比一个个删除 layout item 更干净
        if self.content_widget:
            # 不需要手动 deleteLater, setWidget 会处理所有权转移或我们可以手动新建
            pass
            
        self.content_widget = QWidget()
        self.layout = QVBoxLayout(self.content_widget)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(10) # 图片间距
        self.layout.setAlignment(Qt.AlignHCenter | Qt.AlignTop) # 水平居中，垂直靠上
        
        self.image_labels = []
        for _ in self.original_pixmaps:
            label = QLabel()
            label.setAlignment(Qt.AlignCenter)
            # 设置SizePolicy，让Label随着图片大小变化
            label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            self.layout.addWidget(label)
            self.image_labels.append(label)
            
        self.scroll_area.setWidget(self.content_widget)

    def update_images(self):
        if not self.original_pixmaps:
            return

        # 保存缩放信息到标题
        self.setWindowTitle(f"漫画阅读器 - 缩放: {int(self.scale_factor * 100)}%")

        for label, original_pixmap in zip(self.image_labels, self.original_pixmaps):
            if original_pixmap.isNull():
                continue
                
            width = int(original_pixmap.width() * self.scale_factor)
            height = int(original_pixmap.height() * self.scale_factor)
            
            # 使用 FastTransformation 可能会快点，SmoothTransformation 效果好
            # 缩放操作比较耗时，如果图片特别多可能会卡顿
            # 优化方案是只更新可见区域，但实现复杂。这里直接全部更新。
            scaled_pixmap = original_pixmap.scaled(
                width, height, 
                Qt.KeepAspectRatio, 
                Qt.SmoothTransformation
            )
            label.setPixmap(scaled_pixmap)
            # 显式设置 Label 大小，虽然 setPixmap 会自动触发，但有时候布局需要
            label.setFixedSize(width, height)

    def keyPressEvent(self, event: QKeyEvent):
        # 捕获上下键进行缩放
        key = event.key()
        if key == Qt.Key_Up:
            self.zoom(0.1) # 放大
            event.accept()
        elif key == Qt.Key_Down:
            self.zoom(-0.1) # 缩小
            event.accept()
        else:
            super().keyPressEvent(event)

    def zoom(self, delta):
        new_scale = self.scale_factor + delta
        # 浮点运算消除误差
        new_scale = round(new_scale, 1)
        
        if 0.5 <= new_scale <= 3.0:
            if new_scale != self.scale_factor:
                self.scale_factor = new_scale
                self.update_images()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = ComicReader()
    window.show()
    sys.exit(app.exec())
