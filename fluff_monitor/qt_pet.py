"""Qt frontend for Windows; the metadata, usage and placement policies are shared.

The Linux frontend remains GTK. QT_QPA_PLATFORM=offscreen can exercise this
frontend on Linux without controlling the user's desktop or claiming a Windows run.
"""
from __future__ import annotations

import argparse
import math
import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

from PySide6.QtCore import Qt, QTimer, Signal, QPoint, QRect, QPropertyAnimation
from PySide6.QtGui import QFont, QFontDatabase, QImage, QPainter, QPixmap, QColor
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QWidget, QFrame, QLabel, QPushButton, QHBoxLayout, QVBoxLayout, QSizePolicy

from .activity import ActivityCollector
from .catalog import SnapshotCatalog
from .layout import beside_position
from .platform_support import lock_exclusive
from .presentation import usage_text, route_status, menu_actions, model_text, gpt_request
from .pet_state import MismatchAlerts
from .storage import SnapshotReader, atomic_json, read_json, state_dir
from .views import desktop_view, claude_view

ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT / "vendor/claude-pet"))
from claude_pet.sprites import load_pet

CARD_WIDTH, PET_HEIGHT, GAP, INSET = 288, 120, 12, 8
THEMES = {
    "dark": dict(background="#232427", edge="#3a3b3f", foreground="#eeeeef", muted="#a9aab1", task="#c6c7ce", hover="#36373c", claude="#e8ab95"),
    "light": dict(background="#fafafa", edge="#d8d9de", foreground="#24252a", muted="#686b75", task="#50535c", hover="#eeeeef", claude="#98492e"),
}


def layout(widget, kind, margins=(0, 0, 0, 0), spacing=0):
    result = kind(widget)
    result.setContentsMargins(*margins)
    result.setSpacing(spacing)
    return result


class Text(QLabel):
    """Plain, elided text: task names cannot become rich text or external links."""
    hovered = Signal(bool)

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, self.width()))

    def enterEvent(self, event):
        self.hovered.emit(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hovered.emit(False)
        super().leaveEvent(event)

    def setFixedWidth(self, width):
        # Fixed columns must reserve their width in QBoxLayout; an Ignored
        # policy can otherwise allocate zero and overlap the next value.
        super().setFixedWidth(width)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)


class Mark(QWidget):
    def __init__(self, provider, parent=None):
        super().__init__(parent)
        self.provider = provider
        self.setFixedSize(16, 16)
        self.ink = "#eeeeef"

    def paintEvent(self, event):
        filename = "openai.svg" if self.provider == "gpt" else "claude-spark.svg"
        svg = (ROOT / "assets/providers" / filename).read_text().replace("currentColor", self.ink)
        painter = QPainter(self)
        QSvgRenderer(svg.encode()).render(painter)


class StatusDot(QWidget):
    def __init__(self):
        super().__init__()
        self.color = "#a9aab1"
        self.setFixedSize(5, 5)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self.color))
        painter.drawEllipse(self.rect())


class TitleButton(QPushButton):
    hovered = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("titleButton")
        self.setFixedHeight(24)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.caption = Text()
        self.arrow = Text("⌄")
        self.arrow.setFixedWidth(10)
        box = layout(self, QHBoxLayout, (INSET, 0, INSET, 0), 4)
        box.addWidget(self.caption, 1)
        box.addWidget(self.arrow)

    def set_title(self, text):
        self.caption.setText(text)
        self.setAccessibleName(text)

    def enterEvent(self, event):
        self.hovered.emit(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hovered.emit(False)
        super().leaveEvent(event)


class Surface(QFrame):
    def __init__(self, owner):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.owner = owner
        self.setObjectName("popup")
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
        elif event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self.focusNextPrevChild(event.key()==Qt.Key.Key_Down)
        else:
            super().keyPressEvent(event)


class ChoicePopup(Surface):
    """One selector implementation for both providers and the small action menu."""
    def __init__(self, owner):
        super().__init__(owner)
        # Native popup semantics dismiss on an outside click and return focus;
        # a Tool window would linger above other applications.
        self.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
        self.box = layout(self, QVBoxLayout, (5, 5, 5, 5), 0)
        self.entries = []
        self.setFixedWidth(250)

    def populate(self, entries, selected, choose, auto=True):
        while self.box.count():
            child = self.box.takeAt(0).widget()
            if child:
                child.hide()
                child.deleteLater()
        self.entries = []
        if auto:
            entries = [(None, "자동 선택"), *entries]
        for key, title in entries:
            button = QPushButton()
            button.setObjectName("choice")
            button.setFixedHeight(32)
            button.setAccessibleName(title)
            row = layout(button, QHBoxLayout, (8, 0, 8, 0), 8)
            marker = Text("•" if auto and key == selected else "")
            marker.setFixedWidth(12)
            row.addWidget(marker)
            row.addWidget(Text(title), 1)
            button.clicked.connect(lambda checked=False, k=key: (self.hide(), choose(k)))
            self.box.addWidget(button)
            self.entries.append(button)
        self.adjustSize()

    def open_at(self, anchor, beside=False):
        self.owner.title_popup.hide()
        self.adjustSize()
        area = self.owner.screen_area()
        pet = self.owner.pet_rect()
        if beside:
            bounds = pet
            avoid = [self.owner.rect_tuple()] if self.owner.isVisible() else []
            point = beside_position(bounds, (self.width(), self.height()), area, avoid)
        else:
            at = anchor.mapToGlobal(QPoint(0, anchor.height()+4))
            card = self.owner.rect_tuple()
            x = min(max(at.x(), card[0]), card[0]+card[2]-self.width())
            y = at.y() if at.y()+self.height() <= area.y+area.height else at.y()-anchor.height()-self.height()-8
            point = (max(area.x, min(x, area.x+area.width-self.width())), max(area.y, y))
            if QRect(*point, self.width(), self.height()).intersects(QRect(*pet)):
                point = beside_position(card, (self.width(), self.height()), area, [pet])
        if point is not None:
            self.move(*point)
            self.show()
            if self.entries:
                self.entries[0].setFocus()


class TitlePopup(Surface):
    def __init__(self, owner):
        super().__init__(owner)
        self.setObjectName("titlePopup")
        self.anchor = None
        self.button = TitleButton(self)
        self.box = layout(self, QVBoxLayout)
        self.box.addWidget(self.button)
        self.animation = QPropertyAnimation(self, b"geometry", self)
        self.animation.setDuration(130)
        self.button.hovered.connect(self._hover)
        self.button.clicked.connect(self._click)

    def _hover(self, entered):
        self.owner.title_hover = entered
        if not entered:
            self.owner.title_timer.start(180)

    def _click(self):
        self.hide()
        if isinstance(self.anchor, TitleButton):
            self.anchor.click()

    def reveal(self, anchor):
        is_title = isinstance(anchor, TitleButton)
        source = anchor.caption if is_title else anchor
        if self.isVisible() and self.anchor is anchor and self.button.caption.text() == source.text():
            return
        self.anchor = anchor
        text_width = source.fontMetrics().horizontalAdvance(source.text())
        if text_width <= source.width():
            self.hide()
            return
        self.button.set_title(source.text())
        self.button.arrow.setVisible(is_title)
        self.button.caption.setStyleSheet("" if is_title else source.styleSheet())
        pos = anchor.mapToGlobal(QPoint(0, 0))
        initial = anchor.width()
        if not is_title:
            pos -= QPoint(INSET, (24-anchor.height())//2)
            initial += 2*INSET
            self.button.caption.setStyleSheet(
                f'font-size: {source.font().pixelSize()}px; color: {source.palette().color(source.foregroundRole()).name()};')
        target = text_width + (34 if is_title else 2*INSET)
        area = self.owner.screen_area()
        target = min(420, target, area.x+area.width-pos.x())
        px, py, pw, ph = self.owner.pet_rect()
        if pos.y() < py+ph and pos.y()+24 > py and pos.x() < px:
            target = min(target, px-GAP-pos.x())
        if target <= initial:
            self.hide()
            return
        start = QRect(pos.x(), pos.y(), initial, 24)
        self.setGeometry(start)
        self.show()
        self.animation.stop()
        self.animation.setStartValue(start)
        self.animation.setEndValue(QRect(pos.x(), pos.y(), target, 24))
        self.animation.start()


class ProviderSection(QWidget):
    def __init__(self, provider, owner):
        super().__init__(owner)
        self.provider, self.owner = provider, owner
        box = layout(self, QVBoxLayout, spacing=3)
        self.setFixedHeight(82)
        header = QWidget()
        header.setFixedHeight(24)
        row = layout(header, QHBoxLayout, spacing=6)
        brand = QWidget()
        brand.setFixedWidth(68)
        brand_row = layout(brand, QHBoxLayout, spacing=6)
        self.mark = Mark(provider)
        self.brand = Text("GPT" if provider == "gpt" else "Claude")
        self.brand.setObjectName(provider+"Brand")
        brand_row.addWidget(self.mark)
        brand_row.addWidget(self.brand, 1)
        row.addWidget(brand)
        self.title = TitleButton()
        self.title.clicked.connect(lambda: owner.choose_provider(provider))
        self.title.hovered.connect(lambda entered: owner.hover_title(self.title, entered))
        row.addWidget(self.title, 1)
        if provider == "gpt":
            more = QPushButton("⋯")
            more.clicked.connect(lambda: owner.actions("panel"))
            self.options_button = more
        else:
            more = QWidget()
        more.setFixedSize(24, 24)
        row.addWidget(more)
        box.addWidget(header)
        self.requested = self.add_row(box, "요청" if provider == "gpt" else "설정", 17)
        self.served = self.add_row(box, "응답", 17)
        self.status, self.cache = Text("대기"), Text("")
        self.status.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.status.hovered.connect(lambda entered: owner.hover_title(self.status, entered))
        self.status.setObjectName("muted")
        self.cache.setObjectName("muted")
        footer = QWidget()
        footer.setFixedHeight(15)
        row = layout(footer, QHBoxLayout, spacing=6)
        dot = QWidget()
        dot.setFixedWidth(16)
        self.dot = StatusDot()
        dot_box = layout(dot, QHBoxLayout)
        dot_box.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignCenter)
        self.status.setFixedWidth(54)
        row.addWidget(dot)
        row.addWidget(self.status)
        row.addWidget(self.cache, 1)
        box.addWidget(footer)

    @staticmethod
    def add_row(box, name, height):
        widget = QWidget()
        widget.setFixedHeight(height)
        row = layout(widget, QHBoxLayout, spacing=6)
        blank = QWidget()
        blank.setFixedWidth(16)
        caption = Text(name)
        caption.setObjectName("muted")
        caption.setFixedWidth(54)
        value = Text("—")
        value.caption_label = caption
        row.addWidget(blank)
        row.addWidget(caption)
        row.addWidget(value, 1)
        box.addWidget(widget)
        return value

    def update_data(self, value):
        self.title.set_title(value.get("title") or "현재 작업 없음")
        if self.provider == "gpt":
            caption, requested, effort, detail = gpt_request(value)
            self.requested.caption_label.setText(caption)
            self.requested.setToolTip(detail)
            status, tone = route_status(value)
            self.status.setToolTip(value.get("detail") or "서버 응답의 모델명 관측 상태입니다.")
            served = value.get("served") or ("모델명 미수집" if value.get("verdict")=="OBSERVATION_GAP" else "—")
        else:
            requested, effort = value.get("requested_model"), value.get("requested_effort")
            status, served = value.get("label", "대기"), value.get("served") or "기록 대기"
            tone = "blue" if value.get("state")=="running" else "neutral"
        self.requested.setText((model_text(requested) or "—")+(" · "+effort if effort else ""))
        self.served.setText(model_text(served))
        self.status.setText(status)
        light = self.owner.settings.get("theme") == "light"
        colors = dict(neutral="#686b75",blue="#486798",waiting="#886020",bad="#b44653") if light else dict(neutral="#a9aab1",blue="#a4bbed",waiting="#e4c18b",bad="#f09b9f")
        self.status.setStyleSheet("color: "+colors[tone])
        self.dot.color = colors[tone]
        self.dot.update()
        text, detail = usage_text(value.get("usage"), value.get("metrics_available", True))
        self.cache.setText(text)
        self.cache.setToolTip(detail)


class Panel(Surface):
    def __init__(self, directory, settings, save):
        super().__init__(None)
        self.owner = self
        self.directory, self.settings, self.save = directory, settings, save
        self.pet = None
        self.pinned = False
        self.route, self.claude = {}, {}
        self.setObjectName("panel")
        self.setFixedWidth(CARD_WIDTH)
        self.box = layout(self, QVBoxLayout, (11, 10, 11, 10))
        self.gpt = ProviderSection("gpt", self)
        self.anthropic = ProviderSection("claude", self)
        self.box.addWidget(self.gpt)
        separator = QWidget()
        separator.setFixedHeight(15)
        divider = layout(separator, QVBoxLayout, (0, 7, 0, 7))
        line = QFrame()
        line.setObjectName("divider")
        line.setFixedHeight(1)
        divider.addWidget(line)
        self.box.addWidget(separator)
        self.box.addWidget(self.anthropic)
        self.popup = ChoicePopup(self)
        self.title_popup = TitlePopup(self)
        self.title_hover = False
        self.title_anchor = None
        self.title_timer = QTimer(self)
        self.title_timer.setSingleShot(True)
        self.title_timer.timeout.connect(self._show_title)
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self._hide_if_outside)
        self.apply_theme()
        self.adjustSize()

    def apply_theme(self):
        c = THEMES.get(self.settings.get("theme"), THEMES["dark"])
        css = (f'QWidget {{ font-family: "Pretendard Variable"; font-size: 13px; color: {c["foreground"]}; }}'
               f'#panel, #popup {{ background: {c["background"]}; border: 1px solid {c["edge"]}; border-radius: 10px; }}'
               f'#divider {{ background: {c["edge"]}; }}'
               f'#muted {{ font-size: 12px; color: {c["muted"]}; }}'
               f'#claudeBrand {{ color: {c["claude"]}; font-weight: 600; }} #gptBrand {{ font-weight: 600; }}'
               'QPushButton { border: none; background: transparent; border-radius: 6px; padding: 0; }'
               f'QPushButton:hover, #titlePopup {{ background: {c["hover"]}; border-radius: 6px; }}'
               f'#titleButton QLabel {{ font-size: 12px; color: {c["task"]}; }}')
        for widget in (self, self.popup, self.title_popup):
            widget.setStyleSheet(css)
        self.gpt.mark.ink = c["foreground"]
        self.gpt.mark.update()

    def screen_area(self):
        screen = QApplication.screenAt(self.pet.pos()) if self.pet else QApplication.primaryScreen()
        rect = (screen or QApplication.primaryScreen()).availableGeometry()
        return SimpleNamespace(x=rect.x(), y=rect.y(), width=rect.width(), height=rect.height())

    def rect_tuple(self):
        return self.x(), self.y(), self.width(), self.height()

    def pet_rect(self):
        return (self.pet.x(), self.pet.y(), self.pet.width(), self.pet.height()) if self.pet else (600, 300, 112, 120)

    def place(self):
        point = beside_position(self.pet_rect(), (self.width(), self.height()), self.screen_area())
        if point:
            self.move(*point)

    def reveal(self):
        self.hide_timer.stop()
        self.place()
        self.show()

    def enterEvent(self, event):
        self.hide_timer.stop()

    def leaveEvent(self, event):
        self.hide_timer.start(420)

    def _hide_if_outside(self):
        if not self.pinned and not self.underMouse() and not self.title_hover and not self.popup.isVisible() and not (self.pet and self.pet.underMouse()):
            self.hide()
            self.title_popup.hide()

    def hover_title(self, anchor, entered):
        self.title_anchor = anchor if entered else None
        self.title_timer.start(180)

    def _show_title(self):
        if self.popup.isVisible():
            return
        if self.title_anchor:
            self.title_popup.reveal(self.title_anchor)
        elif not self.title_hover:
            self.title_popup.hide()

    def choose_provider(self, provider):
        value = self.route if provider == "gpt" else self.claude
        config_key = "codex_thread" if provider == "gpt" else "claude_session"
        entries = [(s["thread_id"] if provider=="gpt" else s.get("selection_key", s.get("session_id")), s.get("title") or "이름 없는 작업") for s in value.get("sessions", [])]
        def choose(key):
            if key:self.settings[config_key] = key
            else:self.settings.pop(config_key, None)
            self.save()
        self.popup.setFixedWidth(250)
        self.popup.populate(entries, self.settings.get(config_key), choose)
        self.popup.open_at(self.gpt.title if provider=="gpt" else self.anthropic.title)

    def actions(self, surface="panel"):
        def choose(key):
            if key=="theme":
                self.settings["theme"] = "light" if self.settings.get("theme", "dark")=="dark" else "dark"
                self.apply_theme();self.save()
            elif key=="pin":self.pinned = not self.pinned
            elif key=="walk":self.pet.walking = not self.pet.walking
            elif key=="pet":self.pet.gesture = ("waving", time.monotonic()+2)
            elif key=="quit":QApplication.quit()
        actions = menu_actions(surface, theme=self.settings.get("theme", "dark"), pinned=self.pinned,
                               walking=bool(self.pet and self.pet.walking))
        self.popup.hide()
        self.popup.setFixedWidth(182 if surface == "panel" else 164)
        self.popup.populate(actions,None,choose,auto=False)
        self.popup.open_at(self.gpt.options_button if surface == "panel" else self, beside=surface == "pet")

    def update_data(self, route, claude):
        self.route, self.claude = route, claude
        self.gpt.update_data(route)
        self.anthropic.update_data(claude)
        if self.title_popup.isVisible() and isinstance(self.title_popup.anchor, Text):
            self.title_popup.reveal(self.title_popup.anchor)


class PetWindow(QWidget):
    def __init__(self, pet_dir, panel):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.panel = panel
        self.pack = load_pet(pet_dir)
        self.frames = {}
        for state, frames in self.pack.animations.items():
            self.frames[state] = []
            for frame in frames:
                raw = frame.tobytes("raw", "RGBA")
                image = QImage(raw, frame.width, frame.height, frame.width*4, QImage.Format.Format_RGBA8888).copy()
                self.frames[state].append(QPixmap.fromImage(image).scaledToHeight(PET_HEIGHT, Qt.TransformationMode.SmoothTransformation))
        sample = self.frames["idle"][0]
        self.setFixedSize(sample.size())
        self.state, self.index, self.dragging = "idle", 0, False
        self.gesture = ("idle", 0)
        self.walking, self.direction = False, 1
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.animate)
        self.timer.start(133)
        area = QApplication.primaryScreen().availableGeometry()
        position = panel.settings.get("pet_position", [area.right()-self.width()-32, area.bottom()-self.height()-32])
        self.move(*position)

    def paintEvent(self, event):
        frames = self.frames.get(self.state) or self.frames["idle"]
        painter = QPainter(self)
        painter.drawPixmap(0, 0, frames[self.index % len(frames)])

    def animate(self):
        if self.gesture[1] > time.monotonic():self.state = self.gesture[0]
        else:self.state = "running" if self.panel.route.get("activity")=="running" or self.panel.claude.get("state")=="running" else "idle"
        if self.walking and not self.dragging and not self.underMouse() and not self.panel.popup.isVisible() and not self.panel.isVisible():
            area = self.panel.screen_area()
            x = self.x()+self.direction*2
            if x < area.x or x+self.width() > area.x+area.width:
                self.direction *= -1
            else:self.move(x, self.y())
            self.state = "running-right" if self.direction>0 else "running-left"
        self.index += 1
        self.update()

    def enterEvent(self, event):self.panel.reveal()
    def leaveEvent(self, event):self.panel.hide_timer.start(420)

    def mousePressEvent(self, event):
        if event.button()==Qt.MouseButton.RightButton:
            self.panel.actions("pet");return
        if event.button()==Qt.MouseButton.LeftButton:
            self.press = event.globalPosition().toPoint()
            self.origin = self.pos()
            self.dragging = False

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            delta = event.globalPosition().toPoint()-self.press
            if delta.manhattanLength()>4:
                self.dragging = True
                self.move(self.origin+delta)
                self.panel.title_popup.hide()
                self.panel.place()

    def mouseReleaseEvent(self, event):
        if event.button()!=Qt.MouseButton.LeftButton:return
        if not self.dragging:
            self.panel.pinned = not self.panel.pinned
            self.panel.reveal()
        self.panel.settings["pet_position"] = [self.x(), self.y()]
        self.panel.save()
        self.dragging = False


class CollectorWorker:
    def __init__(self, directory):
        self.directory, self.done = directory, threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        with (self.directory/"activity.lock").open("a+b") as lock:
            if not lock_exclusive(lock):return
            collector = ActivityCollector(self.directory)
            while not self.done.is_set():
                collector.publish()
                if self.done.wait(2):break


class RoutingNotice(Surface):
    def __init__(self, owner):
        super().__init__(owner)
        self.setFixedSize(256, 88)
        box = layout(self, QVBoxLayout, (10, 8, 10, 8), 3)
        header = QWidget()
        row = layout(header, QHBoxLayout)
        row.addWidget(Text("응답 모델명 불일치"), 1)
        close = QPushButton("×")
        close.setFixedSize(18, 18)
        close.setAccessibleName("알림 닫기")
        close.clicked.connect(self.hide)
        row.addWidget(close)
        self.task, self.models = Text(), Text()
        box.addWidget(header)
        box.addWidget(self.task)
        box.addWidget(self.models)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide)

    def display(self, notice):
        self.task.setText(notice.get("title") or "이름 없는 작업")
        self.models.setText((str(notice.get("requested"))+" → "+str(notice.get("served"))) if notice.get("detailed") else f"최근 응답에서 불일치 {notice['count']}건")
        self.setStyleSheet(self.owner.styleSheet())
        avoid = [self.owner.rect_tuple()] if self.owner.isVisible() else []
        point = beside_position(self.owner.pet_rect(), (self.width(), self.height()), self.owner.screen_area(), avoid)
        if point:
            self.move(*point);self.show();self.timer.start(8000)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pet-dir", type=Path, default=Path.home()/".codex/pets/fluff")
    parser.add_argument("--no-collector", action="store_true", help="Use an already running collector or test snapshots")
    parser.add_argument("--test-seconds", type=float)
    args = parser.parse_args(argv)
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    lock = (directory/"pet.lock").open("a+b")
    if not lock_exclusive(lock):return 0
    app = QApplication.instance() or QApplication(["Fluff"])
    app.setQuitOnLastWindowClosed(False)
    QFontDatabase.addApplicationFont(str(ROOT/"assets/fonts/PretendardVariable.ttf"))
    settings_file = directory/"panel.json"
    settings = read_json(settings_file)
    save = lambda: atomic_json(settings_file, settings)
    panel = Panel(directory, settings, save)
    pet = PetWindow(args.pet_dir, panel)
    panel.pet = pet
    notices = RoutingNotice(panel)
    alerts = MismatchAlerts(directory/"pet-alerts.json")
    pet.show()
    catalog, snapshots = SnapshotCatalog(), SnapshotReader()
    worker = None if args.no_collector else CollectorWorker(directory)
    if worker:worker.thread.start()
    def refresh():
        activity = snapshots.read(directory/"activity.json")
        capture = snapshots.read(directory/"desktop.json")
        catalog.update(activity)
        route = desktop_view(directory,catalog=catalog,activity=activity,capture=capture,thread_id=settings.get("codex_thread"))
        claude = claude_view(activity,settings.get("claude_session"))
        changed = False
        if route.get("selection_expired"):
            settings.pop("codex_thread",None);changed=True
            route=desktop_view(directory,catalog=catalog,activity=activity,capture=capture)
        if claude.get("selection_expired"):
            settings.pop("claude_session",None);changed=True
            claude=claude_view(activity)
        if changed:save()
        panel.update_data(route,claude)
        for notice in alerts.poll(route):
            notices.display(notice)
    timer = QTimer()
    timer.timeout.connect(refresh)
    timer.start(500)
    refresh()
    if args.test_seconds:QTimer.singleShot(int(args.test_seconds*1000),app.quit)
    try:return app.exec()
    finally:
        if worker:
            worker.done.set();worker.thread.join(timeout=3)
        lock.close()
