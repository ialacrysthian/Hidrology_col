"""Dialogo de configuracion del plugin TC Calculator."""
from __future__ import annotations
import os
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGroupBox,
    QLabel, QLineEdit, QPushButton, QSpinBox,
    QDoubleSpinBox, QComboBox, QDialogButtonBox,
    QFileDialog,
)
from .. import settings as cfg


class SettingsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("TC Calculator — Configuracion")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)

        # ── IDEAM ──────────────────────────────────────────────────────
        grp_ideam = QGroupBox("IDEAM / Socrata")
        li = QVBoxLayout(grp_ideam)
        li.addWidget(QLabel("App Token (opcional — evita throttling):"))
        self.edit_token = QLineEdit(cfg.get(cfg.Keys.IDEAM_APP_TOKEN) or "")
        self.edit_token.setEchoMode(QLineEdit.Password)
        li.addWidget(self.edit_token)
        lay.addWidget(grp_ideam)

        # ── Delimitacion ───────────────────────────────────────────────
        grp_ws = QGroupBox("Delimitacion")
        lw = QHBoxLayout(grp_ws)
        lw.addWidget(QLabel("FAT por defecto (celdas):"))
        self.spin_fat = QSpinBox()
        self.spin_fat.setRange(50, 50000)
        self.spin_fat.setValue(cfg.get_int(cfg.Keys.WATERSHED_FAT))
        lw.addWidget(self.spin_fat)
        lw.addStretch()
        lay.addWidget(grp_ws)

        # ── Estaciones ─────────────────────────────────────────────────
        grp_est = QGroupBox("Estaciones")
        le = QHBoxLayout(grp_est)
        le.addWidget(QLabel("Buffer por defecto (km):"))
        self.spin_buf = QDoubleSpinBox()
        self.spin_buf.setRange(0, 200)
        self.spin_buf.setValue(float(cfg.get(cfg.Keys.STATIONS_BUFFER_KM)))
        self.spin_buf.setDecimals(1)
        le.addWidget(self.spin_buf)
        le.addStretch()
        lay.addWidget(grp_est)

        grp_prog = QGroupBox("Progreso")
        lp = QHBoxLayout(grp_prog)
        lp.addWidget(QLabel("Modo de progreso:"))
        self.combo_progress_mode = QComboBox()
        self.combo_progress_mode.addItems(["professional", "fun"])
        idx = self.combo_progress_mode.findText(
            cfg.get(cfg.Keys.PROGRESS_MODE) or "professional"
        )
        if idx >= 0:
            self.combo_progress_mode.setCurrentIndex(idx)
        lp.addWidget(self.combo_progress_mode)
        lp.addStretch()
        lay.addWidget(grp_prog)

        # ── Botones ────────────────────────────────────────────────────
        bbox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bbox.accepted.connect(self._save)
        bbox.rejected.connect(self.reject)
        lay.addWidget(bbox)

    def _browse(self, edit: QLineEdit, title: str) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, title, edit.text() or os.path.expanduser("~"))
        if folder:
            edit.setText(folder)

    def _save(self) -> None:
        cfg.set_value(cfg.Keys.IDEAM_APP_TOKEN,    self.edit_token.text().strip())
        cfg.set_value(cfg.Keys.WATERSHED_FAT,      self.spin_fat.value())
        cfg.set_value(cfg.Keys.STATIONS_BUFFER_KM, self.spin_buf.value())
        cfg.set_value(
            cfg.Keys.PROGRESS_MODE,
            self.combo_progress_mode.currentText(),
        )
        self.accept()
