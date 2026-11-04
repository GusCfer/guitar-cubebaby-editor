"""Interface do Cube Baby Editor (PyQt6)."""
from __future__ import annotations

import math
import queue
import sys
import threading
import time
from pathlib import Path

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt, pyqtSignal

from .device import (CubeBaby, DeviceError, PARAMS, SECTIONS, SLOT_COUNT, SLOT_SIZE)
from .library import Library, backup_bank, dict_to_fields, parse_site_text, read_bank_file, BACKUP_DIR
from . import __version__
from .qr import encode as qr_encode
from .site_import import fetch_preset, preset_url
from .remote import RemoteServer, local_ips

ASSETS = Path(__file__).resolve().parent / "assets"
# No Windows (WinMM) o pedal aplica melhor as escritas ao vivo com um "identify" antes de cada rajada
# (observado pelo projeto cubecontrol). No Linux basta depois de um tempo parado.
PRELUDE_IDLE = 0.0 if sys.platform == "win32" else 1.5

# ---------------------------------------------------------------- tema

BG = "#121418"
PANEL = "#1b1e24"
PANEL_2 = "#23272f"
BORDER = "#2c313a"
TEXT = "#eceef1"
MUTED = "#8b919c"
ACCENT = {"drive": "#ff7a45", "fx": "#3fb6f0", "amb": "#a98bff", "master": "#f5c542"}
OK, BAD = "#46d17d", "#ff5f5f"

SECTION_TITLE = {"drive": "Amp / Drive", "fx": "Modulação & Delay", "amb": "IR & Reverb", "master": "Saída"}
SECTION_ORDER = {
    "drive": ["type", "gain", "tone"],
    "fx": ["modulation", "time", "feedback", "mix"],
    "amb": ["cabinet", "reverb"],
    "master": ["volume"],
}
PRM = {p.key: p for p in PARAMS}


def fmt_value(key: str, v: int) -> str:
    if key == "type":
        return f"Amp {v + 1}"
    if key == "modulation":
        if v in (7, 8):
            return "Off"
        return f"Chorus {7 - v}" if v < 7 else f"Phaser {v - 8}"
    if key == "cabinet":
        return f"Cab {v}" if v else "0"
    return str(v)


STYLE = f"""
* {{ font-family: 'Inter', 'Noto Sans', sans-serif; color: {TEXT}; }}
QMainWindow, QWidget#root {{ background: {BG}; }}
QLabel#muted {{ color: {MUTED}; }}
QFrame#card {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 14px; }}
QPushButton {{ background: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 9px;
              padding: 8px 14px; font-weight: 600; }}
QPushButton:hover {{ border-color: #4a5160; }}
QPushButton:disabled {{ color: #5a606b; }}
QPushButton#primary {{ background: {ACCENT['master']}; color: #17140a; border: none; }}
QPushButton#primary:disabled {{ background: #4a4330; color: #8d8467; }}
QPushButton#slot {{ font-size: 22px; font-weight: 800; min-width: 64px; min-height: 48px;
                   border-radius: 12px; }}
QPushButton#slot:checked {{ background: {TEXT}; color: {BG}; border: none; }}
QListWidget {{ background: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 10px; padding: 4px;
              outline: none; }}
QListWidget::item {{ padding: 8px; border-radius: 7px; }}
QListWidget::item:selected {{ background: #343a46; }}
QListWidget::item:hover {{ background: #2b3039; }}
QMenu {{ background: {PANEL_2}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: #343a46; }}
QToolTip {{ background: {PANEL_2}; color: {TEXT}; border: 1px solid {BORDER}; }}
QComboBox {{ background: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 6px 10px; }}
QComboBox QAbstractItemView {{ background: {PANEL_2}; selection-background-color: #343a46; }}
code {{ color: {TEXT}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #3a404b; border-radius: 4px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: #4a5160; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ height: 0; }}
"""


# ---------------------------------------------------------------- widgets


class Knob(QtWidgets.QWidget):
    valueChanged = pyqtSignal(int)

    def __init__(self, key: str, color: str, parent=None):
        super().__init__(parent)
        self.key, self.color = key, QtGui.QColor(color)
        self.maximum = PRM[key].maximum
        self.label = PRM[key].label
        self._v = 0
        self._drag = None
        self.setFixedSize(96, 118)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        # TYPE aparece como "Amp 1..9": o valor digitado segue o que está na tela
        self.shift = 1 if key == "type" else 0
        lo, hi = self.shift, self.maximum + self.shift
        extra = "\nMOD: 0–6 chorus, 7–8 desligado, 9–15 phaser." if key == "modulation" else ""
        self.setToolTip(f"{self.label} ({lo}–{hi})\nArraste, use a roda do mouse ou as setas.\n"
                        f"Duplo clique ou digite um número para editar.{extra}")
        self._editor = None

    # edição digitada
    def start_edit(self, initial: str = ""):
        if self._editor or not self.isEnabled():
            return
        ed = _ValueEdit(self)
        ed.setValidator(QtGui.QIntValidator(0, self.maximum + self.shift, ed))
        ed.setText(initial or str(self._v + self.shift))
        ed.setGeometry(16, 79, self.width() - 32, 22)
        if not initial:
            ed.selectAll()
        ed.committed.connect(self._commit_edit)
        ed.cancelled.connect(self._close_edit)
        self._editor = ed
        ed.show()
        ed.setFocus()

    def _commit_edit(self, text: str):
        if text.strip().isdigit():
            self.setValue(int(text) - self.shift, emit=True)
        self._close_edit()

    def _close_edit(self):
        ed, self._editor = self._editor, None
        if ed:
            ed.hide()
            ed.deleteLater()
            self.setFocus()

    def mouseDoubleClickEvent(self, e):
        self._drag = None
        self.start_edit()

    def value(self) -> int:
        return self._v

    def setValue(self, v: int, emit: bool = False):
        v = max(0, min(self.maximum, int(v)))
        if v != self._v:
            self._v = v
            self.update()
            if emit:
                self.valueChanged.emit(v)

    # interação
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = (e.position().y(), self._v)

    def mouseMoveEvent(self, e):
        if self._drag:
            y0, v0 = self._drag
            span = 160 if self.maximum > 20 else 90
            self.setValue(round(v0 + (y0 - e.position().y()) * self.maximum / span), emit=True)

    def mouseReleaseEvent(self, e):
        self._drag = None

    def wheelEvent(self, e):
        step = 1 if e.angleDelta().y() > 0 else -1
        if self.maximum > 40 and e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            step *= 8
        self.setValue(self._v + step, emit=True)

    def keyPressEvent(self, e):
        big = 8 if self.maximum > 40 else 2
        d = {Qt.Key.Key_Up: 1, Qt.Key.Key_Right: 1, Qt.Key.Key_Down: -1, Qt.Key.Key_Left: -1,
             Qt.Key.Key_PageUp: big, Qt.Key.Key_PageDown: -big}.get(e.key())
        if d:
            self.setValue(self._v + d, emit=True)
        elif e.text().isdigit() and not e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.start_edit(e.text())
        elif e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.start_edit()
        else:
            super().keyPressEvent(e)  # não tratada: sobe para a janela (atalhos A/B/C)

    # desenho
    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        on = self.isEnabled()
        col = self.color if on else QtGui.QColor("#4a4f58")
        cx, cy, r = self.width() / 2, 44, 32
        rect = QtCore.QRectF(cx - r, cy - r, 2 * r, 2 * r)
        frac = self._v / self.maximum if self.maximum else 0

        pen = QtGui.QPen(QtGui.QColor("#2e333c"), 6, cap=Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(rect, 225 * 16, -270 * 16)
        pen.setColor(col)
        p.setPen(pen)
        if frac > 0:
            p.drawArc(rect, 225 * 16, int(-270 * 16 * frac))

        # corpo do knob
        grad = QtGui.QRadialGradient(cx, cy - 8, r)
        grad.setColorAt(0, QtGui.QColor("#3a3f49"))
        grad.setColorAt(1, QtGui.QColor("#22262d"))
        p.setPen(QtGui.QPen(QtGui.QColor("#0d0f12"), 1))
        p.setBrush(grad)
        p.drawEllipse(QtCore.QPointF(cx, cy), r - 9, r - 9)
        ang = math.radians(225 - 270 * frac)
        x1, y1 = cx + math.cos(ang) * (r - 22), cy - math.sin(ang) * (r - 22)
        x2, y2 = cx + math.cos(ang) * (r - 12), cy - math.sin(ang) * (r - 12)
        p.setPen(QtGui.QPen(QtGui.QColor(TEXT if on else "#5a606b"), 3, cap=Qt.PenCapStyle.RoundCap))
        p.drawLine(QtCore.QPointF(x1, y1), QtCore.QPointF(x2, y2))

        if self.hasFocus():
            p.setPen(QtGui.QPen(QtGui.QColor("#5b6372"), 1, Qt.PenStyle.DotLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QtCore.QPointF(cx, cy), r + 6, r + 6)

        f = self.font()
        f.setPointSizeF(9.5)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QtGui.QColor(TEXT if on else MUTED))
        p.drawText(QtCore.QRectF(0, 82, self.width(), 16), Qt.AlignmentFlag.AlignCenter, fmt_value(self.key, self._v))
        f.setPointSizeF(8.5)
        f.setBold(False)
        f.setLetterSpacing(QtGui.QFont.SpacingType.PercentageSpacing, 110)
        p.setFont(f)
        p.setPen(QtGui.QColor(MUTED))
        p.drawText(QtCore.QRectF(0, 100, self.width(), 16), Qt.AlignmentFlag.AlignCenter, self.label)


class _ValueEdit(QtWidgets.QLineEdit):
    committed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, parent):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(f"background:{BG}; border:1px solid {MUTED}; border-radius:5px;"
                           "font-weight:700; padding:0;")
        self._done = False

    def _finish(self, ok: bool):
        if self._done:
            return
        self._done = True
        self.committed.emit(self.text()) if ok else self.cancelled.emit()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab):
            self._finish(True)
        elif e.key() == Qt.Key.Key_Escape:
            self._finish(False)
        else:
            super().keyPressEvent(e)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self._finish(True)


class Toggle(QtWidgets.QAbstractButton):
    def __init__(self, color: str, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.color = QtGui.QColor(color)
        self.setFixedSize(46, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        on = self.isChecked()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self.color if on else QtGui.QColor("#3a3f49"))
        p.drawRoundedRect(QtCore.QRectF(0, 0, 46, 26), 13, 13)
        p.setBrush(QtGui.QColor("#ffffff" if on else "#9aa0aa"))
        p.drawEllipse(QtCore.QPointF(33 if on else 13, 13), 9, 9)


class SectionCard(QtWidgets.QFrame):
    def __init__(self, section: str, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.section = section
        color = ACCENT[section]
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 10)
        head = QtWidgets.QHBoxLayout()
        dot = QtWidgets.QLabel()
        dot.setFixedSize(10, 10)
        dot.setStyleSheet(f"background:{color}; border-radius:5px;")
        title = QtWidgets.QLabel(SECTION_TITLE[section])
        title.setStyleSheet("font-weight:700; font-size:14px;")
        head.addWidget(dot)
        head.addSpacing(4)
        head.addWidget(title)
        head.addStretch()
        self.toggle = None
        if section in SECTIONS:
            self.toggle = Toggle(color)
            self.toggle.setToolTip("Liga/desliga esta seção")
            head.addWidget(self.toggle)
        lay.addLayout(head)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(2)
        self.knobs = {}
        for key in SECTION_ORDER[section]:
            k = Knob(key, color)
            self.knobs[key] = k
            row.addWidget(k)
        row.addStretch()
        lay.addLayout(row)


# ---------------------------------------------------------------- worker


class Bridge(QtCore.QObject):
    done = pyqtSignal(object, object)
    failed = pyqtSignal(object, object)
    busy = pyqtSignal(bool)
    remote = pyqtSignal(object)  # comando vindo do celular (entregue na thread do Qt)
    site = pyqtSignal(object, bool)  # resultado da importação do site (dict ou exceção, perguntar nome?)


class Job:
    def __init__(self, fn, on_done=None, on_error=None, busy=True):
        self.fn, self.on_done, self.on_error, self.busy = fn, on_done, on_error, busy


_FLUSH = object()


class Worker(threading.Thread):
    """Fila única de acesso ao pedal; escritas ao vivo são agrupadas (só vale o último valor)."""

    def __init__(self, bridge: Bridge):
        super().__init__(daemon=True)
        self.bridge = bridge
        self.dev: CubeBaby | None = None
        self.q: queue.Queue = queue.Queue()
        self._pending: dict[tuple[int, int], int] = {}
        self._plock = threading.Lock()
        self._last_live = 0.0

    def submit(self, fn, on_done=None, on_error=None, busy=True):
        self.q.put(Job(fn, on_done, on_error, busy))

    def live(self, slot: int, offset: int, value: int):
        with self._plock:
            first = not self._pending
            self._pending[(slot, offset)] = value
        if first:
            self.q.put(_FLUSH)

    def clear_live(self):
        with self._plock:
            self._pending.clear()

    def stop(self):
        self.q.put(None)

    def run(self):
        while (item := self.q.get()) is not None:
            if item is _FLUSH:
                with self._plock:
                    items, self._pending = self._pending, {}
                if not self.dev or not items:
                    continue
                try:
                    if time.monotonic() - self._last_live > PRELUDE_IDLE:
                        self.dev.info()
                    for (slot, off), v in items.items():
                        self.dev.write_live(slot, off, v)
                    self._last_live = time.monotonic()
                except Exception as e:  # noqa: BLE001
                    self.bridge.failed.emit(Job(None), e)
                continue
            job = item
            if job.busy:
                self.bridge.busy.emit(True)
            try:
                self.bridge.done.emit(job, job.fn(self))
            except Exception as e:  # noqa: BLE001
                self.bridge.failed.emit(job, e)
            finally:
                if job.busy:
                    self.bridge.busy.emit(False)


# ---------------------------------------------------------------- novo preset (formulário)

DEFAULT_FIELDS = bytes([4, 3, 8, 4, 30, 90, 12, 0, 8, 1, 1, 1, 1, 0, 0, 0])  # som neutro de partida


class NewPresetDialog(QtWidgets.QDialog):
    """Formulário rápido: nome + 10 valores digitados + 3 seções."""

    def __init__(self, win: "MainWindow", start: bytes):
        super().__init__(win)
        self.setWindowTitle("Novo preset")
        self.setStyleSheet(f"""
            QDialog {{ background:{PANEL}; }}
            QSpinBox, QLineEdit {{ background:{PANEL_2}; border:1px solid {BORDER}; border-radius:7px;
                                  padding:5px 8px; font-weight:700; min-width:76px; }}
            QSpinBox:focus, QLineEdit:focus {{ border-color:#5b6372; }}
            QCheckBox {{ font-weight:600; }}
        """)
        self.load_after = False
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(12)
        self.name = QtWidgets.QLineEdit(placeholderText="Nome do preset (ex.: Solo com delay)")
        self.name.setStyleSheet("font-size:15px; padding:8px 10px;")
        lay.addWidget(self.name)

        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)
        self.spins: dict[str, QtWidgets.QSpinBox] = {}
        self.checks: dict[str, QtWidgets.QCheckBox] = {}
        for col, sec in enumerate(("drive", "fx", "amb", "master")):
            head = QtWidgets.QLabel(SECTION_TITLE[sec])
            head.setStyleSheet(f"color:{ACCENT[sec]}; font-weight:800;")
            grid.addWidget(head, 0, col * 2, 1, 2)
            row = 1
            if sec in SECTIONS:
                chk = QtWidgets.QCheckBox("ligado")
                self.checks[sec] = chk
                grid.addWidget(chk, row, col * 2, 1, 2)
                row += 1
            for key in SECTION_ORDER[sec]:
                prm = PRM[key]
                spin = QtWidgets.QSpinBox()
                shift = 1 if key == "type" else 0
                spin.setRange(shift, prm.maximum + shift)
                spin.setToolTip({"type": "Modelo de amp 1–9",
                                 "modulation": "0–6 chorus · 7–8 desligado · 9–15 phaser",
                                 "cabinet": "Gabinete (IR) 0–8"}.get(key, f"0–{prm.maximum}"))
                spin.setSuffix(f"  / {prm.maximum + shift}")
                self.spins[key] = spin
                lbl = QtWidgets.QLabel(prm.label, objectName="muted")
                lbl.setBuddy(spin)
                grid.addWidget(lbl, row, col * 2)
                grid.addWidget(spin, row, col * 2 + 1)
                row += 1
        lay.addLayout(grid)
        lay.addWidget(QtWidgets.QLabel("Dica: digite o número e aperte <b>Tab</b> para ir ao próximo campo. "
                                       "MOD: 0–6 chorus, 7–8 desligado, 9–15 phaser.", objectName="muted"))

        btns = QtWidgets.QHBoxLayout()
        b_cur = QtWidgets.QPushButton("Usar som atual")
        b_cur.clicked.connect(lambda: self.fill(win._fields()))
        b_def = QtWidgets.QPushButton("Valores padrão")
        b_def.clicked.connect(lambda: self.fill(DEFAULT_FIELDS))
        b_save = QtWidgets.QPushButton("Salvar")
        b_save.setToolTip("Salva em Meus presets sem mudar o som atual")
        b_save.clicked.connect(lambda: self._finish(False))
        b_load = QtWidgets.QPushButton("Salvar e carregar", objectName="primary")
        b_load.setToolTip("Salva e já aplica no preset atual do pedal")
        b_load.setDefault(True)
        b_load.clicked.connect(lambda: self._finish(True))
        for b in (b_cur, b_def):
            btns.addWidget(b)
        btns.addStretch()
        btns.addWidget(b_save)
        btns.addWidget(b_load)
        lay.addLayout(btns)
        self.fill(start)
        self.name.setFocus()

    def fill(self, f: bytes):
        for key, spin in self.spins.items():
            spin.setValue(f[PRM[key].offset] + (1 if key == "type" else 0))
        for sec, chk in self.checks.items():
            chk.setChecked(bool(f[SECTIONS[sec][1]]))

    def values(self) -> dict:
        d = {key: spin.value() - (1 if key == "type" else 0) for key, spin in self.spins.items()}
        for sec, chk in self.checks.items():
            d[SECTIONS[sec][0]] = 1 if chk.isChecked() else 0
        return d

    def _finish(self, load: bool):
        if not self.name.text().strip():
            self.name.setFocus()
            self.name.setPlaceholderText("Digite um nome para o preset")
            self.name.setStyleSheet(f"font-size:15px; padding:8px 10px; border-color:{BAD};")
            return
        self.load_after = load
        self.accept()


# ---------------------------------------------------------------- celular


def qr_pixmap(text: str, size: int = 260) -> QtGui.QPixmap:
    m = qr_encode(text)
    n = len(m) + 4  # borda de 2 módulos de cada lado
    cell = max(1, size // n)
    pix = QtGui.QPixmap(n * cell, n * cell)
    pix.fill(QtGui.QColor("white"))
    p = QtGui.QPainter(pix)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QtGui.QColor("black"))
    for y, row in enumerate(m):
        for x, on in enumerate(row):
            if on:
                p.drawRect((x + 2) * cell, (y + 2) * cell, cell, cell)
    p.end()
    return pix


class PhoneDialog(QtWidgets.QDialog):
    def __init__(self, win: "MainWindow"):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Controlar pelo celular")
        self.setStyleSheet(f"QDialog {{ background:{PANEL}; }}")
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(10)
        t = QtWidgets.QLabel("Leia o QR code com a câmera do celular")
        t.setStyleSheet("font-size:16px; font-weight:700;")
        lay.addWidget(t)
        lay.addWidget(QtWidgets.QLabel("O celular precisa estar na <b>mesma rede Wi-Fi</b> que este computador.",
                                       objectName="muted"))
        self.qr = QtWidgets.QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.qr.setMinimumSize(280, 280)
        self.qr.setStyleSheet("background:white; border-radius:12px;")
        lay.addWidget(self.qr, alignment=Qt.AlignmentFlag.AlignCenter)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Rede:", objectName="muted"))
        self.ip = QtWidgets.QComboBox()
        self.ip.setToolTip("Se o celular não abrir a página, tente outro endereço da lista")
        self.ip.currentIndexChanged.connect(self._render)
        row.addWidget(self.ip, 1)
        lay.addLayout(row)
        self.url = QtWidgets.QLineEdit(readOnly=True)
        self.url.setStyleSheet(f"background:{PANEL_2}; border:1px solid {BORDER}; border-radius:8px; padding:7px;")
        lay.addWidget(self.url)
        self.note = QtWidgets.QLabel(objectName="muted", wordWrap=True)
        lay.addWidget(self.note)
        btns = QtWidgets.QHBoxLayout()
        copy = QtWidgets.QPushButton("Copiar link")
        copy.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(self.url.text()))
        off = QtWidgets.QPushButton("Desligar acesso")
        off.setToolTip("Fecha o acesso pelo celular. Ao ligar de novo, um novo QR code é gerado.")
        off.clicked.connect(self._off)
        close = QtWidgets.QPushButton("Fechar", objectName="primary")
        close.clicked.connect(self.hide)
        btns.addWidget(copy)
        btns.addWidget(off)
        btns.addStretch()
        btns.addWidget(close)
        lay.addLayout(btns)

    def refresh(self):
        self.ip.blockSignals(True)
        self.ip.clear()
        for ip in local_ips():
            label = ip
            if ip.startswith("100."):
                label += "  (Tailscale)"
            elif ip.startswith(("172.17.", "172.18.", "172.19.")):
                label += "  (Docker — não use)"
            self.ip.addItem(label, ip)
        self.ip.blockSignals(False)
        self._render()

    def _render(self):
        if not self.win.remote:
            return
        url = self.win.remote.url(self.ip.currentData() or "127.0.0.1")
        self.url.setText(url)
        self.qr.setPixmap(qr_pixmap(url))
        hint = ""
        fw = ("Se o celular não abrir: libere a porta no firewall — "
              f"<code>sudo ufw allow {self.win.remote.port}/tcp</code>." if sys.platform.startswith("linux") else
              "Se o Windows perguntar sobre o firewall, permita o acesso em <b>redes privadas</b>.")
        self.note.setText(hint + fw + " Qualquer pessoa com este link na sua rede pode controlar o pedal.")

    def _off(self):
        self.win.stop_phone()
        self.hide()


# ---------------------------------------------------------------- janela


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Cube Baby Editor {__version__}")
        self.setMinimumSize(1060, 620)
        self.lib = Library()
        self.bank = bytearray(48)   # estado de trabalho (o que está soando)
        self.saved = bytes(48)      # o que está gravado no pedal
        self.slot = 0
        self.connected = False
        self._updating = False
        self._msg, self._msg_err = "", False
        self._loaded_name = ""
        self.remote: RemoteServer | None = None
        self._phone_dialog = None

        self.bridge = Bridge()
        self.bridge.done.connect(self._job_done)
        self.bridge.failed.connect(self._job_failed)
        self.bridge.busy.connect(self._set_busy)
        self.bridge.remote.connect(self._remote_cmd)
        self.bridge.site.connect(self._site_result)
        self._site_busy = False
        self.worker = Worker(self.bridge)
        self.worker.start()

        self._build()
        self._refresh_library()
        self._set_connected(False)
        QtCore.QTimer.singleShot(200, self.connect_device)

    # ---- layout
    def _build(self):
        root = QtWidgets.QWidget(objectName="root")
        self.setCentralWidget(root)
        outer = QtWidgets.QVBoxLayout(root)
        outer.setContentsMargins(20, 16, 20, 14)
        outer.setSpacing(14)

        # topo
        top = QtWidgets.QHBoxLayout()
        brand = QtWidgets.QLabel("CUBE <span style='color:%s'>BABY</span>" % ACCENT["drive"])
        brand.setStyleSheet("font-size:22px; font-weight:900; letter-spacing:2px;")
        sub = QtWidgets.QLabel("editor de presets", objectName="muted")
        self.status_dot = QtWidgets.QLabel()
        self.status_dot.setFixedSize(10, 10)
        self.status_lbl = QtWidgets.QLabel()
        self.btn_connect = QtWidgets.QPushButton("Conectar")
        self.btn_connect.clicked.connect(self.connect_device)
        self.btn_phone = QtWidgets.QPushButton("📱  Celular")
        self.btn_phone.setToolTip("Controlar pelo celular (lendo um QR code)")
        self.btn_phone.clicked.connect(self.show_phone)
        top.addWidget(brand)
        top.addSpacing(8)
        top.addWidget(sub)
        top.addStretch()
        top.addWidget(self.status_dot)
        top.addWidget(self.status_lbl)
        top.addSpacing(8)
        top.addWidget(self.btn_connect)
        top.addWidget(self.btn_phone)
        outer.addLayout(top)

        body = QtWidgets.QHBoxLayout()
        body.setSpacing(16)
        outer.addLayout(body, 1)

        # coluna principal
        main = QtWidgets.QVBoxLayout()
        main.setSpacing(14)
        body.addLayout(main, 1)

        slots = QtWidgets.QHBoxLayout()
        self.slot_group = QtWidgets.QButtonGroup(self)
        self.slot_btns = []
        for i, s in enumerate("ABC"):
            b = QtWidgets.QPushButton(s, objectName="slot")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setToolTip(f"Preset {s}  (tecla {s}, Ctrl+{i + 1} ou F{i + 1})")
            self.slot_group.addButton(b, i)
            self.slot_btns.append(b)
            slots.addWidget(b)
        self.slot_group.idClicked.connect(self.select_slot)
        self.slot_btns[0].setChecked(True)
        slots.addSpacing(14)
        info = QtWidgets.QVBoxLayout()
        self.slot_title = QtWidgets.QLabel()
        self.slot_title.setStyleSheet("font-size:16px; font-weight:700;")
        hint = QtWidgets.QLabel("Deixe o pedal no mesmo preset para ouvir as mudanças ao vivo.<br>"
                                "<b>A/B/C</b> troca o preset · <b>duplo clique</b> no knob para digitar · "
                                "<b>Ctrl+S</b> grava", objectName="muted")
        info.addWidget(self.slot_title)
        info.addWidget(hint)
        slots.addLayout(info, 1)
        main.addLayout(slots)

        self.cards: dict[str, SectionCard] = {}
        grid = QtWidgets.QGridLayout()
        grid.setSpacing(14)
        for sec, (r, c) in {"drive": (0, 0), "fx": (0, 1), "amb": (1, 0), "master": (1, 1)}.items():
            card = SectionCard(sec)
            self.cards[sec] = card
            grid.addWidget(card, r, c)
            for key, k in card.knobs.items():
                k.valueChanged.connect(lambda v, key=key: self._knob_changed(key, v))
            if card.toggle:
                card.toggle.toggled.connect(lambda on, sec=sec: self._section_toggled(sec, on))
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        main.addLayout(grid)
        main.addStretch()

        # rodapé
        foot = QtWidgets.QHBoxLayout()
        self.btn_save = QtWidgets.QPushButton("Gravar no pedal", objectName="primary")
        self.btn_save.setToolTip("Grava o slot atual na memória do pedal (fica salvo ao desligar)")
        self.btn_save.clicked.connect(self.save_to_pedal)
        self.btn_discard = QtWidgets.QPushButton("Descartar alterações")
        self.btn_discard.clicked.connect(self.discard)
        self.btn_bank = QtWidgets.QPushButton("Backup")
        menu = QtWidgets.QMenu(self)
        menu.addAction("Exportar banco A/B/C…", self.export_bank)
        menu.addAction("Restaurar banco de um arquivo…", self.restore_bank)
        menu.addSeparator()
        menu.addAction("Abrir pasta de backups", lambda: QtGui.QDesktopServices.openUrl(
            QtCore.QUrl.fromLocalFile(str(BACKUP_DIR))))
        menu.addAction("Recarregar do pedal", self.reload_from_pedal)
        self.btn_bank.setMenu(menu)
        self.msg = QtWidgets.QLabel(objectName="muted")
        foot.addWidget(self.btn_save)
        foot.addWidget(self.btn_discard)
        foot.addWidget(self.btn_bank)
        foot.addSpacing(10)
        foot.addWidget(self.msg, 1)
        main.addLayout(foot)

        # barra lateral: biblioteca
        side = QtWidgets.QFrame(objectName="card")
        side.setFixedWidth(290)
        sl = QtWidgets.QVBoxLayout(side)
        sl.setContentsMargins(14, 14, 14, 14)
        t = QtWidgets.QLabel("Meus presets")
        t.setStyleSheet("font-size:15px; font-weight:700;")
        sl.addWidget(t)
        sl.addWidget(QtWidgets.QLabel("Salvos neste computador. Duplo clique para carregar.", objectName="muted",
                                      wordWrap=True))
        self.search = QtWidgets.QLineEdit(placeholderText="Buscar…")
        self.search.setStyleSheet(f"background:{PANEL_2}; border:1px solid {BORDER}; border-radius:8px; padding:7px;")
        self.search.textChanged.connect(self._refresh_library)
        sl.addWidget(self.search)
        self.list = QtWidgets.QListWidget()
        self.list.itemDoubleClicked.connect(lambda _: self.load_preset())
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._list_menu)
        sl.addWidget(self.list, 1)
        b_new = QtWidgets.QPushButton("＋  Salvar som atual")
        b_new.clicked.connect(self.save_preset)
        sl.addWidget(b_new)
        b_form = QtWidgets.QPushButton("📝  Novo preset (digitar valores)")
        b_form.setToolTip("Formulário rápido para digitar os valores de um preset (Ctrl+N)")
        b_form.clicked.connect(self.new_preset)
        sl.addWidget(b_form)
        b_site = QtWidgets.QPushButton("🔗  Importar do site")
        b_site.setToolTip("Cole o link de um preset de cube-baby-presets.vercel.app\n"
                          "(ex.: .../pt/presets/902) — ele é salvo em Meus presets e carregado.")
        b_site.clicked.connect(self.import_from_site)
        sl.addWidget(b_site)
        row = QtWidgets.QHBoxLayout()
        for text, fn in (("Carregar", self.load_preset), ("Excluir", self.delete_preset)):
            b = QtWidgets.QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        sl.addLayout(row)
        row = QtWidgets.QHBoxLayout()
        for text, fn in (("Importar…", self.import_preset), ("Exportar…", self.export_preset)):
            b = QtWidgets.QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        sl.addLayout(row)
        body.addWidget(side)

        QtGui.QShortcut(QtGui.QKeySequence.StandardKey.Save, self, self.save_to_pedal)
        QtGui.QShortcut(QtGui.QKeySequence.StandardKey.New, self, self.new_preset)
        for i in range(3):
            for seq in (f"Ctrl+{i + 1}", f"F{i + 1}"):
                QtGui.QShortcut(QtGui.QKeySequence(seq), self, lambda i=i: self.select_slot(i))
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+PgDown"), self, lambda: self.select_slot((self.slot + 1) % 3))
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+PgUp"), self, lambda: self.select_slot((self.slot - 1) % 3))

    def keyPressEvent(self, e):
        # Teclas A/B/C trocam o preset quando nenhum campo de texto está em foco
        # (o campo de busca consome as letras antes de chegarem aqui).
        idx = {Qt.Key.Key_A: 0, Qt.Key.Key_B: 1, Qt.Key.Key_C: 2}.get(e.key())
        if idx is not None and not e.modifiers() & ~Qt.KeyboardModifier.KeypadModifier:
            self.select_slot(idx)
        else:
            super().keyPressEvent(e)

    # ---- estado/visual
    def _fields(self, slot=None) -> bytes:
        s = self.slot if slot is None else slot
        return bytes(self.bank[s * SLOT_SIZE:(s + 1) * SLOT_SIZE])

    def _dirty(self, slot=None) -> bool:
        s = self.slot if slot is None else slot
        return self._fields(s) != self.saved[s * SLOT_SIZE:(s + 1) * SLOT_SIZE]

    def _refresh_ui(self):
        self._updating = True
        f = self._fields()
        for sec, card in self.cards.items():
            enabled = True
            if card.toggle:
                enabled = bool(f[SECTIONS[sec][1]])
                card.toggle.setChecked(enabled)
            for key, k in card.knobs.items():
                k.setValue(f[PRM[key].offset])
                k.setEnabled(self.connected and enabled)
            if card.toggle:
                card.toggle.setEnabled(self.connected)
        self._updating = False
        self._refresh_dirty()

    def _refresh_dirty(self):
        for i, b in enumerate(self.slot_btns):
            b.setText("ABC"[i] + (" •" if self._dirty(i) else ""))
        d = self._dirty()
        self.slot_title.setText(f"Preset {'ABC'[self.slot]}" + ("  —  alterações não gravadas" if d else ""))
        self.btn_save.setEnabled(self.connected and d)
        self.btn_discard.setEnabled(self.connected and d)
        self._publish()

    def _set_connected(self, on: bool):
        self.connected = on
        self.status_dot.setStyleSheet(f"background:{OK if on else BAD}; border-radius:5px;")
        self.status_lbl.setText("Conectado" if on else "Desconectado")
        self.btn_connect.setVisible(not on)
        self.btn_bank.setEnabled(on)
        for b in self.slot_btns:
            b.setEnabled(on)
        self._refresh_ui()

    def _set_busy(self, busy: bool):
        QtWidgets.QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor) if busy \
            else QtWidgets.QApplication.restoreOverrideCursor()

    def info(self, text: str, error=False):
        self.msg.setStyleSheet(f"color:{BAD if error else MUTED};")
        self.msg.setText(text)
        self._msg, self._msg_err = text, error
        self._publish()

    def _job_done(self, job, result):
        if job.on_done:
            job.on_done(result)

    def _job_failed(self, job, err):
        if job.on_error:
            job.on_error(err)
            return
        self.info(str(err), error=True)
        if isinstance(err, DeviceError) or "MIDI" in str(err):
            self._lost()

    def _lost(self):
        dev, self.worker.dev = self.worker.dev, None
        if dev:
            dev.close()
        self._set_connected(False)

    # ---- conexão
    def connect_device(self):
        self.info("Procurando o pedal…")

        def work(w):
            if w.dev:
                w.dev.close()
                w.dev = None
            dev = CubeBaby()
            bank = dev.read_bank()
            w.dev = dev
            return bank

        def done(bank):
            self.bank, self.saved = bytearray(bank), bytes(bank)
            try:
                path = backup_bank(bank)
                self.info(f"Conectado. Backup automático: {path.name}")
            except OSError:
                self.info("Conectado.")
            self._set_connected(True)
            self.select_slot(self.slot, push=True)

        def fail(err):
            self._set_connected(False)
            self.info(str(err), error=True)

        self.worker.submit(work, done, fail)

    # ---- edição
    def select_slot(self, i: int, push: bool = True):
        if not self.connected:
            return
        self.slot = i
        self.slot_btns[i].setChecked(True)
        self.worker.clear_live()
        self._refresh_ui()
        if push:
            fields = self._fields()
            self.worker.submit(lambda w: w.dev.push_slot_live(i, fields), busy=False)

    def _knob_changed(self, key: str, v: int):
        if self._updating:
            return
        off = PRM[key].offset
        self.bank[self.slot * SLOT_SIZE + off] = v
        self.worker.live(self.slot, off, v)
        self._refresh_dirty()

    def _section_toggled(self, sec: str, on: bool):
        if self._updating:
            return
        off = SECTIONS[sec][1]
        self.bank[self.slot * SLOT_SIZE + off] = 1 if on else 0
        self.worker.live(self.slot, off, 1 if on else 0)
        for k in self.cards[sec].knobs.values():
            k.setEnabled(on)
        self._refresh_dirty()

    def _apply_fields(self, fields: bytes, label: str):
        s = self.slot
        self.bank[s * SLOT_SIZE:(s + 1) * SLOT_SIZE] = fields
        self._refresh_ui()
        self.worker.clear_live()
        self.worker.submit(lambda w: w.dev.push_slot_live(s, fields), busy=False)
        self.info(label)

    def save_to_pedal(self):
        if not (self.connected and self._dirty()):
            return
        s = "ABC"[self.slot]
        if QtWidgets.QMessageBox.question(
                self, "Gravar no pedal",
                f"Gravar o som atual no preset {s} do pedal?\nO que estava gravado em {s} será substituído.") \
                != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self._do_save()

    def _do_save(self):
        if not (self.connected and self._dirty()):
            return
        s = "ABC"[self.slot]
        new = bytearray(self.saved)
        i = self.slot * SLOT_SIZE
        new[i:i + SLOT_SIZE] = self.bank[i:i + SLOT_SIZE]
        new = bytes(new)

        def done(_):
            self.saved = new
            self._refresh_dirty()
            self.info(f"Preset {s} gravado e verificado no pedal ✓")

        self.worker.submit(lambda w: w.dev.save_bank(new), done)

    def discard(self):
        i = self.slot * SLOT_SIZE
        self._apply_fields(self.saved[i:i + SLOT_SIZE], "Alterações descartadas.")

    def reload_from_pedal(self):
        def done(bank):
            self.bank, self.saved = bytearray(bank), bytes(bank)
            self.select_slot(self.slot)
            self.info("Presets relidos do pedal.")
        self.worker.submit(lambda w: w.dev.read_bank(), done)

    # ---- controle pelo celular
    def _publish(self):
        """Envia o estado atual para o(s) celular(es) conectado(s)."""
        if not (self.remote and self.remote.running):
            return
        f = self._fields()
        self.remote.publish({
            "connected": self.connected,
            "slot": self.slot,
            "dirty": [self._dirty(i) for i in range(SLOT_COUNT)],
            "params": {p.key: f[p.offset] for p in PARAMS},
            "sections": {sec: f[off] for sec, (_, off) in SECTIONS.items()},
            "meta": {p.key: {"label": p.label, "max": p.maximum} for p in PARAMS},
            "presets": [{"name": d.get("name", ""), "updated": d.get("updated", ""), "params": d.get("params", {})}
                        for d in self.lib.list()],
            "loaded": self._loaded_name,
            "msg": self._msg, "err": self._msg_err,
        })

    def _remote_cmd(self, cmd: dict):
        """Executa um comando do celular (já na thread do Qt)."""
        op = cmd.get("op")
        if op == "slot":
            i = cmd.get("slot")
            if i in (0, 1, 2):
                self.select_slot(i)
            return
        if not self.connected:
            return self.info("Pedal desconectado.", error=True)
        if op == "param":
            prm = PRM.get(cmd.get("key"))
            if prm and isinstance(cmd.get("value"), int):
                knob = self.cards[prm.section].knobs[prm.key]
                knob.setValue(cmd["value"])
                self._knob_changed(prm.key, knob.value())
        elif op == "section":
            card = self.cards.get(cmd.get("section"))
            if card and card.toggle:
                card.toggle.setChecked(bool(cmd.get("on")))
        elif op == "save":
            self._do_save()
        elif op == "discard":
            self.discard()
        elif op == "load":
            doc = self.lib.find(str(cmd.get("name", "")))
            if doc:
                self.load_preset(doc)
        elif op == "create_preset":
            if isinstance(cmd.get("params"), dict):
                self._create_preset(str(cmd.get("name", "")), cmd["params"], bool(cmd.get("load")))
        elif op in ("import", "import_text"):
            self.import_from_site(str(cmd.get("text", ""))[:20000], ask=False)
        elif op == "save_preset":
            name = str(cmd.get("name", "")).strip()[:60]
            if name:
                existing = self.lib.find(name)
                self.lib.save(name, self._fields(), existing["_path"] if existing else None)
                self._refresh_library()
                self.info(f"Preset “{name}” salvo no computador (pelo celular).")

    def show_phone(self):
        if self.remote is None:
            self.remote = RemoteServer(self.bridge.remote.emit)
        if not self.remote.running:
            try:
                self.remote.start()
            except OSError as e:
                return self.info(str(e), error=True)
            self._publish()
            self.btn_phone.setText("📱  Celular ●")
        if self._phone_dialog is None:
            self._phone_dialog = PhoneDialog(self)
        self._phone_dialog.refresh()
        self._phone_dialog.show()
        self._phone_dialog.raise_()

    def stop_phone(self):
        if self.remote:
            self.remote.stop()
            self.remote = None  # novo token na próxima vez: links antigos deixam de valer
        self.btn_phone.setText("📱  Celular")
        self.info("Controle pelo celular desligado.")

    # ---- backup de banco
    def export_bank(self):
        try:
            path = backup_bank(self.saved)
        except OSError as e:
            return self.info(str(e), error=True)
        dest, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Exportar banco", str(Path.home() / path.name),
                                                        "Banco Cube Baby (*.json)")
        if dest:
            Path(dest).write_bytes(path.read_bytes())
            self.info(f"Banco exportado para {dest}")

    def restore_bank(self):
        src, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Restaurar banco", str(BACKUP_DIR),
                                                       "Banco Cube Baby (*.json)")
        if not src:
            return
        try:
            data = read_bank_file(Path(src))
        except (OSError, ValueError) as e:
            return self.info(str(e), error=True)
        if QtWidgets.QMessageBox.question(self, "Restaurar banco",
                                          "Substituir os presets A, B e C do pedal por este backup?") \
                != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        def done(_):
            self.bank, self.saved = bytearray(data), data
            self.select_slot(self.slot)
            self.info("Banco restaurado ✓")
        self.worker.submit(lambda w: w.dev.save_bank(data), done)

    # ---- biblioteca
    def _refresh_library(self):
        q = self.search.text().strip().lower() if hasattr(self, "search") else ""
        self.list.clear()
        for doc in self.lib.list():
            if q and q not in doc.get("name", "").lower():
                continue
            it = QtWidgets.QListWidgetItem(f"{doc.get('name')}\n{doc.get('updated', '')}")
            it.setData(Qt.ItemDataRole.UserRole, doc)
            self.list.addItem(it)
        self._publish()

    def _selected(self) -> dict | None:
        it = self.list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def _list_menu(self, pos):
        if not self.list.itemAt(pos):
            return
        m = QtWidgets.QMenu(self)
        m.addAction("Carregar no preset atual", self.load_preset)
        m.addAction("Substituir pelo som atual", self.overwrite_preset)
        m.addAction("Renomear…", self.rename_preset)
        m.addAction("Exportar…", self.export_preset)
        m.addSeparator()
        m.addAction("Excluir", self.delete_preset)
        m.exec(self.list.mapToGlobal(pos))

    def save_preset(self):
        name, ok = QtWidgets.QInputDialog.getText(self, "Salvar preset", "Nome do preset:")
        name = name.strip()
        if not ok or not name:
            return
        existing = self.lib.find(name)
        if existing:
            if QtWidgets.QMessageBox.question(self, "Salvar preset", f"Já existe “{name}”. Substituir?") \
                    != QtWidgets.QMessageBox.StandardButton.Yes:
                return
            self.lib.save(name, self._fields(), existing["_path"])
        else:
            self.lib.save(name, self._fields())
        self._refresh_library()
        self.info(f"Preset “{name}” salvo no computador.")

    def import_from_site(self, text: str | None = None, ask: bool = True):
        """Importa um preset do cube-baby-presets: aceita o link, só o número ou o texto copiado da página."""
        src = text if isinstance(text, str) else QtWidgets.QApplication.clipboard().text()
        src = (src or "").strip()
        is_text = "valores dos knobs" in src.lower() or "knob values" in src.lower()
        if ask and not is_text:
            src, ok = QtWidgets.QInputDialog.getText(
                self, "Importar do site", "Link do preset em cube-baby-presets.vercel.app (ou só o número):",
                text=src if preset_url(src) else "")
            if not ok or not src.strip():
                return
            src = src.strip()
        if not is_text and preset_url(src):
            if self._site_busy:
                return self.info("Já estou buscando um preset, aguarde…")
            self._site_busy = True
            self.info("Buscando o preset no site… (a primeira vez pode levar uns 20 s)")

            def work():
                try:
                    res = fetch_preset(src)
                except Exception as e:  # noqa: BLE001
                    res = e
                self.bridge.site.emit(res, ask)
            threading.Thread(target=work, daemon=True).start()
            return
        try:
            got = parse_site_text(src)
        except ValueError as e:
            if not src:
                e = ValueError("Cole o link do preset (ex.: https://cube-baby-presets.vercel.app/pt/presets/902).")
            if ask:
                QtWidgets.QMessageBox.information(self, "Importar do site", str(e))
            return self.info(str(e), error=True)
        self._site_result(got, ask)

    def _site_result(self, got, ask: bool):
        self._site_busy = False
        if isinstance(got, Exception):
            if ask:
                QtWidgets.QMessageBox.information(self, "Importar do site", str(got))
            return self.info(str(got), error=True)
        name = got["name"] or "Preset do site"
        if got["author"]:
            name += f" — {got['author']}"
        if ask:
            name, ok = QtWidgets.QInputDialog.getText(self, "Importar do site", "Nome do preset:", text=name)
            if not ok or not name.strip():
                return
        name = name.strip()[:60]
        existing = self.lib.find(name)
        self.lib.save(name, dict_to_fields(got["params"], bytes(16)), existing["_path"] if existing else None)
        self._refresh_library()
        doc = self.lib.find(name)
        if self.connected and doc:
            self.load_preset(doc)
            self.info(f"“{name}” importado e carregado no preset {'ABC'[self.slot]} (Gravar no pedal para manter).")
        else:
            self.info(f"“{name}” importado para Meus presets.")

    def new_preset(self):
        dlg = NewPresetDialog(self, self._fields() if self.connected else DEFAULT_FIELDS)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            name = dlg.name.text().strip()
            if self.lib.find(name) and QtWidgets.QMessageBox.question(
                    self, "Novo preset", f"Já existe “{name}”. Substituir?") \
                    != QtWidgets.QMessageBox.StandardButton.Yes:
                return
            self._create_preset(name, dlg.values(), dlg.load_after)

    def _create_preset(self, name: str, params: dict, load: bool):
        name = name.strip()[:60]
        if not name:
            return self.info("Dê um nome ao preset.", error=True)
        clean = {}
        for key, v in params.items():  # só aceita campos conhecidos e números inteiros
            if (key in PRM or key in [n for n, _ in SECTIONS.values()]) and isinstance(v, int) and not isinstance(v, bool):
                clean[key] = v
        base = DEFAULT_FIELDS
        fields = dict_to_fields(clean, base)  # limita cada valor à faixa do knob
        existing = self.lib.find(name)
        self.lib.save(name, fields, existing["_path"] if existing else None)
        self._refresh_library()
        if load and self.connected:
            self.load_preset(self.lib.find(name))
            self.info(f"“{name}” criado e carregado no preset {'ABC'[self.slot]} (Gravar no pedal para manter).")
        else:
            self.info(f"“{name}” criado em Meus presets.")

    def overwrite_preset(self):
        doc = self._selected()
        if doc:
            self.lib.save(doc["name"], self._fields(), doc["_path"])
            self._refresh_library()
            self.info(f"“{doc['name']}” atualizado.")

    def rename_preset(self):
        doc = self._selected()
        if not doc:
            return
        name, ok = QtWidgets.QInputDialog.getText(self, "Renomear", "Novo nome:", text=doc["name"])
        if ok and name.strip():
            fields = dict_to_fields(doc["params"], bytes(16))
            Library.delete(doc)
            self.lib.save(name.strip(), fields)
            self._refresh_library()

    def load_preset(self, doc: dict | None = None):
        doc = doc or self._selected()
        if not doc:
            return self.info("Selecione um preset na lista.")
        if not self.connected:
            return self.info("Conecte o pedal para carregar um preset.", error=True)
        fields = dict_to_fields(doc["params"], self._fields())
        self._loaded_name = doc.get("name", "")
        self._apply_fields(fields, f"“{doc['name']}” carregado no preset {'ABC'[self.slot]} "
                                   "(clique em Gravar no pedal para manter).")

    def delete_preset(self):
        doc = self._selected()
        if doc and QtWidgets.QMessageBox.question(self, "Excluir", f"Excluir “{doc['name']}”?") \
                == QtWidgets.QMessageBox.StandardButton.Yes:
            Library.delete(doc)
            self._refresh_library()

    def import_preset(self):
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "Importar presets", str(Path.home()),
                                                          "Preset Cube Baby (*.json)")
        n = 0
        for f in files:
            try:
                doc = Library.read_file(Path(f))
                self.lib.save(doc.get("name") or Path(f).stem, dict_to_fields(doc["params"], bytes(16)))
                n += 1
            except (OSError, ValueError) as e:
                self.info(f"{Path(f).name}: {e}", error=True)
        if n:
            self._refresh_library()
            self.info(f"{n} preset(s) importado(s).")

    def export_preset(self):
        doc = self._selected()
        if not doc:
            return self.info("Selecione um preset na lista.")
        dest, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Exportar preset",
                                                        str(Path.home() / doc["_path"].name),
                                                        "Preset Cube Baby (*.json)")
        if dest:
            Library.export(doc, Path(dest))
            self.info(f"Exportado para {dest}")

    def closeEvent(self, e):
        if self.remote:
            self.remote.stop()
        self.worker.stop()
        self.worker.join(2)
        if self.worker.dev:
            self.worker.dev.close()
        super().closeEvent(e)


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Cube Baby Editor")
    app.setDesktopFileName("cube-baby-editor")
    app.setWindowIcon(QtGui.QIcon(str(ASSETS / ("icon.ico" if sys.platform == "win32" else "icon.png"))))
    if sys.platform == "win32":
        try:  # ícone próprio na barra de tarefas em vez do ícone do Python
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("GusCfer.CubeBabyEditor")
        except Exception:  # noqa: BLE001
            pass
    app.setStyleSheet(STYLE)
    w = MainWindow()
    w.resize(1120, 660)
    w.show()
    sys.exit(app.exec())
