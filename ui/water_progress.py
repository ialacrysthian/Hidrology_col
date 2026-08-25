"""
Widget de progreso estilo pixel art — vaso de agua.

El vaso se va llenando con gotas de agua animadas.
En la base se muestra el porcentaje actual.

Uso:
    widget = WaterGlassWidget(parent)
    widget.set_progress(65)      # 0-100
    widget.set_message("Paso 3") # texto opcional debajo
"""
from __future__ import annotations
import random
from qgis.PyQt.QtWidgets import QWidget
from qgis.PyQt.QtCore    import Qt, QTimer, QRectF, QPointF
from qgis.PyQt.QtGui     import (
    QPainter, QColor, QPen, QBrush, QFont, QPainterPath, QLinearGradient,
)

# ── Paleta de colores ────────────────────────────────────────────────────────
C_BG         = QColor(30,  34,  40)      # fondo del widget
C_GLASS      = QColor(160, 210, 240, 90) # cristal (semitransparente)
C_GLASS_EDGE = QColor(180, 230, 255)     # borde del vaso
C_WATER_TOP  = QColor(40,  140, 220)     # agua — parte superior
C_WATER_BOT  = QColor(20,   80, 160)     # agua — parte inferior
C_DROP       = QColor(100, 190, 255)     # gota cayendo
C_BUBBLE     = QColor(200, 235, 255, 140)# burbujas dentro del agua
C_TEXT_PCT   = QColor(255, 255, 255)     # porcentaje
C_TEXT_MSG   = QColor(160, 200, 230)     # mensaje de estado
C_SHINE      = QColor(255, 255, 255, 60) # reflejo en el vaso
C_SPLASH     = QColor(130, 200, 255, 200)# splash al caer una gota


class _Drop:
    """Una gota cayendo hacia la superficie del agua."""
    PX = 4   # radio en px del pixel-art

    def __init__(self, x: float, y_start: float, speed: float) -> None:
        self.x  = x
        self.y  = y_start
        self.v  = speed
        self.active = True
        self.splash_frames = 0   # frames de splash al llegar

    def step(self, water_top_y: float) -> None:
        if self.splash_frames > 0:
            self.splash_frames -= 1
            if self.splash_frames == 0:
                self.active = False
            return
        self.y += self.v
        if self.y >= water_top_y:
            self.y = water_top_y
            self.splash_frames = 6   # mostrar splash 6 frames


class _Bubble:
    """Burbuja que sube dentro del agua."""
    def __init__(self, x: float, y: float) -> None:
        self.x  = x + random.uniform(-2, 2)
        self.y  = y
        self.r  = random.uniform(2, 5)
        self.v  = random.uniform(0.4, 1.0)
        self.active = True

    def step(self, water_top_y: float) -> None:
        self.y -= self.v
        if self.y < water_top_y:
            self.active = False


class WaterGlassWidget(QWidget):
    """
    Vaso pixel-art animado que muestra progreso 0-100 %.
    """

    # Proporciones del vaso en fracción del ancho/alto disponible
    _GLASS_W_FRAC  = 0.55
    _GLASS_H_FRAC  = 0.72
    _WALL_FRAC     = 0.045   # grosor de pared como fracción del ancho

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._progress  = 0       # 0-100
        self._message   = ""
        self._drops: list[_Drop]  = []
        self._bubbles: list[_Bubble] = []
        self._wave_offset = 0.0   # para animar la superficie
        self._tick = 0

        self.setMinimumSize(120, 160)

        self._timer = QTimer(self)
        self._timer.setInterval(50)   # 20 fps
        self._timer.timeout.connect(self._animate)
        self._timer.start()

    # ── API pública ──────────────────────────────────────────────────────────

    def set_progress(self, value: int) -> None:
        self._progress = max(0, min(100, value))
        self.update()

    def set_message(self, msg: str) -> None:
        self._message = msg
        self.update()

    def reset(self) -> None:
        self._progress = 0
        self._message  = ""
        self._drops.clear()
        self._bubbles.clear()
        self.update()

    # ── Animacion ────────────────────────────────────────────────────────────

    def _animate(self) -> None:
        self._tick += 1
        w, h = self.width(), self.height()
        gx, gy, gw, gh = self._glass_rect(w, h)
        wall = max(3, int(gw * self._WALL_FRAC))

        # Nivel del agua
        inner_h = gh - wall
        water_h = inner_h * self._progress / 100
        water_top_y = gy + gh - wall - water_h

        # Lanzar nueva gota cada ~8 ticks si hay progreso
        if self._progress > 0 and self._tick % 8 == 0:
            inner_w = gw - 2 * wall
            drop_x  = gx + wall + random.uniform(4, inner_w - 4)
            self._drops.append(_Drop(
                x       = drop_x,
                y_start = gy + wall,
                speed   = random.uniform(3.5, 6.0),
            ))

        # Mover gotas
        for d in self._drops:
            d.step(water_top_y)
            if d.splash_frames == 5:   # primer frame de splash → burbuja
                self._bubbles.append(_Bubble(d.x, water_top_y))

        self._drops   = [d for d in self._drops if d.active]

        # Mover burbujas
        for b in self._bubbles:
            b.step(water_top_y)
        self._bubbles = [b for b in self._bubbles if b.active]

        # Ola en la superficie
        self._wave_offset = (self._wave_offset + 0.3) % (3.14159 * 2)

        self.update()

    # ── Dimensiones helper ───────────────────────────────────────────────────

    def _glass_rect(self, w: int, h: int) -> tuple[int, int, int, int]:
        """Retorna (x, y, width, height) del vaso centrado."""
        gw = int(w * self._GLASS_W_FRAC)
        gh = int(h * self._GLASS_H_FRAC)
        # Dejar espacio abajo para el texto
        text_space = int(h * 0.22)
        gx = (w - gw) // 2
        gy = int(h * 0.04)
        # Ajustar si no cabe
        if gy + gh + text_space > h:
            gh = h - gy - text_space
        return gx, gy, gw, gh

    # ── Dibujo ───────────────────────────────────────────────────────────────

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # Fondo
        p.fillRect(0, 0, w, h, C_BG)

        gx, gy, gw, gh = self._glass_rect(w, h)
        wall = max(3, int(gw * self._WALL_FRAC))

        # Zona interior del vaso
        ix = gx + wall
        iy = gy + wall
        iw = gw - 2 * wall
        ih = gh - wall          # sin tapa

        # Nivel del agua
        water_h   = ih * self._progress / 100
        water_y   = iy + ih - water_h   # y donde empieza el agua

        # ── 1. Agua ─────────────────────────────────────────────────────────
        if self._progress > 0:
            grad = QLinearGradient(ix, water_y, ix, iy + ih)
            grad.setColorAt(0.0, C_WATER_TOP)
            grad.setColorAt(1.0, C_WATER_BOT)
            p.setBrush(QBrush(grad))
            p.setPen(Qt.NoPen)

            # Forma con ola en la superficie (sino hay progreso completo)
            if self._progress < 100:
                path = QPainterPath()
                path.moveTo(ix, iy + ih)
                path.lineTo(ix, water_y + 4)
                import math
                # Ola usando sinusoide
                steps  = max(iw // 4, 8)
                for i in range(steps + 1):
                    px = ix + i * iw / steps
                    wave = math.sin(self._wave_offset + i * 0.7) * 2.5
                    py   = water_y + wave
                    if i == 0:
                        path.lineTo(px, py)
                    else:
                        path.lineTo(px, py)
                path.lineTo(ix + iw, iy + ih)
                path.closeSubpath()
                p.drawPath(path)
            else:
                p.drawRect(ix, iy, iw, ih)

        # ── 2. Burbujas ──────────────────────────────────────────────────────
        p.setPen(Qt.NoPen)
        p.setBrush(C_BUBBLE)
        for b in self._bubbles:
            if b.y > water_y:
                p.drawEllipse(QPointF(b.x, b.y), b.r, b.r)

        # ── 3. Vaso (paredes pixel-art) ──────────────────────────────────────
        # Paredes izquierda, derecha y fondo
        p.setPen(Qt.NoPen)
        p.setBrush(C_GLASS)

        # Pared izquierda
        p.drawRect(gx, gy, wall, gh)
        # Pared derecha
        p.drawRect(gx + gw - wall, gy, wall, gh)
        # Fondo
        p.drawRect(gx, gy + gh - wall, gw, wall)

        # Borde pixelado (línea exterior)
        p.setPen(QPen(C_GLASS_EDGE, 1.5))
        p.setBrush(Qt.NoBrush)
        # Líneas del contorno (forma U)
        p.drawLine(gx,           gy,      gx,           gy + gh)
        p.drawLine(gx + gw,      gy,      gx + gw,      gy + gh)
        p.drawLine(gx,           gy + gh, gx + gw,      gy + gh)
        # Líneas interiores (efecto grosor)
        p.setPen(QPen(C_GLASS_EDGE.darker(130), 1))
        p.drawLine(gx + wall,        gy,      gx + wall,        gy + gh - wall)
        p.drawLine(gx + gw - wall,   gy,      gx + gw - wall,   gy + gh - wall)
        p.drawLine(gx + wall,        gy + gh - wall, gx + gw - wall, gy + gh - wall)

        # Reflejo (brillo en pared izquierda del vaso)
        shine_path = QPainterPath()
        shine_path.moveTo(gx + wall + 2, gy + 6)
        shine_path.lineTo(gx + wall + 5, gy + 6)
        shine_path.lineTo(gx + wall + 4, gy + gh * 0.55)
        shine_path.lineTo(gx + wall + 2, gy + gh * 0.55)
        shine_path.closeSubpath()
        p.setPen(Qt.NoPen)
        p.setBrush(C_SHINE)
        p.drawPath(shine_path)

        # ── 4. Gotas cayendo ─────────────────────────────────────────────────
        for d in self._drops:
            if d.splash_frames > 0:
                self._draw_splash(p, d.x, d.y, d.splash_frames)
            else:
                self._draw_drop(p, d.x, d.y)

        # ── 5. Líneas de "pixel grid" en el agua (textura) ──────────────────
        if self._progress > 0:
            p.setPen(QPen(QColor(255, 255, 255, 18), 1))
            step = 8
            y0 = max(int(water_y), iy)
            for yy in range(y0, iy + ih, step):
                p.drawLine(ix + 1, yy, ix + iw - 1, yy)

        # ── 6. Texto — porcentaje ────────────────────────────────────────────
        pct_y  = gy + gh + 10
        font   = QFont("Courier New", 12, QFont.Bold)
        p.setFont(font)
        p.setPen(C_TEXT_PCT)
        pct_txt = f"{self._progress}%"
        fm      = p.fontMetrics()
        tw      = fm.horizontalAdvance(pct_txt)
        p.drawText((w - tw) // 2, pct_y + fm.ascent(), pct_txt)

        # ── 7. Mensaje de estado ─────────────────────────────────────────────
        if self._message:
            font2 = QFont("Courier New", 7)
            p.setFont(font2)
            p.setPen(C_TEXT_MSG)
            fm2 = p.fontMetrics()
            # Cortar si es muy largo
            msg = self._message
            max_w = w - 8
            while fm2.horizontalAdvance(msg) > max_w and len(msg) > 6:
                msg = msg[:-4] + "..."
            tw2 = fm2.horizontalAdvance(msg)
            p.drawText((w - tw2) // 2, pct_y + fm.ascent() + fm2.height() + 2, msg)

        p.end()

    def _draw_drop(self, p: QPainter, x: float, y: float) -> None:
        """Dibuja una gota en pixel art (forma de rombo/gota)."""
        p.setPen(Qt.NoPen)
        p.setBrush(C_DROP)
        px = _Drop.PX
        # Cabeza (círculo)
        p.drawEllipse(QPointF(x, y + px), px, px)
        # Punta (triángulo hacia arriba)
        path = QPainterPath()
        path.moveTo(x,       y)
        path.lineTo(x - px,  y + px + 1)
        path.lineTo(x + px,  y + px + 1)
        path.closeSubpath()
        p.drawPath(path)

    def _draw_splash(self, p: QPainter, x: float, y: float, frames: int) -> None:
        """Pequeño splash al impactar la gota."""
        import math
        p.setPen(QPen(C_SPLASH, 1.5))
        p.setBrush(Qt.NoBrush)
        radius = (6 - frames) * 3.0
        alpha  = int(255 * frames / 6)
        color  = QColor(C_SPLASH)
        color.setAlpha(alpha)
        p.setPen(QPen(color, 1.5))
        p.drawEllipse(QPointF(x, y), radius, radius * 0.4)
