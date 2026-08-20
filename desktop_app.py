from config import BASE_DIR
import sys
import os
import time
import threading
import ctypes
import socket

# ── PERF: must be set BEFORE any Qt import ──
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--enable-gpu-rasterization --enable-zero-copy "
    "--ignore-gpu-blocklist --disable-smooth-scrolling "
    "--enable-speech-api --enable-media-stream",
)

import requests
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QUrl, QSize
from PySide6.QtGui import QKeySequence, QShortcut, QIcon, QPixmap, QPainterPath, QRegion, QPainter, QPen, QColor, QLinearGradient, QRadialGradient, QBrush
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSystemTrayIcon, QMenu, QGraphicsDropShadowEffect
)

try:
    from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
    from PySide6.QtMultimediaWidgets import QVideoWidget
    MULTIMEDIA = True
except Exception:
    MULTIMEDIA = False

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEngineSettings, QWebEnginePage
    WEBENGINE = True
except Exception:
    WEBENGINE = False

SESSION = requests.Session()

VARIC_DIR   = os.environ.get("VARIC_DIR", str(BASE_DIR))
ICON_PATH   = os.path.join(VARIC_DIR, "ekaur.ico")
LOGO_PATH   = os.path.join(VARIC_DIR, "logo.png")
LOADING_DIR = os.path.join(VARIC_DIR, "loading")

# ──────────────────────────────────────────────────────────────────────────────
# Find free port
# ──────────────────────────────────────────────────────────────────────────────
def find_free_port(start=8000, end=8100) -> int:
    for port in range(start, end):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", port))
            s.close()
            return port
        except OSError:
            s.close()
            continue
    raise RuntimeError("No free port found")


APP_PORT = find_free_port()
BACKEND  = f"http://127.0.0.1:{APP_PORT}"
print(f"[PORT] Port: {APP_PORT}")


# ──────────────────────────────────────────────────────────────────────────────
# Dark titlebar (Windows 10/11)
# ──────────────────────────────────────────────────────────────────────────────
def dark_titlebar(win):
    if os.name == "nt":
        try:
            hwnd = int(win.winId())
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 20, ctypes.byref(ctypes.c_int(2)), ctypes.sizeof(ctypes.c_int))
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────────────────
# Backend server controller (graceful shutdown support)
# ──────────────────────────────────────────────────────────────────────────────
class BackendServer:
    def __init__(self):
        self._server = None
        self._thread = None
        self._lock = threading.Lock()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        with self._lock:
            if self.is_running():
                return

            def run():
                try:
                    import logging
                    import asyncio
                    logging.getLogger("asyncio").setLevel(logging.CRITICAL)

                    import uvicorn
                    from main import app as fastapi_app
                    config = uvicorn.Config(
                        fastapi_app, host="127.0.0.1", port=APP_PORT,
                        log_level="warning", loop="asyncio", http="h11",
                    )
                    self._server = uvicorn.Server(config)
                    self._server.run()
                except Exception as e:
                    print(f"[SERVER ERROR] {e}")

            self._thread = threading.Thread(target=run, daemon=True)
            self._thread.start()

    def stop(self):
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=3)


backend_server = BackendServer()


# ──────────────────────────────────────────────────────────────────────────────
# Startup Worker
# ──────────────────────────────────────────────────────────────────────────────
class StartupWorker(QThread):
    status         = Signal(str)
    backend_ready  = Signal()
    backend_failed = Signal(str)

    TIMEOUT_S = 30

    def run(self):
        start_t = time.monotonic()
        self.status.emit("⚙️ Starting backend…")
        backend_server.start()

        deadline = time.monotonic() + self.TIMEOUT_S
        while time.monotonic() < deadline:
            try:
                r = SESSION.get(f"{BACKEND}/health", timeout=1)
                if r.ok:
                    self.status.emit("✅ Ready")
                    self.backend_ready.emit()
                    self._report_engine_status()
                    return
            except requests.RequestException:
                pass
            time.sleep(0.05)

        self.backend_failed.emit(f"Backend failed to start after {self.TIMEOUT_S}s")

    def _report_engine_status(self):
        try:
            r = SESSION.get(f"{BACKEND}/health", timeout=3)
            if r.ok:
                self.status.emit("⚡ AITA Engine Ready")
                return
        except requests.RequestException:
            pass
        self.status.emit("⚡ Connecting to AITA Engine...")


class GlowOverlay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        rect = self.rect()
        w, h = rect.width(), rect.height()

        # 1. Multi-pass outer-to-inner neon bloom glow
        glow_layers = [
            (QColor(236, 72, 153, 255), 5.0, 1),   # Intense Hot Pink border
            (QColor(236, 72, 153, 210), 8.0, 2),   # Hot Pink bloom layer
            (QColor(139, 92, 246, 230), 4.0, 3),   # Electric Violet ring
            (QColor(99, 102, 241, 190), 7.0, 5),   # Deep Indigo bloom
            (QColor(6, 182, 212, 220), 3.5, 7),    # Cyber Cyan core ring
            (QColor(6, 182, 212, 160), 6.0, 9),    # Cyan bloom halo
            (QColor(249, 168, 212, 140), 2.0, 11),  # Magenta inner highlight
            (QColor(236, 72, 153, 95), 10.0, 13),  # Wide inner ambient glow
            (QColor(139, 92, 246, 65), 16.0, 16),  # Soft ambient fill
        ]

        for color, width, inset in glow_layers:
            pen = QPen(color, width)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            r = rect.adjusted(inset, inset, -inset, -inset)
            radius = max(4, 36 - inset)
            painter.drawRoundedRect(r, radius, radius)

        # 2. Central video glow aura (Vibrant radial backlight directly over video content)
        radial_aura = QRadialGradient(w / 2.0, h / 2.0, w / 1.5)
        radial_aura.setColorAt(0.0, QColor(236, 72, 153, 90))   # Hot pink center aura
        radial_aura.setColorAt(0.4, QColor(139, 92, 246, 65))   # Electric violet mid-aura
        radial_aura.setColorAt(0.75, QColor(6, 182, 212, 45))   # Cyber cyan outer aura
        radial_aura.setColorAt(1.0, QColor(0, 0, 0, 0))         # Transparent edge fade
        painter.fillRect(rect, QBrush(radial_aura))

        # 3. Top-down pink inset glow vignette
        top_grad = QLinearGradient(0, 0, 0, 50)
        top_grad.setColorAt(0.0, QColor(236, 72, 153, 110))
        top_grad.setColorAt(1.0, QColor(236, 72, 153, 0))
        painter.fillRect(0, 0, w, 50, QBrush(top_grad))

        # 4. Bottom-up cyan inset glow vignette
        bot_grad = QLinearGradient(0, h, 0, h - 50)
        bot_grad.setColorAt(0.0, QColor(6, 182, 212, 110))
        bot_grad.setColorAt(1.0, QColor(6, 182, 212, 0))
        painter.fillRect(0, h - 50, w, 50, QBrush(bot_grad))


# ──────────────────────────────────────────────────────────────────────────────
# Frameless Rounded Video Splash Screen with Inner & Video Glow
# ──────────────────────────────────────────────────────────────────────────────
class VideoSplashScreen(QWidget):
    def __init__(self, video_path):
        super().__init__()
        # Make it a frameless splash screen that stays on top
        self.setWindowFlags(Qt.SplashScreen | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        
        # Set a small square size
        self.setFixedSize(240, 240)

        # Apply a rounded mask to clip the window and native video widget into a rounded square
        path = QPainterPath()
        path.addRoundedRect(0, 0, 240, 240, 36, 36)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))

        # Center splash on screen
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.geometry()
            self.move((geo.width() - 240) // 2, (geo.height() - 240) // 2)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Wrapper to apply the rounded corners
        self.wrapper = QWidget(self)
        self.wrapper.setStyleSheet('''
            QWidget {
                background-color: #07090e;
                border-radius: 36px;
                overflow: hidden;
            }
        ''')
        wrapper_layout = QVBoxLayout(self.wrapper)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        
        if MULTIMEDIA:
            self._video_widget = QVideoWidget()
            self._video_widget.setAspectRatioMode(Qt.KeepAspectRatioByExpanding)
            
            # Attach glowing drop shadow effect directly to video widget
            video_shadow = QGraphicsDropShadowEffect(self._video_widget)
            video_shadow.setBlurRadius(50)
            video_shadow.setColor(QColor(236, 72, 153, 220))
            video_shadow.setOffset(0, 0)
            self._video_widget.setGraphicsEffect(video_shadow)

            wrapper_layout.addWidget(self._video_widget)
            
            self._audio_output = QAudioOutput()
            self._player = QMediaPlayer()
            self._player.setAudioOutput(self._audio_output)
            self._player.setVideoOutput(self._video_widget)
            self._player.setSource(QUrl.fromLocalFile(video_path))
            self._player.setLoops(QMediaPlayer.Infinite)
            self._player.play()
        else:
            err = QLabel("pip install PySide6")
            err.setAlignment(Qt.AlignCenter)
            err.setStyleSheet("color:#f87171;font-size:14px;")
            wrapper_layout.addWidget(err)
            
        layout.addWidget(self.wrapper)

        # Attach inner neon glow overlay over the video
        self._glow = GlowOverlay(self.wrapper)
        self._glow.setGeometry(0, 0, 240, 240)
        self._glow.raise_()

    def close_splash(self):
        if MULTIMEDIA and hasattr(self, '_player'):
            self._player.stop()
        self.close()


# ──────────────────────────────────────────────────────────────────────────────
# Main Window
# ──────────────────────────────────────────────────────────────────────────────
QSS = '''
QMainWindow, QWidget { background: #07090e; }
QLabel#status { color: #64748b; font-size: 11px; font-family: 'Segoe UI'; }
QPushButton#reload {
    color: #64748b; background: transparent; border: none;
    font-size: 11px; padding: 0 8px;
}
QPushButton#reload:hover { color: #e2e8f0; }
QPushButton#retry {
    color: #e2e8f0; background: #1c2333; border: 1px solid #2a3347;
    border-radius: 6px; font-size: 13px; padding: 8px 24px;
}
QPushButton#retry:hover { background: #2a3347; }
QWidget#bar { background: #0e1219; border-bottom: 1px solid #1c2333; }
'''


class VaricWindow(QMainWindow):
    HEALTH_INTERVAL_MS = 5000

    def __init__(self):
        super().__init__()
        self.setWindowTitle("AITA")
        if os.path.exists(ICON_PATH):
            self.setWindowIcon(QIcon(ICON_PATH))
        self.setMinimumSize(960, 720)
        self.setStyleSheet(QSS)
        self._worker = None
        self._web_shown = False

        root = QWidget(); self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Status bar ──
        bar = QWidget(); bar.setObjectName("bar"); bar.setFixedHeight(26)
        bl  = QHBoxLayout(bar); bl.setContentsMargins(10, 0, 10, 0)
        self._status = QLabel("Starting…"); self._status.setObjectName("status")
        bl.addWidget(self._status); bl.addStretch()
        btn = QPushButton("⟳ Reload"); btn.setObjectName("reload")
        btn.clicked.connect(self._reload); bl.addWidget(btn)
        layout.addWidget(bar)

        # ── Error / Loading Fallback container ──
        self._loading_container = QWidget()
        loading_layout = QVBoxLayout(self._loading_container)
        
        self._loading = QLabel("Loading...")
        self._loading.setAlignment(Qt.AlignCenter)
        self._loading.setStyleSheet("color:#94a3b8; font-size:15px;")
        
        self._retry_btn = QPushButton("Retry")
        self._retry_btn.setObjectName("retry")
        self._retry_btn.clicked.connect(self._retry_startup)
        self._retry_btn.hide()
        
        loading_layout.addStretch()
        loading_layout.addWidget(self._loading)
        loading_layout.addWidget(self._retry_btn, 0, Qt.AlignHCenter)
        loading_layout.addStretch()

        layout.addWidget(self._loading_container, 1)

        # ── Web view ──
        if WEBENGINE:
            self._web = QWebEngineView()
            self._web.hide()
            page = self._web.page()
            if hasattr(page, 'permissionRequested'):
                page.permissionRequested.connect(lambda permission: permission.grant())
            s = self._web.settings()
            s.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
            s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
            if hasattr(QWebEngineSettings.WebAttribute, 'ScrollAnimatorEnabled'):
                s.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, False)
            self._web.loadFinished.connect(
                lambda ok: self._status.setText("✅ Ready" if ok else "⚠️ Page error"))
            layout.addWidget(self._web, 1)

            QShortcut(QKeySequence("F5"),      self).activated.connect(self._reload)
            QShortcut(QKeySequence("Ctrl+R"),  self).activated.connect(self._reload)
            QShortcut(QKeySequence("F11"),     self).activated.connect(self._toggle_fullscreen)
        else:
            self._web = None
            err = QLabel("pip install PySide6-WebEngine")
            err.setAlignment(Qt.AlignCenter)
            err.setStyleSheet("color:#f87171;font-size:14px;")
            layout.addWidget(err, 1)

        self._health_timer = QTimer(self)
        self._health_timer.timeout.connect(self._check_health)
        self._backend_up = False

        dark_titlebar(self)
        self._force_quit = False
        self._setup_tray()

    def _setup_tray(self):
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._tray_icon = QSystemTrayIcon(self)
            if os.path.exists(ICON_PATH):
                self._tray_icon.setIcon(QIcon(ICON_PATH))
            menu = QMenu()
            show_action = menu.addAction("Show AITA")
            show_action.triggered.connect(self.restore_window)
            quit_action = menu.addAction("Quit AITA")
            quit_action.triggered.connect(self.quit_app)
            self._tray_icon.setContextMenu(menu)
            self._tray_icon.activated.connect(self._on_tray_activated)
            self._tray_icon.show()
        else:
            self._tray_icon = None

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.restore_window()

    def restore_window(self):
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowMinimized | Qt.WindowActive)
        self.raise_()
        self.activateWindow()

    def quit_app(self):
        self._force_quit = True
        if hasattr(self, "_tray_icon") and self._tray_icon:
            self._tray_icon.hide()
        backend_server.stop()
        QApplication.quit()

    def closeEvent(self, event):
        if hasattr(self, "_tray_icon") and self._tray_icon:
            self._tray_icon.hide()
        backend_server.stop()
        event.accept()
        QApplication.quit()

    def set_status(self, text: str):
        self._status.setText(text)

    def show_web(self):
        if not self._backend_up:
            self._backend_up = True
            self._health_timer.start(self.HEALTH_INTERVAL_MS)
            self._do_show_web()

    def _do_show_web(self):
        self._loading_container.hide()
        self._backend_up = True
        self._health_timer.start(self.HEALTH_INTERVAL_MS)
        if self._web and not self._web_shown:
            self._web_shown = True
            self._web.setUrl(QUrl(BACKEND))
            self._web.show()

    def show_error(self, msg: str):
        self._loading_container.show()
        if self._web:
            self._web.hide()
        self._loading.setText(f"❌ {msg}")
        self._loading.setStyleSheet("color:#f87171;font-size:16px;")
        self._retry_btn.show()

    def _retry_startup(self):
        self._retry_btn.hide()
        self._loading.setStyleSheet("color:#94a3b8; font-size:15px;")
        self._loading.setText("Retrying…")
        if self._worker:
            self._worker.start()

    def _check_health(self):
        def probe():
            try:
                ok = SESSION.get(f"{BACKEND}/health", timeout=2).ok
            except requests.RequestException:
                ok = False
            QTimer.singleShot(0, lambda: self._on_health(ok))
        threading.Thread(target=probe, daemon=True).start()

    def _on_health(self, ok: bool):
        if ok and not self._backend_up:
            self._backend_up = True
            self._status.setText("✅ Backend reconnected")
            if self._web:
                self._web.reload()
        elif not ok and self._backend_up:
            self._backend_up = False
            self._status.setText("⚠️ Backend unreachable")

    def _reload(self):
        if self._web:
            self._web.reload()

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showMaximized()
        else:
            self.showFullScreen()


# ──────────────────────────────────────────────────────────────────────────────
# Entry
# ──────────────────────────────────────────────────────────────────────────────
def main():
    app = QApplication(sys.argv)
    app.setApplicationName("AITA")
    if os.path.exists(ICON_PATH):
        app.setWindowIcon(QIcon(ICON_PATH))

    win = VaricWindow()
    
    video_path = r"C:\Varic\extra glow loading.mp4"
    splash = VideoSplashScreen(video_path)
    splash.show()

    splash_start_t = time.monotonic()

    def on_ready():
        elapsed = time.monotonic() - splash_start_t
        remaining_ms = int(max(0, (5.0 - elapsed) * 1000))
        if remaining_ms > 0:
            QTimer.singleShot(remaining_ms, do_open_app)
        else:
            do_open_app()

    def do_open_app():
        splash.close_splash()
        win.showMaximized()
        win.show_web()

    worker = StartupWorker()
    win._worker = worker
    worker.status.connect(win.set_status)
    
    worker.backend_ready.connect(on_ready)
    
    worker.backend_failed.connect(splash.close_splash)
    worker.backend_failed.connect(win.showMaximized)
    worker.backend_failed.connect(win.show_error)

    worker.start()

    app.aboutToQuit.connect(backend_server.stop)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
