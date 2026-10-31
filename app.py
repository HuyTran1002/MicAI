import sys
import os
import time
import subprocess
import winreg
import json
import copy
import sounddevice as sd

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QPushButton, QSlider, QProgressBar,
    QFrame, QCheckBox, QSystemTrayIcon, QMenu, QMessageBox, QTabWidget,
    QScrollArea
)
from PyQt6.QtCore import Qt, pyqtSignal, QObject, QTimer, QCoreApplication
from PyQt6.QtGui import QIcon, QCursor
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
import ctypes
from ctypes import wintypes

from engine import (
    AudioEngine, get_windows_default_playback, find_sd_output_device_by_name,
    get_audio_endpoints_fingerprint
)

AUTOSTART_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_APP_NAME = "AIMicNoiseCanceller"

# --- CƠ CHẾ KHÓA DUY NHẤT 1 PHIÊN BẢN (SINGLE INSTANCE LOCK) ---
SINGLE_INSTANCE_MUTEX_NAME = "AIMicNoiseCanceller_SingleInstance_Mutex"
SINGLE_INSTANCE_IPC_NAME = "AIMicNoiseCanceller_SingleInstance_IPC"

def check_single_instance():
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, SINGLE_INSTANCE_MUTEX_NAME)
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        try:
            # Gửi thông điệp kích hoạt cửa sổ cho instance đang chạy
            _dummy = QCoreApplication.instance() or QCoreApplication(sys.argv)
            socket = QLocalSocket()
            socket.connectToServer(SINGLE_INSTANCE_IPC_NAME)
            if socket.waitForConnected(1000):
                socket.write(b"SHOW\n")
                socket.flush()
                socket.waitForBytesWritten(1000)
                socket.close()
            else:
                # Fallback bằng Windows API nếu socket chưa sẵn sàng
                hwnd = ctypes.windll.user32.FindWindowW(None, "AI Mic Noise Canceller (Discord Krisp Style)")
                if hwnd:
                    ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                    ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
        ctypes.windll.kernel32.CloseHandle(mutex)
        sys.exit(0)
    return mutex

def is_autostart_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_REG_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, AUTOSTART_APP_NAME)
            return True
    except Exception:
        return False

def set_autostart(enabled: bool):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_REG_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                exe_path = sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(sys.argv[0])
                cmd = f'"{exe_path}" --autostart'
                winreg.SetValueEx(key, AUTOSTART_APP_NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, AUTOSTART_APP_NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception as e:
        print(f"Error updating autostart: {e}")
        return False

def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

ICON_PATH = resource_path("app_icon.png")

# --- HỆ THỐNG GHI NHỚ VÀ QUẢN LÝ CÀI ĐẶT (SETTINGS) ---
DEFAULT_SETTINGS = {
    "version": 1,
    "audio_devices": {
        "input_mic": "",          # Tên thiết bị mic thật
        "output_mic": "",         # Tên thiết bị CABLE Input
        "music_input": "",        # Tên Line 1
        "headphone_output": "default" # "default", "none", hoặc tên tai nghe/loa
    },
    "mic_settings": {
        "denoise_enabled": True,  # Bật lọc âm AI
        "noise_gate": 10,         # Ngưỡng Gate (0..50)
        "gain": 100               # Khuếch đại Mic (50..300)
    },
    "music_settings": {
        "web_to_hp": False,       # Phát ra tai nghe / loa
        "web_to_mic": False,      # Trộn vào mic Discord / Game
        "ducking": False,         # Tự hạ nhạc khi nói
        "hp_volume": 50,          # Âm lượng trong tai (0..100)
        "discord_volume": 50      # Âm lượng gửi vào mic (0..100)
    },
    "app_settings": {
        "autostart": False,       # Khởi động cùng Windows
        "minimize_to_tray": True, # Thu nhỏ taskbar khi bấm X
        "auto_start_mic": True,   # Tự động bật mic khi mở app
        "current_tab": 0          # Tab mở gần nhất
    }
}

def get_config_dir():
    try:
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(os.path.abspath(sys.executable))
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            
        test_file = os.path.join(base_dir, ".write_test")
        with open(test_file, "w") as f:
            f.write("1")
        os.remove(test_file)
        return base_dir
    except Exception:
        pass
        
    appdata = os.getenv('APPDATA')
    if appdata:
        cfg_dir = os.path.join(appdata, "AIMicNoiseCanceller")
        os.makedirs(cfg_dir, exist_ok=True)
        return cfg_dir
        
    return os.path.expanduser("~")

SETTINGS_FILE_PATH = os.path.join(get_config_dir(), "settings.json")

def deep_merge(default_dict, user_dict):
    result = copy.deepcopy(default_dict)
    for k, v in user_dict.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = deep_merge(result[k], v)
        else:
            result[k] = v
    return result

def load_settings():
    if os.path.exists(SETTINGS_FILE_PATH):
        try:
            with open(SETTINGS_FILE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return deep_merge(DEFAULT_SETTINGS, data)
        except Exception as e:
            print(f"Warning: Failed to load settings from {SETTINGS_FILE_PATH}: {e}")
    return copy.deepcopy(DEFAULT_SETTINGS)

def save_settings_to_file(settings_dict):
    try:
        tmp_path = SETTINGS_FILE_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(settings_dict, f, indent=2, ensure_ascii=False)
        if os.path.exists(SETTINGS_FILE_PATH):
            os.replace(tmp_path, SETTINGS_FILE_PATH)
        else:
            os.rename(tmp_path, SETTINGS_FILE_PATH)
        return True
    except Exception as e:
        print(f"Error saving settings to {SETTINGS_FILE_PATH}: {e}")
        return False

def ensure_check_icon():
    p = resource_path("check_icon.png")
    if not os.path.exists(p):
        try:
            from PIL import Image, ImageDraw
            img = Image.new('RGBA', (32, 32), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.line([(7, 16), (13, 22), (25, 10)], fill=(255, 255, 255, 255), width=4)
            img.save(p)
        except Exception:
            pass
    return p.replace("\\", "/")

CHECK_ICON_PATH = ensure_check_icon()

TAB_MODERN_STYLE = f"""
QMainWindow {{
    background-color: #0B0D13;
}}

QWidget {{
    color: #E2E8F0;
    font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif;
    font-size: 12px;
}}

/* Header */
QLabel#headerTitle {{
    font-size: 17px;
    font-weight: 700;
    color: #FFFFFF;
    letter-spacing: 0.2px;
}}

QLabel#headerSub {{
    font-size: 11px;
    color: #94A3B8;
}}

/* TabWidget Styles */
QTabWidget::pane {{
    border: 1px solid #222634;
    background-color: #12141C;
    border-radius: 10px;
    top: -1px;
}}

QTabBar::tab {{
    background: #171923;
    color: #94A3B8;
    border: 1px solid #222634;
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    padding: 8px 18px;
    font-size: 12px;
    font-weight: 600;
    margin-right: 4px;
}}

QTabBar::tab:selected {{
    background: #12141C;
    color: #FFFFFF;
    border-color: #5865F2;
    border-bottom: 2px solid #12141C;
}}

QTabBar::tab:hover:!selected {{
    background: #202433;
    color: #E2E8F0;
}}

/* Scroll Area */
QScrollArea {{
    border: none;
    background: transparent;
}}

QScrollBar:vertical {{
    border: none;
    background: transparent;
    width: 5px;
    margin: 0px;
    border-radius: 2px;
}}

QScrollBar::handle:vertical {{
    background: #2E364E;
    border-radius: 2px;
    min-height: 20px;
}}

QScrollBar::handle:vertical:hover {{
    background: #5865F2;
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}

QWidget#tabScrollContent {{
    background: transparent;
}}

/* Group Container Inside Tabs */
QFrame.innerGroup {{
    background-color: #171A24;
    border: 1px solid #222634;
    border-radius: 10px;
}}

QLabel.groupTitle {{
    font-size: 10px;
    font-weight: 700;
    color: #818CF8;
    text-transform: uppercase;
    letter-spacing: 0.6px;
    margin-bottom: 2px;
}}

QLabel.fieldLabel {{
    font-weight: 600;
    color: #CBD5E1;
    font-size: 11px;
}}

QLabel.hintLabel {{
    color: #64748B;
    font-size: 10px;
}}

/* ComboBox */
QComboBox {{
    background-color: #11131A;
    border: 1px solid #292E3E;
    border-radius: 6px;
    padding: 4px 10px;
    color: #FFFFFF;
    font-size: 12px;
    min-height: 24px;
}}

QComboBox:hover {{
    border-color: #5865F2;
    background-color: #141720;
}}

QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 24px;
    border-left: none;
}}

QComboBox QAbstractItemView {{
    background-color: #141722;
    border: 1px solid #2C3246;
    selection-background-color: #4F46E5;
    selection-color: #FFFFFF;
    color: #E2E8F0;
    padding: 4px;
    outline: 0;
    border-radius: 6px;
}}

/* Buttons */
QPushButton {{
    border-radius: 6px;
    font-weight: 600;
    font-size: 11px;
    outline: none;
}}

QPushButton#refreshBtn {{
    background-color: #171A24;
    border: 1px solid #292E3E;
    color: #94A3B8;
    padding: 5px 12px;
    font-size: 11px;
    font-weight: 600;
    border-radius: 6px;
}}

QPushButton#refreshBtn:hover {{
    background-color: #222635;
    color: #FFFFFF;
    border-color: #5865F2;
}}

QPushButton#vbcableBtn {{
    background-color: #0E2A24;
    border: 1px solid #14534A;
    color: #2DD4BF;
    font-size: 11px;
    font-weight: 600;
    padding: 5px 12px;
    border-radius: 6px;
}}

QPushButton#vbcableBtn:hover {{
    background-color: #133D34;
    border-color: #2DD4BF;
    color: #5EEAD4;
}}

QPushButton#actionBtn {{
    background-color: #1C202C;
    border: 1px solid #2D3448;
    color: #C7D2FE;
    font-size: 11px;
    font-weight: 600;
    padding: 5px 12px;
    border-radius: 6px;
}}

QPushButton#actionBtn:hover {{
    background-color: #252B3C;
    border-color: #5865F2;
    color: #FFFFFF;
}}

QPushButton#startBtn {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4F46E5, stop:1 #6366F1);
    color: #FFFFFF;
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.4px;
    border: none;
    border-radius: 8px;
}}

QPushButton#startBtn:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4338CA, stop:1 #4F46E5);
}}

QPushButton#startBtn:disabled {{
    background-color: #151821;
    color: #475569;
}}

QPushButton#stopBtn {{
    background-color: #DC2626;
    color: #FFFFFF;
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.4px;
    border: none;
    border-radius: 8px;
}}

QPushButton#stopBtn:hover {{
    background-color: #B91C1C;
}}

QPushButton#stopBtn:disabled {{
    background-color: #151821;
    color: #475569;
}}

/* CheckBox */
QCheckBox {{
    spacing: 8px;
    font-weight: 600;
    color: #E2E8F0;
    font-size: 11px;
    padding: 1px 0px;
}}

QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid #333A4D;
    background-color: #11131A;
}}

QCheckBox::indicator:hover {{
    border-color: #5865F2;
    background-color: #181B24;
}}

QCheckBox::indicator:checked {{
    background-color: #5865F2;
    border-color: #5865F2;
    image: url("{CHECK_ICON_PATH}");
}}

/* Sliders */
QSlider::groove:horizontal {{
    height: 5px;
    background: #242938;
    border-radius: 2px;
}}

QSlider::sub-page:horizontal {{
    background: #5865F2;
    border-radius: 2px;
}}

QSlider::handle:horizontal {{
    background: #FFFFFF;
    border: 2px solid #5865F2;
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}}

QSlider::handle:horizontal:hover {{
    border-color: #818CF8;
    background: #F8FAFC;
}}

/* VU Meters */
QProgressBar {{
    border: none;
    border-radius: 3px;
    background-color: #11131A;
    height: 9px;
}}

QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #5865F2, stop:0.75 #10B981, stop:1 #F59E0B);
    border-radius: 3px;
}}
"""

class SignalEmitter(QObject):
    meter_signal = pyqtSignal(float, float, float)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI Mic Noise Canceller (Discord Krisp Style)")
        self.setFixedSize(600, 725)
        self.setStyleSheet(TAB_MODERN_STYLE)
        
        if os.path.exists(ICON_PATH):
            self.app_icon = QIcon(ICON_PATH)
            self.setWindowIcon(self.app_icon)
        else:
            self.app_icon = self.style().standardIcon(self.style().StandardPixmap.SP_MediaVolume)
            
        self.settings = load_settings()
        self._is_loading_settings = True
        self._autostart_retries = 0

        self.engine = AudioEngine()
        self.signals = SignalEmitter()
        self.signals.meter_signal.connect(self.update_meters_ui)
        self.engine.on_meter_update = self._on_meter_engine_callback
        
        self.input_devices_list = []
        self.output_devices_list = []
        
        # Timer tự động lưu cài đặt (debounced 400ms)
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(400)
        self.save_timer.timeout.connect(self._do_save_settings)

        self.init_ui()
        self.refresh_devices()
        self.load_and_apply_settings()
        self.connect_change_events()
        self.init_tray()
        
        # Lưu trạng thái danh sách thiết bị âm thanh Windows và endpoint mặc định ban đầu
        self._last_endpoints_fingerprint = get_audio_endpoints_fingerprint()
        self._last_default_playback_ep, _ = get_windows_default_playback()
        
        # Timer tự động theo dõi thay đổi thiết bị âm thanh mặc định của Windows (1.2s / lần, siêu nhẹ, không lag game)
        self.device_watch_timer = QTimer(self)
        self.device_watch_timer.setInterval(1200)
        self.device_watch_timer.timeout.connect(self.check_default_device_change)
        self.device_watch_timer.start()

        
    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(16, 12, 16, 12)
        
        # 1. Header (Gọn gàng, rõ ràng)
        header_layout = QHBoxLayout()
        header_layout.setSpacing(10)
        header_layout.setContentsMargins(2, 0, 2, 2)
        
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        
        title_lbl = QLabel("🎙️ AI Mic Noise Canceller", objectName="headerTitle")
        sub_lbl = QLabel("Lọc tạp âm AI • Chống vọng tiếng khi nghe nhạc", objectName="headerSub")
        title_box.addWidget(title_lbl)
        title_box.addWidget(sub_lbl)
        header_layout.addLayout(title_box)
        header_layout.addStretch()
        
        btn_box = QHBoxLayout()
        btn_box.setSpacing(6)

        reset_btn = QPushButton("↺ Đặt lại", objectName="refreshBtn")
        reset_btn.setToolTip("Khôi phục toàn bộ cài đặt về mặc định ban đầu")
        reset_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        reset_btn.clicked.connect(self.reset_to_default_settings)
        btn_box.addWidget(reset_btn)

        refresh_btn = QPushButton("🔄 Làm mới", objectName="refreshBtn")
        refresh_btn.setToolTip("Quét lại danh sách thiết bị âm thanh")
        refresh_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        refresh_btn.clicked.connect(self.refresh_devices)
        btn_box.addWidget(refresh_btn)

        header_layout.addLayout(btn_box)
        main_layout.addLayout(header_layout)
        
        # 2. QTabWidget
        self.tabs = QTabWidget()
        
        def create_tab_scroll(content_widget):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            scroll.setStyleSheet("QScrollArea { border: none; background: transparent; } QWidget#tabScrollContent { background: transparent; }")
            scroll.setWidget(content_widget)
            return scroll
        
        # ==========================================================
        # TAB 1: 🎙️ Mic Lọc Âm AI (Kênh B)
        # ==========================================================
        tab_mic = QWidget()
        tab_mic.setObjectName("tabScrollContent")
        layout_tab_mic = QVBoxLayout(tab_mic)
        layout_tab_mic.setSpacing(10)
        layout_tab_mic.setContentsMargins(10, 10, 10, 10)
        
        # --- Khung 1: Cổng âm thanh (Audio Routing) ---
        group_dev = QFrame()
        group_dev.setProperty("class", "innerGroup")
        dev_vbox = QVBoxLayout(group_dev)
        dev_vbox.setSpacing(8)
        dev_vbox.setContentsMargins(12, 10, 12, 10)
        
        lbl_g1 = QLabel("CỔNG ÂM THANH (AUDIO ROUTING)")
        lbl_g1.setProperty("class", "groupTitle")
        dev_vbox.addWidget(lbl_g1)
        
        # Hàng 1: Mic đầu vào (Mic thật) - Toàn chiều rộng
        lbl_in = QLabel("🎤 Mic đầu vào (Mic thật của máy):")
        lbl_in.setProperty("class", "fieldLabel")
        self.input_combo = QComboBox()
        self.input_combo.setFixedHeight(30)
        dev_vbox.addWidget(lbl_in)
        dev_vbox.addWidget(self.input_combo)
        
        # Hàng 2: Cổng ra Discord / Game (Kênh B) - Toàn chiều rộng
        lbl_out = QLabel("🔊 Cổng ra Discord / Game (Kênh B):")
        lbl_out.setProperty("class", "fieldLabel")
        self.output_combo = QComboBox()
        self.output_combo.setFixedHeight(30)
        self.output_combo.currentIndexChanged.connect(self.check_output_selection)
        dev_vbox.addWidget(lbl_out)
        dev_vbox.addWidget(self.output_combo)
        
        # Cảnh báo nếu chọn nhầm Loa / Tai nghe thật làm vọng tiếng
        self.echo_warn_lbl = QLabel(
            "⚠️ BẠN ĐANG CHỌN TAI NGHE / LOA THẬT: Giọng mic sẽ phát thẳng vào tai gây vọng tiếng!\n"
            "👉 Hãy chọn 'CABLE Input' để đưa tiếng mic vào Discord mà không bị vọng."
        )
        self.echo_warn_lbl.setStyleSheet("color: #F87171; font-size: 11px; background: #2A1215; border: 1px solid #7F1D1D; border-radius: 6px; padding: 6px 10px; line-height: 130%;")
        self.echo_warn_lbl.setWordWrap(True)
        self.echo_warn_lbl.setVisible(False)
        dev_vbox.addWidget(self.echo_warn_lbl)
        
        # Nút hỗ trợ cáp ảo & tắt tiếng vọng Windows
        btn_dev_row = QHBoxLayout()
        btn_dev_row.setSpacing(10)
        
        vbcable_btn = QPushButton("🔌 Cài đặt VB-Cable", objectName="vbcableBtn")
        vbcable_btn.setFixedHeight(28)
        vbcable_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        vbcable_btn.clicked.connect(self.install_vbcable_action)
        btn_dev_row.addWidget(vbcable_btn)
        
        fix_echo_btn = QPushButton("🛠️ Tắt tiếng vọng trong Windows", objectName="actionBtn")
        fix_echo_btn.setFixedHeight(28)
        fix_echo_btn.setToolTip("Mở cài đặt Windows Sound để tắt tính năng 'Listen to this device' gây vọng mic")
        fix_echo_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        fix_echo_btn.clicked.connect(self.open_sound_control_panel)
        btn_dev_row.addWidget(fix_echo_btn)
        btn_dev_row.addStretch()
        dev_vbox.addLayout(btn_dev_row)
        
        layout_tab_mic.addWidget(group_dev)
        
        # --- Khung 2: Bộ lọc tạp âm AI ---
        group_ai = QFrame()
        group_ai.setProperty("class", "innerGroup")
        ai_vbox = QVBoxLayout(group_ai)
        ai_vbox.setSpacing(8)
        ai_vbox.setContentsMargins(12, 10, 12, 10)
        
        lbl_g2 = QLabel("BỘ LỌC TẠP ÂM AI (RNNOISE DISCORD ENGINE)")
        lbl_g2.setProperty("class", "groupTitle")
        ai_vbox.addWidget(lbl_g2)
        
        self.denoise_cb = QCheckBox("Bật Lọc Âm AI (Triệt tiêu bàn phím, tiếng quạt gió, tạp âm)")
        self.denoise_cb.setChecked(True)
        self.denoise_cb.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.denoise_cb.toggled.connect(self.toggle_denoise)
        ai_vbox.addWidget(self.denoise_cb)
        
        # Slider 1: Noise Gate (Gọn gàng)
        gate_head = QHBoxLayout()
        gate_lbl = QLabel("Chặn tạp âm & giọng nói xa (Gate):")
        gate_lbl.setProperty("class", "fieldLabel")
        gate_lbl.setToolTip("Chặn tiếng thở, tiếng quạt, tiếng TikTok hoặc tiếng hát karaoke từ xa khi bạn không nói")
        self.gate_val_label = QLabel("10%")
        self.gate_val_label.setStyleSheet("color: #10B981; background-color: rgba(16, 185, 129, 0.12); border: 1px solid rgba(16, 185, 129, 0.25); border-radius: 4px; padding: 1px 6px; font-weight: 700; font-size: 11px;")
        gate_head.addWidget(gate_lbl)
        gate_head.addStretch()
        gate_head.addWidget(self.gate_val_label)
        ai_vbox.addLayout(gate_head)
        
        self.gate_slider = QSlider(Qt.Orientation.Horizontal)
        self.gate_slider.setRange(0, 50)
        self.gate_slider.setValue(10)
        self.gate_slider.setFixedHeight(18)
        self.gate_slider.setToolTip("Kéo lên nếu có người hát karaoke hoặc mở TikTok gần bạn. Kéo xuống nếu bạn nói nhỏ hoặc mic để xa.")
        self.gate_slider.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.gate_slider.valueChanged.connect(self.change_noise_gate)
        ai_vbox.addWidget(self.gate_slider)
        
        # Slider 2: Gain Booster (Gọn gàng)
        gain_head = QHBoxLayout()
        gain_lbl = QLabel("Khuếch đại mic (Gain):")
        gain_lbl.setProperty("class", "fieldLabel")
        self.gain_val_label = QLabel("100%")
        self.gain_val_label.setStyleSheet("color: #818CF8; background-color: rgba(129, 140, 248, 0.12); border: 1px solid rgba(129, 140, 248, 0.25); border-radius: 4px; padding: 1px 6px; font-weight: 700; font-size: 11px;")
        gain_head.addWidget(gain_lbl)
        gain_head.addStretch()
        gain_head.addWidget(self.gain_val_label)
        ai_vbox.addLayout(gain_head)
        
        self.gain_slider = QSlider(Qt.Orientation.Horizontal)
        self.gain_slider.setRange(50, 300)
        self.gain_slider.setValue(100)
        self.gain_slider.setFixedHeight(18)
        self.gain_slider.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.gain_slider.valueChanged.connect(self.change_gain)
        ai_vbox.addWidget(self.gain_slider)
        
        layout_tab_mic.addWidget(group_ai)
        layout_tab_mic.addStretch()
        
        self.tabs.addTab(create_tab_scroll(tab_mic), "🎙️ Mic Lọc Âm AI (Kênh B)")
        
        # ==========================================================
        # TAB 2: 🌐 Kênh A: Nhạc Web / YouTube (2 Kênh Chống Vọng)
        # ==========================================================
        tab_music = QWidget()
        tab_music.setObjectName("tabScrollContent")
        layout_tab_music = QVBoxLayout(tab_music)
        layout_tab_music.setSpacing(10)
        layout_tab_music.setContentsMargins(10, 10, 10, 10)
        
        group_web = QFrame()
        group_web.setProperty("class", "innerGroup")
        web_vbox = QVBoxLayout(group_web)
        web_vbox.setSpacing(7)
        web_vbox.setContentsMargins(12, 9, 12, 9)
        
        lbl_gw = QLabel("KÊNH A: NHẬN NHẠC TRÌNH DUYỆT ➔ PHÁT RA TAI & DISCORD")
        lbl_gw.setProperty("class", "groupTitle")
        web_vbox.addWidget(lbl_gw)
        
        # Hàng 1: Cổng nhận nhạc từ Chrome
        min_header = QHBoxLayout()
        lbl_min = QLabel("📻 Cổng nhận nhạc (Chrome):")
        lbl_min.setProperty("class", "fieldLabel")
        vac_btn = QPushButton("🔌 Cài Line 1", objectName="vbcableBtn")
        vac_btn.setFixedHeight(22)
        vac_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        vac_btn.clicked.connect(self.install_vac_action)
        min_header.addWidget(lbl_min)
        min_header.addStretch()
        min_header.addWidget(vac_btn)
        web_vbox.addLayout(min_header)
        
        self.music_in_combo = QComboBox()
        self.music_in_combo.setFixedHeight(28)
        self.music_in_combo.currentIndexChanged.connect(self.on_web_device_changed)
        web_vbox.addWidget(self.music_in_combo)
        
        # Hàng 2: Tai nghe / Loa ngoài (Tự động theo Windows hoặc chọn cố định)
        lbl_hp = QLabel("🎧 Tai nghe / Loa ngoài (Nghe nhạc):")
        lbl_hp.setProperty("class", "fieldLabel")
        web_vbox.addWidget(lbl_hp)
        self.hp_combo = QComboBox()
        self.hp_combo.setFixedHeight(28)
        self.hp_combo.currentIndexChanged.connect(self.on_web_device_changed)
        web_vbox.addWidget(self.hp_combo)
        
        # Hàng Toggles (Tách biệt độc lập: Phát ra tai nghe VS Gửi sang Discord / Game)
        row_toggles_1 = QHBoxLayout()
        row_toggles_1.setSpacing(14)
        self.web_to_hp_cb = QCheckBox("🎧 Phát ra tai nghe / loa")
        self.web_to_hp_cb.setChecked(False)
        self.web_to_hp_cb.setToolTip("Bật để bạn nghe nhạc ở tai nghe hoặc loa ngoài. Tắt đi nếu bạn không muốn nghe mà chỉ muốn truyền cho đồng đội.")
        self.web_to_hp_cb.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.web_to_hp_cb.toggled.connect(self.sync_web_stream)
        row_toggles_1.addWidget(self.web_to_hp_cb)
        
        self.web_to_mic_cb = QCheckBox("📻 Trộn vào Mic (Game / App)")
        self.web_to_mic_cb.setChecked(False)
        self.web_to_mic_cb.setToolTip("Bật để truyền nhạc vào Microphone ảo cho mọi Game, Discord, OBS, Zoom...")
        self.web_to_mic_cb.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.web_to_mic_cb.toggled.connect(self.sync_web_stream)
        row_toggles_1.addWidget(self.web_to_mic_cb)
        row_toggles_1.addStretch()
        web_vbox.addLayout(row_toggles_1)
        
        row_toggles_2 = QHBoxLayout()
        row_toggles_2.setSpacing(14)
        self.ducking_cb = QCheckBox("🎙️ Tự hạ nhạc khi nói")
        self.ducking_cb.setChecked(False)
        self.ducking_cb.setToolTip("Tự động giảm âm lượng nhạc khi bạn nói vào mic để giọng nói không bị đè lấn át")
        self.ducking_cb.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.ducking_cb.toggled.connect(self.toggle_ducking)
        row_toggles_2.addWidget(self.ducking_cb)
        row_toggles_2.addStretch()
        
        open_mixer_btn = QPushButton("⚙️ Windows Mixer", objectName="actionBtn")
        open_mixer_btn.setFixedHeight(24)
        open_mixer_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        open_mixer_btn.clicked.connect(self.open_volume_mixer_action)
        row_toggles_2.addWidget(open_mixer_btn)
        web_vbox.addLayout(row_toggles_2)
        
        # Sliders âm lượng
        hp_head = QHBoxLayout()
        hp_lbl = QLabel("Âm lượng trong tai bạn:")
        hp_lbl.setProperty("class", "fieldLabel")
        self.hp_vol_val_label = QLabel("50%")
        self.hp_vol_val_label.setStyleSheet("color: #10B981; background-color: rgba(16, 185, 129, 0.12); border: 1px solid rgba(16, 185, 129, 0.25); border-radius: 4px; padding: 1px 6px; font-weight: 700; font-size: 11px;")
        hp_head.addWidget(hp_lbl)
        hp_head.addStretch()
        hp_head.addWidget(self.hp_vol_val_label)
        web_vbox.addLayout(hp_head)
        
        self.hp_vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.hp_vol_slider.setRange(0, 100)
        self.hp_vol_slider.setValue(50)
        self.hp_vol_slider.setFixedHeight(18)
        self.hp_vol_slider.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.hp_vol_slider.valueChanged.connect(self.change_hp_music_volume)
        web_vbox.addWidget(self.hp_vol_slider)
        
        disc_head = QHBoxLayout()
        disc_lbl = QLabel("Âm lượng gửi vào Mic (Game / App):")
        disc_lbl.setProperty("class", "fieldLabel")
        self.discord_vol_val_label = QLabel("50%")
        self.discord_vol_val_label.setStyleSheet("color: #C084FC; background-color: rgba(192, 132, 252, 0.12); border: 1px solid rgba(192, 132, 252, 0.25); border-radius: 4px; padding: 1px 6px; font-weight: 700; font-size: 11px;")
        disc_head.addWidget(disc_lbl)
        disc_head.addStretch()
        disc_head.addWidget(self.discord_vol_val_label)
        web_vbox.addLayout(disc_head)
        
        self.discord_vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.discord_vol_slider.setRange(0, 100)
        self.discord_vol_slider.setValue(50)
        self.discord_vol_slider.setFixedHeight(18)
        self.discord_vol_slider.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.discord_vol_slider.valueChanged.connect(self.change_discord_music_volume)
        web_vbox.addWidget(self.discord_vol_slider)
        
        layout_tab_music.addWidget(group_web)
        
        # Hướng dẫn 3 bước chuẩn 2 kênh (Dạng thanh ngang siêu gọn)
        guide_card = QFrame()
        guide_card.setProperty("class", "innerGroup")
        guide_vbox = QVBoxLayout(guide_card)
        guide_vbox.setSpacing(3)
        guide_vbox.setContentsMargins(12, 7, 12, 7)
        
        lbl_g_guide = QLabel("⚡ 3 BƯỚC THIẾT LẬP:")
        lbl_g_guide.setStyleSheet("color: #FBBF24; font-weight: 700; font-size: 10px;")
        guide_vbox.addWidget(lbl_g_guide)
        
        g_steps = QLabel("1️⃣ Chrome: <b>Line 1</b> • 2️⃣ Mic Game/App/Discord: <b>CABLE Output</b> • 3️⃣ Loa: <b>Tai nghe</b>")
        g_steps.setStyleSheet("color: #CBD5E1; font-size: 10px;")
        g_steps.setWordWrap(True)
        guide_vbox.addWidget(g_steps)
        
        g_done = QLabel("🎉 <i>Bạn nghe Nhạc + Game. Đồng đội nghe Giọng bạn + Nhạc. Không bị vọng!</i>")
        g_done.setStyleSheet("color: #34D399; font-size: 10px; padding-top: 1px;")
        g_done.setWordWrap(True)
        guide_vbox.addWidget(g_done)
        
        layout_tab_music.addWidget(guide_card)
        layout_tab_music.addStretch()
        
        self.tabs.addTab(create_tab_scroll(tab_music), "🌐 Nhạc 2 Kênh (Kênh A)")
        
        main_layout.addWidget(self.tabs, stretch=1)
        
        # 3. Cường độ âm thanh (VU Meters) - Giao diện ngang studio hiện đại, gọn gàng
        meter_card = QFrame()
        meter_card.setProperty("class", "innerGroup")
        meter_vbox = QVBoxLayout(meter_card)
        meter_vbox.setSpacing(6)
        meter_vbox.setContentsMargins(12, 8, 12, 8)
        
        # Mic Vào Meter
        in_m_box = QHBoxLayout()
        in_m_box.setSpacing(8)
        lbl_im = QLabel("Mic Vào:")
        lbl_im.setFixedWidth(55)
        lbl_im.setStyleSheet("color: #94A3B8; font-weight: 600; font-size: 11px;")
        self.in_meter = QProgressBar()
        self.in_meter.setRange(0, 100)
        self.in_meter.setValue(0)
        self.in_meter.setFixedHeight(9)
        self.in_meter.setTextVisible(False)
        in_m_box.addWidget(lbl_im)
        in_m_box.addWidget(self.in_meter)
        meter_vbox.addLayout(in_m_box)
        
        # Mic Ra Meter
        out_m_box = QHBoxLayout()
        out_m_box.setSpacing(8)
        lbl_om = QLabel("Mic Ra:")
        lbl_om.setFixedWidth(55)
        lbl_om.setStyleSheet("color: #94A3B8; font-weight: 600; font-size: 11px;")
        self.out_meter = QProgressBar()
        self.out_meter.setRange(0, 100)
        self.out_meter.setValue(0)
        self.out_meter.setFixedHeight(9)
        self.out_meter.setTextVisible(False)
        out_m_box.addWidget(lbl_om)
        out_m_box.addWidget(self.out_meter)
        meter_vbox.addLayout(out_m_box)
        
        # Speech indicator status
        self.speech_indicator = QLabel("● Trạng thái giọng nói: 0%")
        self.speech_indicator.setStyleSheet("color: #64748B; font-size: 10px; font-weight: 600; padding-top: 1px;")
        meter_vbox.addWidget(self.speech_indicator)
        
        main_layout.addWidget(meter_card)
        
        # 4. Action Buttons (BẬT / DỪNG)
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        self.start_btn = QPushButton("▶  BẬT MIC LỌC ÂM", objectName="startBtn")
        self.start_btn.setFixedHeight(40)
        self.start_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.start_btn.clicked.connect(self.start_audio)
        btn_layout.addWidget(self.start_btn, stretch=3)
        
        self.stop_btn = QPushButton("⏹  TẮT MIC", objectName="stopBtn")
        self.stop_btn.setFixedHeight(40)
        self.stop_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_audio)
        btn_layout.addWidget(self.stop_btn, stretch=2)
        main_layout.addLayout(btn_layout)
        
        # 5. Thanh chân trang tích hợp (Autostart + Tray Checkbox + Hướng dẫn nhanh + Ghi nhớ cấu hình)
        footer_vbox = QVBoxLayout()
        footer_vbox.setSpacing(4)
        footer_vbox.setContentsMargins(2, 3, 2, 2)
        
        row1 = QHBoxLayout()
        row1.setSpacing(14)
        
        self.autostart_cb = QCheckBox("🚀 Khởi động cùng Windows")
        self.autostart_cb.setStyleSheet("color: #94A3B8; font-size: 11px; font-weight: 500;")
        self.autostart_cb.setChecked(is_autostart_enabled())
        self.autostart_cb.toggled.connect(self.toggle_autostart)
        row1.addWidget(self.autostart_cb)
        
        self.auto_start_mic_cb = QCheckBox("⚡ Tự bật Mic khi mở app")
        self.auto_start_mic_cb.setStyleSheet("color: #94A3B8; font-size: 11px; font-weight: 500;")
        self.auto_start_mic_cb.setToolTip("Tự động kích hoạt lọc âm Mic ngay khi mở ứng dụng")
        self.auto_start_mic_cb.setChecked(True)
        row1.addWidget(self.auto_start_mic_cb)

        self.tray_cb = QCheckBox("Chạy ngầm Taskbar (X)")
        self.tray_cb.setStyleSheet("color: #94A3B8; font-size: 11px; font-weight: 500;")
        self.tray_cb.setChecked(True)
        row1.addWidget(self.tray_cb)
        row1.addStretch()
        footer_vbox.addLayout(row1)
        
        row2 = QHBoxLayout()
        row2.setSpacing(10)
        
        self.cfg_status_lbl = QLabel("💾 Tự động ghi nhớ cấu hình")
        self.cfg_status_lbl.setStyleSheet("color: #10B981; font-size: 10px; font-weight: 500;")
        row2.addWidget(self.cfg_status_lbl)
        row2.addStretch()
        
        tip_text = QLabel("💡 Mic Game & App: 'CABLE Output'")
        tip_text.setStyleSheet("color: #FBBF24; font-size: 10px; font-weight: 600; background: rgba(245, 158, 11, 0.1); border: 1px solid rgba(245, 158, 11, 0.2); border-radius: 5px; padding: 2px 7px;")
        row2.addWidget(tip_text)
        footer_vbox.addLayout(row2)
        
        main_layout.addLayout(footer_vbox)


    def toggle_autostart(self, checked):
        success = set_autostart(checked)
        if not success:
            self.autostart_cb.blockSignals(True)
            self.autostart_cb.setChecked(not checked)
            self.autostart_cb.blockSignals(False)
            QMessageBox.warning(self, "Lỗi thiết lập", "Không thể cập nhật cấu hình khởi động cùng Windows!")

    def check_default_device_change(self):
        current_fingerprint = get_audio_endpoints_fingerprint()
        current_ep_id, current_ep_name = get_windows_default_playback()
        
        # 1. Kiểm tra xem phần cứng âm thanh Windows có thay đổi không (cắm/rút tai nghe, tắt/bật Bluetooth)
        fingerprint_changed = (
            hasattr(self, '_last_endpoints_fingerprint')
            and self._last_endpoints_fingerprint != current_fingerprint
        )

        # 2. Kiểm tra xem Windows có đổi thiết bị phát mặc định không (kết nối Bluetooth, đổi trên Taskbar)
        default_changed = (
            current_ep_id
            and hasattr(self, '_last_default_playback_ep')
            and self._last_default_playback_ep != current_ep_id
        )

        if fingerprint_changed or default_changed:
            self._last_endpoints_fingerprint = current_fingerprint
            if current_ep_id:
                self._last_default_playback_ep = current_ep_id
            
            # Trì hoãn 0.8 giây để Windows và driver Bluetooth hoàn tất kết nối A2DP
            if not getattr(self, '_device_switch_pending', False):
                self._device_switch_pending = True
                QTimer.singleShot(800, self._perform_device_switch)
            return

        # 3. Watchdog tự phục hồi Mic lọc âm: Kiểm tra nếu Mic bị ngắt ngầm (inactive stream) thì lập tức hồi sinh
        if self.engine.is_running and not getattr(self, '_device_switch_pending', False):
            stream_dead = (self.engine.stream is None) or (not getattr(self.engine.stream, 'active', False))
            if stream_dead:
                in_idx = self.input_combo.currentData()
                out_idx = self.output_combo.currentData()
                if in_idx is not None and out_idx is not None:
                    self.engine.start(input_device_id=in_idx, output_device_id=out_idx)

        # 4. Watchdog tự phục hồi Kênh A (Line 1 Web Pass-through): Nếu checkbox đang bật mà stream bị tắt ngầm -> Tự động hồi sinh!
        if (self.web_to_hp_cb.isChecked() or self.web_to_mic_cb.isChecked()) and not getattr(self, '_device_switch_pending', False):
            hp_on = self.web_to_hp_cb.isChecked()
            in_dead = (self.engine.web_input_stream is None) or (not getattr(self.engine.web_input_stream, 'active', False))
            out_dead = hp_on and (
                (self.engine.web_output_stream is None) or (not getattr(self.engine.web_output_stream, 'active', False))
            )
            cable_dead = (self.engine.web_cable_stream is not None) and (not getattr(self.engine.web_cable_stream, 'active', False))
            
            if in_dead or out_dead or cable_dead or not self.engine.web_passthrough_enabled:
                self.sync_web_stream()

    def _retry_start_mic(self):
        """Thử lại khởi động luồng mic nếu lần đầu bị khóa driver"""
        if self.engine.is_running and (self.engine.stream is None or not getattr(self.engine.stream, 'active', False)):
            in_idx = self.input_combo.currentData()
            out_idx = self.output_combo.currentData()
            if in_idx is not None and out_idx is not None:
                self.engine.start(input_device_id=in_idx, output_device_id=out_idx)

    def _perform_device_switch(self):
        self._device_switch_pending = False
        
        # 1. Lưu lại TÊN thiết bị đang chọn vào settings
        saved_mic_running = self.engine.is_running
        saved_in_name = self.input_combo.currentText().strip()
        saved_out_name = self.output_combo.currentText().strip()
        saved_music_in_name = self.music_in_combo.currentText().strip()
        saved_hp_data = self.hp_combo.currentData()
        
        if saved_in_name and not saved_in_name.startswith("❌"):
            self.settings.setdefault("audio_devices", {})["input_mic"] = saved_in_name
        if saved_out_name and not saved_out_name.startswith("❌"):
            self.settings.setdefault("audio_devices", {})["output_mic"] = saved_out_name
        if saved_music_in_name and not saved_music_in_name.startswith("❌"):
            self.settings.setdefault("audio_devices", {})["music_input"] = saved_music_in_name
        if saved_hp_data == "default":
            self.settings.setdefault("audio_devices", {})["headphone_output"] = "default"
        elif saved_hp_data is None:
            self.settings.setdefault("audio_devices", {})["headphone_output"] = "none"
        else:
            raw_hp = self.hp_combo.currentText().strip()
            if raw_hp.startswith("🎧 "):
                raw_hp = raw_hp[2:].strip()
            self.settings.setdefault("audio_devices", {})["headphone_output"] = raw_hp
        
        # 2. Dừng an toàn luồng âm thanh
        self.engine.stop_web_passthrough()
        if saved_mic_running:
            self.engine.stop()
            
        try:
            sd._terminate()
            sd._initialize()
        except Exception:
            pass
            
        # 3. Cập nhật lại danh sách thiết bị trên giao diện và tự động khôi phục đúng thiết bị theo tên
        self.refresh_devices()
                    
        # 4. Khôi phục luồng mic lọc âm với ID MỚI HỢP LỆ sau khi re-init
        if saved_mic_running:
            new_in_id = self.input_combo.currentData()
            new_out_id = self.output_combo.currentData()
            if new_in_id is not None and new_out_id is not None:
                success, msg = self.engine.start(input_device_id=new_in_id, output_device_id=new_out_id)
                if not success:
                    # Tự động thử lại sau 1.5 giây nếu Windows đang bận cấu hình driver
                    QTimer.singleShot(1500, self._retry_start_mic)
                
        # 5. Tự động kết nối lại luồng phát nhạc sang tai nghe / loa mới
        if self.web_to_hp_cb.isChecked() or self.web_to_mic_cb.isChecked():
            self.sync_web_stream()

    def init_tray(self):

        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(self.app_icon)
        self.tray.setToolTip("AI Mic Noise Canceller")
        
        menu = QMenu()
        show_action = menu.addAction("Mở giao diện")
        show_action.triggered.connect(self.show_window)
        menu.addSeparator()
        quit_action = menu.addAction("Thoát hoàn toàn")
        quit_action.triggered.connect(self.quit_application)
        
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.on_tray_activated)
        self.tray.show()

    def show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        try:
            hwnd = int(self.winId())
            if hwnd:
                user32 = ctypes.windll.user32
                # SW_RESTORE = 9
                user32.ShowWindow(hwnd, 9)
                user32.SetForegroundWindow(hwnd)
        except Exception:
            pass

    def on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            if self.isVisible():
                self.hide()
            else:
                self.show_window()

    def closeEvent(self, event):
        self.save_settings_immediately()
        if self.tray_cb.isChecked():
            event.ignore()
            self.hide()
            self.tray.showMessage(
                "AI Mic Noise Canceller",
                "Ứng dụng đang chạy ngầm ở góc phải Taskbar. Nhấp vào icon hình Mic để mở lại.",
                self.app_icon,
                2500
            )
        else:
            self.quit_application()
            event.accept()

    def quit_application(self):
        self.save_settings_immediately()
        self.stop_audio()
        self.engine.stop_web_passthrough()
        try:
            QLocalServer.removeServer(SINGLE_INSTANCE_IPC_NAME)
        except Exception:
            pass
        if self.tray:
            self.tray.hide()
        QApplication.instance().quit()

    def check_output_selection(self, index):
        out_text = self.output_combo.currentText().lower()
        if out_text:
            if "line" in out_text:
                self.echo_warn_lbl.setText(
                    "⚠️ KÊNH B (MIC) ĐANG CHỌN 'LINE 1':\n"
                    "Line 1 là Cáp của Kênh A (Nhạc)! Nếu đổ tiếng mic vào Line 1 thì tai bạn sẽ bị nghe tiếng mic!\n"
                    "👉 Hãy chọn 'CABLE Input' ở ô này để dẫn tiếng mic sang Discord mà không bị vọng."
                )
                self.echo_warn_lbl.setVisible(True)
            elif "cable input" not in out_text and "cable in" not in out_text:
                self.echo_warn_lbl.setText(
                    "⚠️ BẠN ĐANG CHỌN TAI NGHE / LOA THẬT:\n"
                    "Giọng mic của bạn sẽ phát thẳng vào tai gây vọng tiếng!\n"
                    "👉 Hãy chọn 'CABLE Input' để đưa tiếng mic vào Game/Discord mà không bị vọng."
                )
                self.echo_warn_lbl.setVisible(True)
            else:
                self.echo_warn_lbl.setVisible(False)

    def open_sound_control_panel(self):
        try:
            subprocess.Popen("control mmsys.cpl sounds", shell=True)
        except Exception:
            try:
                subprocess.Popen("mmsys.cpl", shell=True)
            except Exception as e:
                pass
                
        QMessageBox.information(
            self,
            "Cách tắt tiếng vọng Mic trong Windows",
            "💡 HƯỚNG DẪN 3 BƯỚC ĐỂ HẾT VỌNG TIẾNG HOÀN TOÀN:\n\n"
            "1. Cửa sổ 'Sound' vừa mở ra trên màn hình của bạn.\n"
            "2. Bấm chọn thẻ 'Recording' (Ghi âm).\n"
            "3. Nhấp đúp vào 'CABLE Output' (hoặc Microphone thật của bạn) ➔ Chọn thẻ 'Listen'.\n"
            "4. BỎ DẤU TÍCH [ ] ở ô 'Listen to this device' (Nghe thiết bị này) ➔ Bấm OK.\n\n"
            "✓ Bạn sẽ không còn bị vọng lại bất kỳ tiếng nói nào từ Mic vào tai nghe nữa!"
        )

    def refresh_devices(self):
        inputs, outputs = self.engine.get_devices()
        self.input_devices_list = inputs
        self.output_devices_list = outputs
        
        self.input_combo.blockSignals(True)
        self.output_combo.blockSignals(True)
        self.music_in_combo.blockSignals(True)
        self.hp_combo.blockSignals(True)
        
        try:
            # 1. Input mic (Microphone vật lý của bạn)
            self.input_combo.clear()
            real_mic_idx = -1
            target_in_idx = -1
            saved_in_name = self.settings.get("audio_devices", {}).get("input_mic", "").strip()

            for idx, dev in enumerate(inputs):
                name = dev['name']
                text = f"{name}"
                self.input_combo.addItem(text, dev['id'])
                self.input_combo.setItemData(idx, text, Qt.ItemDataRole.ToolTipRole)
                
                # Khớp với thiết bị đã lưu
                if saved_in_name and target_in_idx == -1:
                    if (saved_in_name.lower() == name.lower() or 
                        saved_in_name.lower() in name.lower() or 
                        name.lower() in saved_in_name.lower()):
                        target_in_idx = idx

                n_low = name.lower()
                # Ưu tiên tìm Microphone thật (Microphone Array, Realtek Mic, Intel Mic)
                if ('mic' in n_low or 'array' in n_low) and 'cable' not in n_low and 'line' not in n_low and real_mic_idx == -1:
                    real_mic_idx = idx
                    
            if target_in_idx != -1:
                self.input_combo.setCurrentIndex(target_in_idx)
            elif real_mic_idx != -1:
                self.input_combo.setCurrentIndex(real_mic_idx)
            elif self.input_combo.count() > 0:
                self.input_combo.setCurrentIndex(0)
                
            # 2. Output mic (CABLE Input - Kênh B cho Discord)
            self.output_combo.clear()
            cable_in_idx = -1
            target_out_idx = -1
            saved_out_name = self.settings.get("audio_devices", {}).get("output_mic", "").strip()

            for idx, dev in enumerate(outputs):
                name = dev['name']
                text = f"{name}"
                self.output_combo.addItem(text, dev['id'])
                self.output_combo.setItemData(idx, text, Qt.ItemDataRole.ToolTipRole)
                
                # Khớp với thiết bị đã lưu
                if saved_out_name and target_out_idx == -1:
                    if (saved_out_name.lower() == name.lower() or 
                        saved_out_name.lower() in name.lower() or 
                        name.lower() in saved_out_name.lower()):
                        target_out_idx = idx

                # Ưu tiên tìm CABLE Input chính xác
                if "cable input" in name.lower() and cable_in_idx == -1:
                    cable_in_idx = idx
                    
            if cable_in_idx == -1:
                for idx, dev in enumerate(outputs):
                    if "cable" in dev['name'].lower():
                        cable_in_idx = idx
                        break
                    
            if target_out_idx != -1:
                self.output_combo.setCurrentIndex(target_out_idx)
            elif cable_in_idx != -1:
                self.output_combo.setCurrentIndex(cable_in_idx)
            elif self.output_combo.count() > 0:
                self.output_combo.setCurrentIndex(0)

            # 3. Kênh A: Cổng nhận nhạc từ Chrome (Cáp A Input - Line 1)
            self.music_in_combo.clear()
            vac_line_idx = -1
            target_music_in_idx = -1
            saved_music_in_name = self.settings.get("audio_devices", {}).get("music_input", "").strip()

            for idx, dev in enumerate(inputs):
                name = dev['name']
                text = f"{name}"
                self.music_in_combo.addItem(text, dev['id'])
                self.music_in_combo.setItemData(idx, text, Qt.ItemDataRole.ToolTipRole)
                
                # Khớp với thiết bị đã lưu
                if saved_music_in_name and target_music_in_idx == -1:
                    if (saved_music_in_name.lower() == name.lower() or 
                        saved_music_in_name.lower() in name.lower() or 
                        name.lower() in saved_music_in_name.lower()):
                        target_music_in_idx = idx

                # Ưu tiên tìm Line 1 (Cáp nhận nhạc Kênh A)
                if ("line" in name.lower() or "vac" in name.lower()) and vac_line_idx == -1:
                    vac_line_idx = idx

            if target_music_in_idx != -1:
                self.music_in_combo.setCurrentIndex(target_music_in_idx)
            elif vac_line_idx != -1:
                self.music_in_combo.setCurrentIndex(vac_line_idx)
            else:
                self.music_in_combo.addItem("❌ Chưa cài Line 1 (Bấm nút 'Cài Line 1' ở trên)", None)
                self.music_in_combo.setCurrentIndex(self.music_in_combo.count() - 1)
                
            # 4. Tai nghe / Loa thật của người dùng (Kênh A Output)
            self.hp_combo.clear()
            _, current_default_name = get_windows_default_playback()
            def_text = f"🔊 [Mặc định] Theo Windows (Hiện tại: {current_default_name})" if current_default_name else "🔊 [Mặc định] Theo thiết bị Windows (Tự đổi Tai nghe / Loa)"
            self.hp_combo.addItem(def_text, "default")
            self.hp_combo.addItem("❌ Không nghe kèm tai nghe / loa", None)
            
            saved_hp = self.settings.get("audio_devices", {}).get("headphone_output", "default")
            target_hp_idx = -1

            for idx, dev in enumerate(outputs):
                name = dev['name']
                n_low = name.lower()
                if "cable" not in n_low and "line" not in n_low and "virtual" not in n_low:
                    combo_idx = self.hp_combo.count()
                    self.hp_combo.addItem(f"🎧 {name}", dev['id'])
                    
                    if saved_hp not in ("default", "none", "") and target_hp_idx == -1:
                        if (saved_hp.lower() == name.lower() or 
                            saved_hp.lower() in name.lower() or 
                            name.lower() in saved_hp.lower()):
                            target_hp_idx = combo_idx
                        
            if saved_hp == "none":
                self.hp_combo.setCurrentIndex(1)
            elif target_hp_idx != -1:
                self.hp_combo.setCurrentIndex(target_hp_idx)
            else:
                # Mặc định chọn mục 0: Tự động theo Windows (khi cất tai nghe sẽ tự phát ra loa ngoài)
                self.hp_combo.setCurrentIndex(0)
        finally:
            self.input_combo.blockSignals(False)
            self.output_combo.blockSignals(False)
            self.music_in_combo.blockSignals(False)
            self.hp_combo.blockSignals(False)
            
        self.check_output_selection(self.output_combo.currentIndex())

    def open_volume_mixer_action(self):
        try:
            subprocess.Popen("start ms-settings:apps-volume", shell=True)
        except Exception:
            try:
                subprocess.Popen("sndvol.exe", shell=True)
            except Exception as e:
                QMessageBox.warning(self, "Lỗi", f"Không mở được Volume Mixer: {e}")

    def on_web_device_changed(self, index):
        if self.web_to_hp_cb.isChecked() or self.web_to_mic_cb.isChecked():
            self.sync_web_stream()

    def sync_web_stream(self):
        if getattr(self, '_web_sync_in_progress', False):
            return
        self._web_sync_in_progress = True
        try:
            hp_enabled = self.web_to_hp_cb.isChecked()
            discord_enabled = self.web_to_mic_cb.isChecked()
            
            self.engine.headphone_music_enabled = hp_enabled
            self.engine.web_music_to_mic = discord_enabled
            
            # Nếu cả 2 công tắc đều tắt -> dừng thu âm Line 1
            if not hp_enabled and not discord_enabled:
                self.engine.stop_web_passthrough()
                return
                
            music_in_id = self.music_in_combo.currentData()
            music_in_name = self.music_in_combo.currentText().lower()
            hp_id = self.hp_combo.currentData()
            
            # Cảnh báo nghiêm ngặt nếu Kênh A chọn nhầm Cáp Mic của Kênh B (CABLE Output)
            if ("cable output" in music_in_name or "vb-audio" in music_in_name) and "line" not in music_in_name:
                reply = QMessageBox.warning(
                    self,
                    "Cảnh báo chọn Cáp Kênh A",
                    "⚠️ Kênh A đang chọn 'CABLE Output' (Cáp Mic của Kênh B)!\n"
                    "Điều này sẽ khiến tai nghe của bạn bị nghe lại tiếng mic của chính mình.\n\n"
                    "👉 Hãy chọn Kênh A là 'Line 1 (Virtual Audio Cable)'!\n"
                    "(Nếu máy bạn đã cài Line 1, hãy chọn Line 1 ở ô trên).\n"
                    "Bạn có chắc muốn tiếp tục không?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                if reply != QMessageBox.StandardButton.Yes:
                    self.web_to_hp_cb.blockSignals(True)
                    self.web_to_mic_cb.blockSignals(True)
                    self.web_to_hp_cb.setChecked(False)
                    self.web_to_mic_cb.setChecked(False)
                    self.web_to_hp_cb.blockSignals(False)
                    self.web_to_mic_cb.blockSignals(False)
                    self.engine.stop_web_passthrough()
                    return
                    
            if music_in_id is None:
                return

            if self.hp_combo.currentIndex() == -1:
                return

            if hp_id is None:
                # Người dùng chọn "Không nghe kèm tai nghe / loa"
                self.engine.headphone_music_enabled = False
                if not discord_enabled:
                    self.engine.stop_web_passthrough()
                    return
                if len(self.output_devices_list) > 0:
                    hp_id = self.output_devices_list[0]['id']
                else:
                    return

            # Kiểm tra trạng thái hoạt động thực tế của các luồng âm thanh
            hp_on = self.web_to_hp_cb.isChecked()
            in_dead = (self.engine.web_input_stream is None) or (not getattr(self.engine.web_input_stream, 'active', False))
            out_dead = hp_on and (
                (self.engine.web_output_stream is None) or (not getattr(self.engine.web_output_stream, 'active', False))
            )

            # Khởi động hoặc cập nhật stream nếu thiết bị thay đổi hoặc stream bị ngắt
            need_restart = (
                not self.engine.web_passthrough_enabled
                or self.engine.web_in_dev_id != music_in_id
                or getattr(self.engine, 'web_req_out_dev_id', None) != hp_id
                or in_dead
                or out_dead
            )
            if not need_restart and hp_id == "default":
                try:
                    _, def_name = get_windows_default_playback()
                    if def_name and music_in_id is not None:
                        cable_hostapi = sd.query_devices(music_in_id)['hostapi']
                        curr_def = find_sd_output_device_by_name(def_name, preferred_hostapi=cable_hostapi)
                        if curr_def is not None and self.engine.web_out_dev_id != curr_def:
                            need_restart = True
                except Exception:
                    pass

            if need_restart:
                success, msg = self.engine.start_web_passthrough(music_in_id, hp_id)
                if not success:
                    # Nếu thiết bị mới (tai nghe Bluetooth/USB) đang trong quá trình nhận diện hoặc bận,
                    # TUYỆT ĐỐI KHÔNG tự động hủy chọn checkbox của người dùng và KHÔNG hiện dialog chặn đứng app!
                    # Lên lịch tự động thử lại êm dịu sau 1.5 giây
                    if not hasattr(self, '_web_sync_retry_count'):
                        self._web_sync_retry_count = 0
                    if self._web_sync_retry_count < 3:
                        self._web_sync_retry_count += 1
                        QTimer.singleShot(1500, self.sync_web_stream)
                    else:
                        self._web_sync_retry_count = 0
                else:
                    self._web_sync_retry_count = 0
        finally:
            self._web_sync_in_progress = False


    def change_hp_music_volume(self, val):
        self.hp_vol_val_label.setText(f"{val}%")
        self.engine.headphone_music_volume = val / 100.0

    def change_discord_music_volume(self, val):
        self.discord_vol_val_label.setText(f"{val}%")
        self.engine.web_to_mic_volume = val / 100.0

    def toggle_ducking(self, checked):
        self.engine.auto_ducking_enabled = checked

    def install_vac_action(self):
        reply = QMessageBox.question(
            self,
            "Cài đặt Cáp ảo Line 1 (VAC Lite)",
            "Ứng dụng sẽ mở bộ cài Virtual Audio Cable (Line 1) để tạo Cáp ảo Kênh A nhận nhạc từ Chrome.\nBạn có muốn tiếp tục?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            import threading
            import install_vac
            
            def run_install():
                try:
                    d = install_vac.download_and_extract_vac()
                    install_vac.run_installer(d)
                except Exception as e:
                    print(f"Error installing VAC: {e}")
                    
            threading.Thread(target=run_install, daemon=True).start()
            QMessageBox.information(
                self,
                "Đang mở trình cài đặt",
                "Đang mở trình cài đặt Line 1. Vui lòng bấm 'Install' trên cửa sổ hiện ra và nhấn 'Làm mới thiết bị' sau khi cài xong!"
            )

    def change_headphone_device(self, index):
        hp_id = self.hp_combo.currentData()
        self.engine.start_headphone_monitor(hp_id)
        self.on_web_device_changed(index)

    def toggle_denoise(self, checked):
        self.engine.denoise_enabled = checked

    def change_noise_gate(self, val):
        self.gate_val_label.setText(f"{val}%")
        # val chạy từ 0 đến 50:
        # 0%  -> 0.002 (thu nhạy thì thầm)
        # 10% (mặc định) -> 0.008 (chuẩn giọng nói gần, khóa sạch tiếng ồn/TikTok xa)
        # 25% - 50% -> 0.017 - 0.032 (chặn cực mạnh cho môi trường rất ồn)
        ratio = val / 50.0
        self.engine.noise_gate_threshold = 0.002 + ratio * 0.030
        self.engine.vad_threshold = 0.20 + ratio * 0.40


    def change_gain(self, val):
        self.gain_val_label.setText(f"{val}%")
        self.engine.gain = val / 100.0

    def _on_meter_engine_callback(self, in_lvl, out_lvl, prob):
        # Khi thu nhỏ xuống khay hệ thống (chạy nền), bỏ qua hoàn toàn việc vẽ UI để đạt 0% CPU GUI
        if not self.isVisible():
            return
        # Giới hạn tần số vẽ thanh âm lượng ở mức ~33 FPS (mỗi 3 frame = 30ms) thay vì 100 FPS
        self._meter_frame_count = getattr(self, '_meter_frame_count', 0) + 1
        if self._meter_frame_count % 3 != 0:
            return
        self.signals.meter_signal.emit(in_lvl, out_lvl, prob)

    def update_meters_ui(self, in_lvl, out_lvl, prob):
        if not self.isVisible():
            return
        self.in_meter.setValue(int(in_lvl * 100))
        self.out_meter.setValue(int(out_lvl * 100))
        percent_prob = int(prob * 100)
        color = "#10B981" if percent_prob > 50 else "#64748B"
        self.speech_indicator.setText(f"● Phát hiện giọng nói: {percent_prob}%")
        if getattr(self, '_last_speech_color', None) != color:
            self._last_speech_color = color
            self.speech_indicator.setStyleSheet(f"color: {color}; font-size: 11px;")

    def start_audio(self):
        if self.engine.is_running:
            return

        in_idx = self.input_combo.currentData()
        out_idx = self.output_combo.currentData()
        out_name = self.output_combo.currentText().lower()
        
        if in_idx is None or out_idx is None:
            if self.isVisible():
                QMessageBox.warning(self, "Cảnh báo", "Vui lòng chọn đầy đủ thiết bị Input và Output!")
            return
            
        # Kiểm tra nếu người dùng chọn nhầm Cổng ra Mic là Line 1
        if "line" in out_name:
            if self.isVisible():
                reply = QMessageBox.critical(
                    self,
                    "Lỗi cấu hình Cổng ra Mic!",
                    "⚠️ BẠN ĐANG CHỌN CỔNG RA MIC LÀ 'LINE 1'!\n\n"
                    "• 'Line 1' là Cáp của Kênh A (chỉ dùng để nhận nhạc từ Chrome).\n"
                    "• Nếu đổ tiếng mic vào Line 1 thì toàn bộ giọng nói của bạn sẽ bị dội thẳng vào tai nghe!\n\n"
                    "👉 Hãy chọn Cổng ra Mic là 'CABLE Input' (Kênh B cho Discord) để không bị vọng.\n\n"
                    "Bạn có muốn ứng dụng tự động chuyển về 'CABLE Input' ngay không?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                if reply == QMessageBox.StandardButton.Yes:
                    for i in range(self.output_combo.count()):
                        if "cable input" in self.output_combo.itemText(i).lower():
                            self.output_combo.setCurrentIndex(i)
                            out_idx = self.output_combo.currentData()
                            break
                else:
                    return

        elif "cable input" not in out_name and "cable in" not in out_name:
            if self.isVisible():
                reply = QMessageBox.warning(
                    self,
                    "Cảnh báo Cổng ra Mic",
                    "⚠️ Bạn đang chọn Cổng ra Mic là Tai nghe/Loa thật!\n"
                    "Điều này sẽ khiến bạn nghe thấy giọng mic của chính mình (bị vọng tiếng).\n\n"
                    "👉 Bạn nên chọn 'CABLE Input' để đưa tiếng mic vào Discord mà không bị vọng.\n\n"
                    "Bạn có chắc muốn tiếp tục không?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
            
        success, msg = self.engine.start(input_device_id=in_idx, output_device_id=out_idx)
        if success:
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
            self.input_combo.setEnabled(False)
            self.output_combo.setEnabled(False)
            self._autostart_retries = 0
        else:
            if not self.isVisible() and getattr(self, '_autostart_retries', 0) < 3:
                self._autostart_retries = getattr(self, '_autostart_retries', 0) + 1
                QTimer.singleShot(1500, self.start_audio)
            elif self.isVisible():
                QMessageBox.critical(self, "Lỗi kết nối âm thanh", msg)

    def stop_audio(self):
        self.engine.stop()
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.input_combo.setEnabled(True)
        self.output_combo.setEnabled(True)
        self.in_meter.setValue(0)
        self.out_meter.setValue(0)
        self.speech_indicator.setText("● Phát hiện giọng nói: 0%")

    def install_vbcable_action(self):
        reply = QMessageBox.question(
            self,
            "Cài đặt VB-Audio Cable",
            "Ứng dụng sẽ tải xuống và mở bộ cài VB-Audio Virtual Cable để tạo Mic ảo cho máy của bạn.\nBạn có muốn tiếp tục?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            import threading
            import install_vbcable
            
            def run_install():
                try:
                    dir_path = install_vbcable.download_and_extract_vbcable()
                    install_vbcable.run_installer(dir_path)
                except Exception as e:
                    print(f"Error installing: {e}")
                    
            threading.Thread(target=run_install, daemon=True).start()
            QMessageBox.information(
                self,
                "Đang mở trình cài đặt",
                "Đang tải và mở bộ cài đặt. Vui lòng bấm 'Install Driver' trên cửa sổ hiện ra và khởi động lại ứng dụng sau khi cài xong!"
            )

    def connect_change_events(self):
        self.input_combo.currentIndexChanged.connect(self.trigger_save_settings)
        self.output_combo.currentIndexChanged.connect(self.trigger_save_settings)
        self.music_in_combo.currentIndexChanged.connect(self.trigger_save_settings)
        self.hp_combo.currentIndexChanged.connect(self.trigger_save_settings)
        
        self.denoise_cb.toggled.connect(self.trigger_save_settings)
        self.gate_slider.valueChanged.connect(self.trigger_save_settings)
        self.gain_slider.valueChanged.connect(self.trigger_save_settings)
        
        self.web_to_hp_cb.toggled.connect(self.trigger_save_settings)
        self.web_to_mic_cb.toggled.connect(self.trigger_save_settings)
        self.ducking_cb.toggled.connect(self.trigger_save_settings)
        self.hp_vol_slider.valueChanged.connect(self.trigger_save_settings)
        self.discord_vol_slider.valueChanged.connect(self.trigger_save_settings)
        
        self.autostart_cb.toggled.connect(self.trigger_save_settings)
        self.auto_start_mic_cb.toggled.connect(self.trigger_save_settings)
        self.tray_cb.toggled.connect(self.trigger_save_settings)
        self.tabs.currentChanged.connect(self.trigger_save_settings)

    def load_and_apply_settings(self):
        self._is_loading_settings = True
        try:
            mic_cfg = self.settings.get("mic_settings", {})
            music_cfg = self.settings.get("music_settings", {})
            app_cfg = self.settings.get("app_settings", {})
            
            # Cấu hình Mic
            self.denoise_cb.setChecked(mic_cfg.get("denoise_enabled", True))
            self.gate_slider.setValue(int(mic_cfg.get("noise_gate", 10)))
            self.gain_slider.setValue(int(mic_cfg.get("gain", 100)))
            
            # Cấu hình Nhạc
            self.hp_vol_slider.setValue(int(music_cfg.get("hp_volume", 50)))
            self.discord_vol_slider.setValue(int(music_cfg.get("discord_volume", 50)))
            self.ducking_cb.setChecked(music_cfg.get("ducking", False))
            
            # Cấu hình App
            self.tray_cb.setChecked(app_cfg.get("minimize_to_tray", True))
            self.auto_start_mic_cb.setChecked(app_cfg.get("auto_start_mic", True))
            
            tab_idx = int(app_cfg.get("current_tab", 0))
            if 0 <= tab_idx < self.tabs.count():
                self.tabs.setCurrentIndex(tab_idx)
                
            # Khôi phục trạng thái phát nhạc Kênh A
            web_hp = music_cfg.get("web_to_hp", False)
            web_mic = music_cfg.get("web_to_mic", False)
            self.web_to_hp_cb.setChecked(web_hp)
            self.web_to_mic_cb.setChecked(web_mic)
            
            if web_hp or web_mic:
                self.sync_web_stream()
        finally:
            self._is_loading_settings = False

    def trigger_save_settings(self):
        if getattr(self, '_is_loading_settings', False):
            return
        if hasattr(self, 'save_timer'):
            self.save_timer.start()

    def save_settings_immediately(self):
        if getattr(self, '_is_loading_settings', False):
            return
        if hasattr(self, 'save_timer') and self.save_timer.isActive():
            self.save_timer.stop()
        self._do_save_settings()

    def collect_current_settings(self):
        cfg = deep_merge(DEFAULT_SETTINGS, self.settings)
        
        # Audio Devices
        in_text = self.input_combo.currentText().strip()
        if in_text and not in_text.startswith("❌"):
            cfg["audio_devices"]["input_mic"] = in_text
            
        out_text = self.output_combo.currentText().strip()
        if out_text and not out_text.startswith("❌"):
            cfg["audio_devices"]["output_mic"] = out_text
            
        music_in_text = self.music_in_combo.currentText().strip()
        if music_in_text and not music_in_text.startswith("❌"):
            cfg["audio_devices"]["music_input"] = music_in_text
            
        hp_data = self.hp_combo.currentData()
        if hp_data == "default":
            cfg["audio_devices"]["headphone_output"] = "default"
        elif hp_data is None:
            cfg["audio_devices"]["headphone_output"] = "none"
        else:
            hp_text = self.hp_combo.currentText().strip()
            if hp_text.startswith("🎧 "):
                hp_text = hp_text[2:].strip()
            cfg["audio_devices"]["headphone_output"] = hp_text
            
        # Mic Settings
        cfg["mic_settings"]["denoise_enabled"] = self.denoise_cb.isChecked()
        cfg["mic_settings"]["noise_gate"] = self.gate_slider.value()
        cfg["mic_settings"]["gain"] = self.gain_slider.value()
        
        # Music Settings
        cfg["music_settings"]["web_to_hp"] = self.web_to_hp_cb.isChecked()
        cfg["music_settings"]["web_to_mic"] = self.web_to_mic_cb.isChecked()
        cfg["music_settings"]["ducking"] = self.ducking_cb.isChecked()
        cfg["music_settings"]["hp_volume"] = self.hp_vol_slider.value()
        cfg["music_settings"]["discord_volume"] = self.discord_vol_slider.value()
        
        # App Settings
        cfg["app_settings"]["autostart"] = self.autostart_cb.isChecked()
        cfg["app_settings"]["minimize_to_tray"] = self.tray_cb.isChecked()
        cfg["app_settings"]["auto_start_mic"] = self.auto_start_mic_cb.isChecked()
        cfg["app_settings"]["current_tab"] = self.tabs.currentIndex()
        
        return cfg

    def _do_save_settings(self):
        if getattr(self, '_is_loading_settings', False):
            return
        self.settings = self.collect_current_settings()
        success = save_settings_to_file(self.settings)
        if success and hasattr(self, 'cfg_status_lbl'):
            t = time.strftime("%H:%M:%S")
            self.cfg_status_lbl.setText(f"✓ Đã lưu cài đặt ({t})")
            self.cfg_status_lbl.setStyleSheet("color: #10B981; font-size: 10px; font-weight: 500;")

    def reset_to_default_settings(self):
        reply = QMessageBox.question(
            self,
            "Khôi phục mặc định",
            "Bạn có chắc muốn đặt lại toàn bộ cài đặt về trạng thái ban đầu?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.settings = copy.deepcopy(DEFAULT_SETTINGS)
            self._is_loading_settings = True
            try:
                self.refresh_devices()
                self.load_and_apply_settings()
            finally:
                self._is_loading_settings = False
            self.save_settings_immediately()
            QMessageBox.information(self, "Đã đặt lại", "Đã khôi phục toàn bộ cài đặt về mặc định thành công!")

def main():
    # 1. Khóa Single-Instance: Tuyệt đối chỉ cho phép 1 phiên bản duy nhất chạy cùng lúc
    app_mutex = check_single_instance()

    # Nâng mức ưu tiên tiến trình lên ABOVE_NORMAL để Windows ưu tiên CPU cho bộ xử lý âm thanh,
    # tuyệt đối không bị game 3D chiếm dụng 100% CPU gây giật lag / drop fps hay nghẽn âm thanh mic
    try:
        kernel32 = ctypes.windll.kernel32
        # 0x00008000 = ABOVE_NORMAL_PRIORITY_CLASS
        kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), 0x00008000)
    except Exception:
        pass

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    window = MainWindow()
    window._app_instance_mutex = app_mutex

    # 2. Lắng nghe tín hiệu IPC khi người dùng bấm mở app lần thứ 2 thì tự hiện cửa sổ
    try:
        QLocalServer.removeServer(SINGLE_INSTANCE_IPC_NAME)
        ipc_server = QLocalServer(window)
        if ipc_server.listen(SINGLE_INSTANCE_IPC_NAME):
            def on_ipc_conn():
                conn = ipc_server.nextPendingConnection()
                if conn:
                    def on_ipc_read():
                        conn.readAll()
                        window.show_window()
                        conn.close()
                    conn.readyRead.connect(on_ipc_read)
                    if conn.bytesAvailable() > 0:
                        on_ipc_read()
            ipc_server.newConnection.connect(on_ipc_conn)
            window.ipc_server = ipc_server
    except Exception:
        pass

    is_autostart = "--autostart" in sys.argv or "--minimized" in sys.argv
    if is_autostart:
        # Chạy ngầm hoàn toàn trong khay hệ thống (System Tray) khi khởi động cùng Windows
        window.hide()
        # Tự động kích hoạt lọc âm Mic
        QTimer.singleShot(600, window.start_audio)
        window.tray.showMessage(
            "AI Mic Noise Canceller",
            "Ứng dụng đã khởi động và đang chạy ngầm bảo vệ âm thanh.",
            QSystemTrayIcon.MessageIcon.Information,
            3000
        )
    else:
        window.show()
        if window.auto_start_mic_cb.isChecked():
            QTimer.singleShot(600, window.start_audio)
        
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
