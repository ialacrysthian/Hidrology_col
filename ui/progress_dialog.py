from __future__ import annotations

import math
from qgis.PyQt.QtWidgets import (
    QDialog, QWidget, QLabel, QPushButton, QTextEdit,
    QVBoxLayout, QHBoxLayout, QSizePolicy, QFrame,
)
from qgis.PyQt.QtCore import (
    Qt, QTimer, QPropertyAnimation, QRect, QEasingCurve, pyqtSignal,
    QDateTime,
)
from qgis.PyQt.QtGui import QFont, QPainter, QColor, QPen, QBrush, QLinearGradient

from .water_progress import WaterGlassWidget


class ProgressBarWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._value = 0
        self.setMinimumHeight(24)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_value(self, value: int) -> None:
        self._value = max(0, min(100, value))
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(0, 4, 0, -4)
        radius = min(12, rect.height() // 2)

        # Background track
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(220, 224, 230))
        p.drawRoundedRect(rect, radius, radius)

        # Progress fill
        width = int(rect.width() * (self._value / 100.0))
        if width > 0:
            fill_rect = QRect(rect.left(), rect.top(), width, rect.height())
            grad = QLinearGradient(fill_rect.topLeft(), fill_rect.topRight())
            grad.setColorAt(0.0, QColor(227, 242, 253))
            grad.setColorAt(1.0, QColor(33, 150, 243))
            p.setBrush(QBrush(grad))
            p.drawRoundedRect(fill_rect, radius, radius)

            # Fill right edge if width small
            if width < radius * 2:
                p.setBrush(QBrush(QColor(33, 150, 243)))
                p.drawRect(fill_rect.left(), fill_rect.top(), width, rect.height())

        # Text overlay
        p.setPen(QColor(33, 33, 33))
        p.setFont(QFont("Segoe UI", 8, QFont.DemiBold))
        p.drawText(rect, Qt.AlignCenter, f"{self._value}%")


class ProgressDialog(QDialog):
    canceled = pyqtSignal()

    def __init__(self, mode: str = "professional", parent=None) -> None:
        super().__init__(parent)
        self.mode = mode if mode in ("professional", "fun") else "professional"
        self._start_time = QDateTime.currentDateTime()
        self._closing = False

        self.setWindowFlags(
            Qt.Dialog | Qt.WindowTitleHint | Qt.WindowSystemMenuHint | Qt.WindowCloseButtonHint
        )
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setMaximumWidth(600)
        self.setWindowTitle("Progreso")

        self._build_ui()
        self._init_timers()
        self._apply_theme()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        self.title_label = QLabel("Iniciando")
        self.title_label.setFont(QFont("Segoe UI", 14, QFont.Bold))
        self.subtitle_label = QLabel("Preparando operación...")
        self.subtitle_label.setFont(QFont("Segoe UI", 10))
        self.subtitle_label.setWordWrap(True)

        root.addWidget(self.title_label)
        root.addWidget(self.subtitle_label)

        self.step_label = QLabel("Paso 0 de 0")
        self.step_label.setFont(QFont("Segoe UI", 9))
        self.step_label.setStyleSheet("color: #909090;")
        root.addWidget(self.step_label)

        self.progress_container = QFrame()
        self.progress_container.setFrameShape(QFrame.NoFrame)
        progress_layout = QVBoxLayout(self.progress_container)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(0)

        if self.mode == "fun":
            self.progress_widget = WaterGlassWidget(self)
            self.progress_widget.setFixedSize(160, 260)
            self.progress_widget.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        else:
            self.progress_widget = ProgressBarWidget(self)
            self.progress_widget.setFixedHeight(42)

        progress_layout.addWidget(self.progress_widget, 0, Qt.AlignCenter)
        root.addWidget(self.progress_container, 0, Qt.AlignCenter)

        self.info_label = QLabel("0:00 elapsed — ETA: calculando...")
        self.info_label.setFont(QFont("Segoe UI", 9))
        self.info_label.setStyleSheet("color: #606060;")
        root.addWidget(self.info_label)

        self.log_toggle = QPushButton("Mostrar registro")
        self.log_toggle.setCheckable(True)
        self.log_toggle.clicked.connect(self._toggle_log)
        root.addWidget(self.log_toggle, 0, Qt.AlignLeft)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setVisible(False)
        self.log_view.setFont(QFont("Courier New", 9))
        self.log_view.setStyleSheet(
            "background: #1E1E1E; color: #E8F1FF; border: 1px solid #333;"
        )
        self.log_view.setMinimumHeight(120)
        root.addWidget(self.log_view)

        button_row = QHBoxLayout()
        button_row.addStretch()
        self.cancel_button = QPushButton("Cancelar")
        self.cancel_button.setFixedWidth(120)
        self.cancel_button.clicked.connect(self._on_cancel_clicked)
        button_row.addWidget(self.cancel_button)
        root.addLayout(button_row)

    def _apply_theme(self) -> None:
        if self.mode == "fun":
            self.setStyleSheet(
                "QDialog { background: #1A1A2E; color: #F5F7FF; border-radius: 12px; }"
                "QLabel { color: #F5F7FF; }"
                "QPushButton { background: #2F3F6E; color: #F5F7FF; border: 1px solid #5A7ACB; border-radius: 8px; padding: 8px 14px; }"
                "QPushButton:checked { background: #4FC3F7; color: #122B4A; }"
            )
        else:
            self.setStyleSheet(
                "QDialog { background: #FFFFFF; color: #212121; border-radius: 12px; }"
                "QLabel { color: #212121; }"
                "QPushButton { background: #2196F3; color: #FFFFFF; border: none; border-radius: 8px; padding: 8px 14px; }"
                "QPushButton:hover { background: #1976D2; }"
            )

    def _init_timers(self) -> None:
        self._update_timer = QTimer(self)
        self._update_timer.setInterval(1000)
        self._update_timer.timeout.connect(self._refresh_timing)
        self._update_timer.start()

    def _toggle_log(self) -> None:
        self.log_view.setVisible(self.log_toggle.isChecked())
        self.log_toggle.setText(
            "Ocultar registro" if self.log_toggle.isChecked() else "Mostrar registro"
        )

    def _refresh_timing(self) -> None:
        if self._start_time is None:
            return
        elapsed = self._start_time.secsTo(QDateTime.currentDateTime())
        elapsed_text = self._format_seconds(elapsed)
        progress = getattr(self.progress_widget, "_value", 0)
        if progress > 0:
            remaining = int(elapsed * (100.0 / progress - 1.0))
            eta_text = self._format_seconds(remaining)
        else:
            eta_text = "calculando..."
        self.info_label.setText(f"{elapsed_text} elapsed — ETA: {eta_text}")

    def _format_seconds(self, secs: int) -> str:
        mins, secs = divmod(max(0, secs), 60)
        return f"{mins}:{secs:02d}"

    def _on_cancel_clicked(self) -> None:
        self.canceled.emit()

    def append_log(self, message: str) -> None:
        self.log_view.append(message)
        self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    def set_step(self, step_number: int, total_steps: int, step_name: str) -> None:
        self.title_label.setText(f"Paso {step_number} de {total_steps} — {step_name}")
        self.step_label.setText(f"Paso {step_number} de {total_steps}")

    def set_progress(self, value: int, message: str | None = None) -> None:
        if self.mode == "fun":
            self.progress_widget.set_progress(value)
        else:
            self.progress_widget.set_value(value)
        if message is not None:
            self.subtitle_label.setText(message)
        self._refresh_timing()

    def show_success(self, message: str = "Completado") -> None:
        self.set_progress(100, message)
        self.cancel_button.setEnabled(False)
        QTimer.singleShot(1500, self._fade_close)

    def show_error(self, message: str) -> None:
        self.set_progress(0, message)
        self._shake()

    def _shake(self) -> None:
        anim = QPropertyAnimation(self, b"geometry")
        geo = self.geometry()
        anim.setDuration(300)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.setKeyValueAt(0.0, geo)
        anim.setKeyValueAt(0.25, geo.translated(-10, 0))
        anim.setKeyValueAt(0.5, geo.translated(10, 0))
        anim.setKeyValueAt(0.75, geo.translated(-6, 0))
        anim.setKeyValueAt(1.0, geo)
        anim.start(QPropertyAnimation.DeleteWhenStopped)

    def _fade_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        anim = QPropertyAnimation(self, b"windowOpacity")
        anim.setDuration(300)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.InCubic)
        anim.finished.connect(self.accept)
        anim.start(QPropertyAnimation.DeleteWhenStopped)

    def showEvent(self, event) -> None:
        self.setWindowOpacity(0.0)
        super().showEvent(event)
        anim = QPropertyAnimation(self, b"windowOpacity")
        anim.setDuration(300)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.start(QPropertyAnimation.DeleteWhenStopped)

    def closeEvent(self, event) -> None:
        if self._closing:
            super().closeEvent(event)
            return
        self._fade_close()
        event.ignore()
