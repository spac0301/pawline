"""Qt frontend for Windows; the metadata, usage and placement policies are shared.

The Linux frontend remains GTK. QT_QPA_PLATFORM=offscreen can exercise this
frontend on Linux without controlling the user's desktop or claiming a Windows run.
"""
from __future__ import annotations

import argparse
import math
import os
import math
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

from PySide6.QtCore import Qt, QTimer, Signal, QPoint, QRect, QPropertyAnimation
from PySide6.QtGui import QFont, QFontDatabase, QFontMetricsF, QImage, QPainter, QPixmap, QColor, QIcon, QLinearGradient
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QWidget, QFrame, QLabel, QPushButton, QHBoxLayout, QVBoxLayout, QSizePolicy

from .activity import ActivityCollector
from .catalog import SnapshotCatalog
from .layout import beside_position
from .platform_support import lock_exclusive
from .presentation import usage_text, route_status, menu_actions, model_text, gpt_request
from .presentation import CARD_WIDTH, CARD_PADDING, ROW_HEIGHT, CONTROL_HEIGHT, PROVIDER_HEIGHT
from .presentation import CONTENT_INSET, FIELD_WIDTH, FIELD_GAP, STATUS_WIDTH
from .presentation import progress_status, observation_status, selection_text, usage_parts, cache_breakdown, FONT_SIZES, THEMES, toolbar_icon
from .pet_state import MismatchAlerts
from .storage import SnapshotReader, atomic_json, read_json, state_dir
from .views import desktop_view, claude_view
from .identity import APP_NAME, DESKTOP_ID, ICON, identify_windows_app, set_process_name

ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT / "vendor/claude-pet"))
from claude_pet.sprites import load_pet

PET_HEIGHT, GAP, INSET = 120, 12, CONTENT_INSET



def layout(widget, kind, margins=(0, 0, 0, 0), spacing=0):
    result = kind(widget)
    result.setContentsMargins(*margins)
    result.setSpacing(spacing)
    return result


def apply_font_axes(root):
    """Select Pretendard's real weight, not just its nominal QFont weight.

    https://doc.qt.io/qt-6/qfont.html#setVariableAxis
    The bundled variable font otherwise renders its Regular outline on the
    tested Qt backend even when QFontInfo reports the requested weight.
    """
    tag = QFont.Tag.fromString("wght")
    for widget in root.findChildren(QLabel):
        widget.ensurePolished()
        font = widget.font()
        font.setVariableAxis(tag, float(font.weight()))
        widget.setFont(font)


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
        painter.setFont(self.font())
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.drawText(self.rect(), self.alignment(),
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


class ControlMark(QWidget):
    def __init__(self, name, parent=None, size=18):
        super().__init__(parent)
        self.name, self.active, self.ink = name, False, THEMES["dark"]["muted"]
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, event):
        painter = QPainter(self)
        QSvgRenderer(toolbar_icon(self.name, self.ink, self.active)).render(painter)


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
        self.arrow = ControlMark("chevron-down", size=12)
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
        apply_font_axes(self)
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
        target = min(420, target, area.width)
        pos.setX(max(area.x, min(pos.x(), area.x+area.width-target)))
        px, py, pw, ph = self.owner.pet_rect()
        height = anchor.height() if is_title else 24
        if pos.y() < py+ph and pos.y()+height > py and pos.x() < px+pw and pos.x()+target > px:
            if px-GAP-target >= area.x:
                pos.setX(px-GAP-target)
            elif px+pw+GAP+target <= area.x+area.width:
                pos.setX(px+pw+GAP)
            else:
                self.hide()
                return
        if target <= initial:
            self.hide()
            return
        height = anchor.height() if is_title else 24
        self.button.setFixedHeight(height)
        start = QRect(pos.x(), pos.y(), initial, height)
        self.setGeometry(start)
        self.show()
        self.animation.stop()
        self.animation.setStartValue(start)
        self.animation.setEndValue(QRect(pos.x(), pos.y(), target, height))
        self.animation.start()


class ProviderSection(QWidget):
    def __init__(self, provider, owner):
        super().__init__()
        self.provider, self.owner = provider, owner
        box = layout(self, QVBoxLayout, spacing=4)
        self.setFixedHeight(PROVIDER_HEIGHT)
        header = QWidget()
        header.setFixedHeight(ROW_HEIGHT)
        header_row = layout(header, QHBoxLayout, (INSET, 0, INSET, 0), 6)
        brand = QWidget()
        brand.setFixedWidth(68)
        brand_row = layout(brand, QHBoxLayout, spacing=6)
        self.mark = Mark(provider)
        self.brand = Text("GPT" if provider == "gpt" else "Claude")
        self.brand.setObjectName(provider+"Brand")
        brand_row.addWidget(self.mark)
        brand_row.addWidget(self.brand, 1)
        self.mode = Text("자동 추적")
        self.mode.setObjectName("muted")
        self.mode.setFixedWidth(64)
        self.progress = Text("작업 없음")
        self.progress.setObjectName("muted")
        self.progress.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        header_row.addWidget(brand)
        header_row.addWidget(self.mode)
        header_row.addWidget(self.progress, 1)
        box.addWidget(header)
        self.title = TitleButton()
        self.title.setFixedHeight(CONTROL_HEIGHT)
        self.title.clicked.connect(lambda: owner.choose_provider(provider))
        self.title.hovered.connect(lambda entered: owner.hover_title(self.title, entered))
        box.addWidget(self.title)
        self.served = self.add_row(box, "응답 모델", CONTROL_HEIGHT)
        self.served.setObjectName("responseModel")
        self.requested = self.add_row(box, "요청" if provider == "gpt" else "설정", ROW_HEIGHT)
        self.requested.setObjectName("muted")
        self.requested.parentWidget().hide()
        self.status, self.cache = Text("모델 대기"), Text("")
        self.status.setObjectName("muted")
        self.cache.setObjectName("muted")
        self.status_button = QPushButton()
        self.status_button.setObjectName("statusButton")
        self.status_button.setFixedSize(STATUS_WIDTH, CONTROL_HEIGHT)
        status_row = layout(self.status_button, QHBoxLayout, (INSET, 0, INSET, 0), 4)
        self.dot = StatusDot()
        status_row.addWidget(self.dot)
        status_row.addWidget(self.status, 1)
        status_arrow = ControlMark("chevron-right", size=12)
        status_row.addWidget(status_arrow)
        self.status_button.clicked.connect(lambda: owner.show_details(self.status_button, self.brand.text()+" · 모델 관측", self.status.toolTip()))
        self.served.parentWidget().layout().addWidget(self.status_button)
        self.cache_button = QPushButton()
        self.cache_button.setObjectName("cacheButton")
        self.cache_button.setFixedHeight(CONTROL_HEIGHT)
        cache_row = layout(self.cache_button, QHBoxLayout, (INSET, 0, INSET, 0), FIELD_GAP)
        self.cache.setFixedWidth(FIELD_WIDTH)
        self.cache_metric, self.cache_age = Text(""), Text("")
        self.cache_metric.setObjectName("cacheValue")
        self.cache_age.setObjectName("muted")
        self.cache_age.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        cache_row.addWidget(self.cache)
        cache_row.addWidget(self.cache_metric)
        cache_row.addWidget(self.cache_age, 1)
        arrow = ControlMark("chevron-right", size=12)
        cache_row.addWidget(arrow)
        self.cache_button.clicked.connect(lambda: owner.show_details(self.cache_button, self.brand.text()+" · 캐시 적중률", self.cache.toolTip(), cache=True))
        box.addWidget(self.cache_button)

    @staticmethod
    def add_row(box, name, height):
        widget = QWidget()
        widget.setFixedHeight(height)
        row = layout(widget, QHBoxLayout, (INSET, 0, 0, 0), FIELD_GAP)
        caption = Text(name)
        caption.setObjectName("muted")
        caption.setFixedWidth(FIELD_WIDTH)
        value = Text("—")
        value.caption_label = caption
        row.addWidget(caption)
        row.addWidget(value, 1)
        box.addWidget(widget)
        return value

    def update_data(self, value):
        self.cache_button.cache_record = value.get("usage")
        self.cache_button.cache_available = value.get("metrics_available", True)
        self.title.set_title(value.get("title") or "현재 작업 없음")
        key = "codex_thread" if self.provider == "gpt" else "claude_session"
        self.mode.setText(selection_text(self.owner.settings.get(key)))
        self.mode.setToolTip("메뉴에서 고른 작업을 표시합니다." if self.owner.settings.get(key) else "최근 활동이 있는 작업을 자동으로 표시합니다.")
        if self.provider == "gpt":
            caption, requested, effort, detail = gpt_request(value)
            self.requested.caption_label.setText(caption)
            self.requested.setToolTip(detail)
            served = value.get("served") or ("모델명 미수집" if value.get("verdict")=="OBSERVATION_GAP" else "—")
        else:
            requested, effort = value.get("requested_model"), value.get("requested_effort")
            served = value.get("served") or "기록 대기"
            self.requested.setToolTip("CLI를 시작할 때의 모델·추론 설정입니다. 실제 응답 모델과 다를 수 있습니다.")
        self.requested.setText((model_text(requested) or "—")+(" · "+effort if effort else ""))
        self.served.setText(model_text(served))
        self.served.setToolTip(model_text(served))
        status, tone, detail = observation_status(self.provider, value)
        if self.provider == "gpt" and value.get("historical_mismatches"):
            detail += f"\n관측된 모델명 불일치: {value['historical_mismatches']}건"
        self.status.setText(status)
        self.status.setToolTip(detail)
        theme = THEMES.get(self.owner.settings.get("theme"), THEMES["dark"])
        colors = dict(neutral=theme["muted"], blue=theme["accent"], waiting=theme["warning"], bad=theme["danger"])
        self.status.setStyleSheet("color: "+colors[tone])
        self.dot.color = colors[tone]
        self.dot.update()
        text, progress_tone = progress_status(self.provider, value)
        self.progress.setText(text)
        self.progress.setStyleSheet("color: "+colors[progress_tone])
        self.progress.setToolTip("작업의 진행 상태입니다. 응답 모델 확인 여부는 아래에 따로 표시합니다.")
        caption, metric, age, detail = usage_parts(value.get("usage"), value.get("metrics_available", True))
        self.cache.setText(caption)
        self.cache_metric.setText(metric)
        self.cache_age.setText(age)
        self.cache_metric.ensurePolished()
        # Integer advance can round down and elide the final percent glyph.
        metric_width = QFontMetricsF(self.cache_metric.font()).horizontalAdvance(self.cache_metric.text())
        self.cache_metric.setFixedWidth(math.ceil(metric_width)+1)
        self.cache.setToolTip(detail)
        provider = self.brand.text()
        self.cache_button.setAccessibleName(provider+" 입력 캐시 · "+usage_text(value.get("usage"), value.get("metrics_available", True))[0])
        self.cache_button.setAccessibleDescription(detail)
        self.status_button.setAccessibleName(provider+" 모델 관측 · "+status)
        self.status_button.setAccessibleDescription(self.status.toolTip())


class CacheBar(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner, self.fraction = owner, 0
        self.setFixedHeight(8)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        colors = THEMES.get(self.owner.settings.get("theme"), THEMES["dark"])
        painter.setBrush(QColor(colors["edge"]))
        painter.drawRoundedRect(self.rect(), 4, 4)
        painter.setClipRect(0, 0, round(self.width()*self.fraction), self.height())
        gradient = QLinearGradient(0,0,max(self.width()*self.fraction,1),0)
        gradient.setColorAt(0,QColor(colors["accent_start"]))
        gradient.setColorAt(1,QColor(colors["accent_end"]))
        painter.setBrush(gradient)
        painter.drawRoundedRect(self.rect(), 4, 4)


class CacheDetails(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.data = {"known": False}
        box = layout(self, QVBoxLayout, spacing=8)
        self.hero = QWidget()
        top = layout(self.hero, QHBoxLayout, spacing=8)
        self.number, self.age = QLabel(), QLabel()
        self.number.setObjectName("cacheNumber")
        self.age.setObjectName("muted")
        top.addWidget(self.number, 1)
        top.addWidget(self.age)
        self.bar = CacheBar(owner)
        self.legend = QWidget()
        row = layout(self.legend, QHBoxLayout, spacing=6)
        self.cached, self.other = QLabel(), QLabel()
        self.cached_dot, self.other_dot = StatusDot(), StatusDot()
        for widget in (self.cached, self.other):widget.setObjectName("muted")
        row.addWidget(self.cached_dot);row.addWidget(self.cached)
        row.addStretch(1)
        row.addWidget(self.other_dot);row.addWidget(self.other)
        self.empty = QLabel()
        self.empty.setObjectName("muted")
        self.empty.setTextFormat(Qt.TextFormat.PlainText)
        self.parts = (self.hero, self.bar, self.legend)
        for widget in (*self.parts, self.empty):box.addWidget(widget)

    def update_data(self, usage, available=True):
        self.data = cache_breakdown(usage, available)
        for widget in self.parts:widget.setVisible(self.data["known"])
        self.empty.setVisible(not self.data["known"])
        if self.data["known"]:
            self.number.setText(self.data["percent"])
            self.age.setText(self.data["age"])
            self.cached.setText(f"적중 {self.data['cached']:,}")
            self.other.setText(f"미적중 {self.data['other']:,}")
            self.cached.setToolTip(f"캐시에서 읽은 입력 {self.data['cached']:,} 토큰")
            self.other.setToolTip(f"캐시에서 읽지 않은 입력 {self.data['other']:,} 토큰")
            colors = THEMES.get(self.owner.settings.get("theme"),THEMES["dark"])
            self.cached_dot.color, self.other_dot.color = colors["accent"], colors["muted"]
            self.cached_dot.update();self.other_dot.update()
            self.bar.fraction = self.data["fraction"]
            self.setAccessibleName(f"입력 토큰 {self.data['total']:,}개 중 {self.data['percent']} 재사용")
        else:
            self.empty.setText(self.data["message"])
        self.bar.update()


class DetailPopup(ChoicePopup):
    """Keyboard-accessible details using the same native dismissal as menus."""
    def __init__(self, owner):
        super().__init__(owner)
        self.setFixedWidth(CARD_WIDTH)
        self.box.setContentsMargins(12, 12, 12, 12)
        self.box.setSpacing(8)
        self.heading = QLabel()
        self.heading.setTextFormat(Qt.TextFormat.PlainText)
        self.heading.setObjectName("detailHeading")
        self.body = QLabel()
        self.body.setTextFormat(Qt.TextFormat.PlainText)
        self.body.setWordWrap(True)
        self.body.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.body.setObjectName("muted")
        self.cache_graph = CacheDetails(owner)
        self.cache_mode = False
        self.cache_graph.hide()
        self.box.addWidget(self.heading)
        self.box.addWidget(self.body)
        self.box.addWidget(self.cache_graph)
        self.anchor = None

    def open_at(self, anchor, beside=False):
        # Size the wrapped body at the actual card width, not QLabel's preferred
        # line width. Otherwise adjustSize can crop the first and last lines.
        self.ensurePolished()
        margins = self.box.contentsMargins()
        width = self.width()-2*self.frameWidth()-margins.left()-margins.right()
        content = self.cache_graph if self.cache_mode else self.body
        # Release the previous minimum before asking for the new content height.
        # A short model detail must not inherit the preceding cache detail's size.
        content.setMinimumHeight(0)
        content.setMaximumHeight(16777215)
        content.setFixedWidth(width)
        if self.cache_mode:
            for widget in self.cache_graph.findChildren(QWidget):widget.ensurePolished()
            self.cache_graph.layout().invalidate()
            height = self.cache_graph.layout().sizeHint().height()
        else:
            height = self.body.heightForWidth(width)
        content.setFixedHeight(height)
        self.heading.setFixedHeight(self.heading.sizeHint().height())
        self.setFixedHeight(margins.top()+margins.bottom()+2*self.frameWidth()
                            +self.heading.height()+self.box.spacing()+height)
        super().open_at(anchor, beside)

    def display(self, anchor, title, text, cache=False):
        if self.isVisible() and self.anchor is anchor:
            self.hide()
            return
        self.anchor = anchor
        self.cache_mode = cache
        self.body.setVisible(not cache)
        self.cache_graph.setVisible(cache)
        if cache:
            self.cache_graph.update_data(getattr(anchor, "cache_record", None), getattr(anchor, "cache_available", True))
        self.heading.setText(title)
        self.body.setText(text)
        self.setAccessibleName(title+" 상세")
        self.open_at(anchor)


class Panel(Surface):
    def __init__(self, directory, settings, save):
        super().__init__(None)
        self.setWindowTitle("Pawline · 작업 정보")
        self.owner = self
        self.directory, self.settings, self.save = directory, settings, save
        self.pet = None
        self.pinned = False
        self.route, self.claude = {}, {}
        self.setObjectName("panel")
        self.setFixedWidth(CARD_WIDTH)
        self.box = layout(self, QVBoxLayout, (CARD_PADDING, CARD_PADDING, CARD_PADDING, CARD_PADDING))
        toolbar = QWidget()
        toolbar.setFixedHeight(CONTROL_HEIGHT)
        top = layout(toolbar, QHBoxLayout, (INSET, 0, 0, 0), 4)
        self.app_name = Text("Pawline")
        self.app_name.setObjectName("muted")
        self.theme_button = QPushButton()
        self.theme_button.setFixedSize(CONTROL_HEIGHT, CONTROL_HEIGHT)
        self.theme_mark = ControlMark("sun")
        layout(self.theme_button, QHBoxLayout).addWidget(self.theme_mark, 0, Qt.AlignmentFlag.AlignCenter)
        self.theme_button.clicked.connect(self.toggle_theme)
        self.pin_toggle = QPushButton()
        self.pin_mark = ControlMark("pin")
        layout(self.pin_toggle, QHBoxLayout).addWidget(self.pin_mark, 0, Qt.AlignmentFlag.AlignCenter)
        self.pin_toggle.setObjectName("pinToggle")
        self.pin_toggle.setCheckable(True)
        self.pin_toggle.setFixedSize(CONTROL_HEIGHT, CONTROL_HEIGHT)
        self.pin_toggle.setAccessibleName("창 고정")
        self.pin_toggle.clicked.connect(self.toggle_pin)
        top.addWidget(self.app_name, 1)
        top.addWidget(self.pin_toggle)
        top.addWidget(self.theme_button)
        self.box.addWidget(toolbar)
        self.box.addSpacing(6)
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
        self.detail_popup = DetailPopup(self)
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
        css = (f'QWidget {{ font-family: "Pretendard Variable"; font-size: {FONT_SIZES["body"]}px; color: {c["foreground"]}; }}'
               f'#panel, #popup {{ background: {c["background"]}; border: 1px solid {c["edge"]}; border-radius: 12px; }}'
               f'#divider {{ background: {c["edge"]}; }}'
               f'#muted {{ font-size: {FONT_SIZES["secondary"]}px; color: {c["muted"]}; }}'
               f'#claudeBrand, #gptBrand {{ font-size: {FONT_SIZES["body"]}px; font-weight: 500; color: {c["secondary"]}; }}'
               'QPushButton { border: none; background: transparent; border-radius: 6px; padding: 0; }'
               f'QPushButton:hover, #titlePopup {{ background: {c["hover"]}; border-radius: 6px; }}'
               f'QPushButton:focus {{ border: 1px solid {c["muted"]}; }}'
               f'#titleButton QLabel {{ font-size: {FONT_SIZES["heading"]}px; font-weight: 600; color: {c["foreground"]}; }}'
               f'#responseModel {{ font-size: {FONT_SIZES["body"]}px; font-weight: 500; color: {c["secondary"]}; }}'
               f'#cacheValue {{ font-size: {FONT_SIZES["body"]}px; font-weight: 600; }}'
               f'#cacheNumber {{ font-size: {FONT_SIZES["metric"]}px; font-weight: 600; }}'
               f'#pinToggle:checked {{ background: {c["selected"]}; }}'
               f'#cacheButton {{ background: {c["inset"]}; border-radius: 6px; }}'
               f'#cacheButton:hover {{ background: {c["selected"]}; }}'
               '#detailHeading { font-weight: 600; }')
        for widget in (self, self.popup, self.title_popup, self.detail_popup):
            widget.setStyleSheet(css)
            apply_font_axes(widget)
            for mark in widget.findChildren(ControlMark):
                mark.ink = c["muted"]
                mark.update()
        self.theme_mark.name = "moon" if self.settings.get("theme") == "light" else "sun"
        self.theme_mark.ink = c["muted"]
        self.theme_mark.update()
        action = "어두운 화면으로 전환" if self.settings.get("theme") == "light" else "밝은 화면으로 전환"
        self.theme_button.setToolTip(action)
        self.theme_button.setAccessibleName(action)
        self._sync_pin()
        self.gpt.mark.ink = c["foreground"]
        self.gpt.mark.update()
        if self.route or self.claude:
            self.update_data(self.route, self.claude)

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
        self._sync_pin()
        self.hide_timer.stop()
        self.place()
        self.show()

    def _sync_pin(self):
        self.pin_toggle.setChecked(self.pinned)
        colors = THEMES.get(self.settings.get("theme"), THEMES["dark"])
        self.pin_mark.active = self.pinned
        self.pin_mark.ink = colors["foreground"] if self.pinned else colors["muted"]
        self.pin_mark.update()
        self.pin_toggle.setToolTip("창 고정 해제" if self.pinned else "창 고정")
        self.pin_toggle.setAccessibleDescription("고정됨" if self.pinned else "고정되지 않음")

    def toggle_theme(self):
        self.popup.hide()
        self.detail_popup.hide()
        self.title_popup.hide()
        self.settings["theme"] = "light" if self.settings.get("theme", "dark") == "dark" else "dark"
        self.apply_theme()
        self.save()

    def toggle_pin(self):
        self.pinned = not self.pinned
        self._sync_pin()
        if self.pinned:
            self.reveal()
        else:
            self.hide_timer.start(420)

    def enterEvent(self, event):
        self.hide_timer.stop()

    def leaveEvent(self, event):
        self.hide_timer.start(420)

    def _hide_if_outside(self):
        if not self.pinned and not self.underMouse() and not self.title_hover and not self.popup.isVisible() and not self.detail_popup.isVisible() and not (self.pet and self.pet.underMouse()):
            self.hide()
            self.title_popup.hide()

    def hover_title(self, anchor, entered):
        self.title_anchor = anchor if entered else None
        self.title_timer.start(180)

    def _show_title(self):
        if self.popup.isVisible() or self.detail_popup.isVisible():
            return
        if self.title_anchor:
            self.title_popup.reveal(self.title_anchor)
        elif not self.title_hover:
            self.title_popup.hide()

    def choose_provider(self, provider):
        self.detail_popup.hide()
        value = self.route if provider == "gpt" else self.claude
        config_key = "codex_thread" if provider == "gpt" else "claude_session"
        entries = [(s["thread_id"] if provider=="gpt" else s.get("selection_key", s.get("session_id")), s.get("title") or "이름 없는 작업") for s in value.get("sessions", [])]
        def choose(key):
            if key:self.settings[config_key] = key
            else:self.settings.pop(config_key, None)
            self.save()
        self.popup.setFixedWidth(276)
        self.popup.populate(entries, self.settings.get(config_key), choose)
        self.popup.open_at(self.gpt.title if provider=="gpt" else self.anthropic.title)

    def actions(self, surface="pet"):
        self.detail_popup.hide()
        def choose(key):
            if key=="walk":self.pet.walking = not self.pet.walking
            elif key=="pet":self.pet.gesture = ("waving", time.monotonic()+2)
            elif key=="quit":QApplication.quit()
        actions = menu_actions(surface, walking=bool(self.pet and self.pet.walking))
        self.popup.hide()
        self.popup.setFixedWidth(164)
        self.popup.populate(actions,None,choose,auto=False)
        self.popup.open_at(self, beside=True)

    def show_details(self, anchor, title, text, cache=False):
        self.popup.hide()
        self.title_popup.hide()
        self.detail_popup.display(anchor, title, text, cache)

    def update_data(self, route, claude):
        self._sync_pin()
        self.route, self.claude = route, claude
        self.gpt.update_data(route)
        self.anthropic.update_data(claude)
        if self.detail_popup.isVisible() and self.detail_popup.anchor:
            self.detail_popup.body.setText(self.detail_popup.anchor.accessibleDescription())
            if self.detail_popup.cache_mode:
                anchor = self.detail_popup.anchor
                self.detail_popup.cache_graph.update_data(anchor.cache_record, anchor.cache_available)
            self.detail_popup.open_at(self.detail_popup.anchor)
        if self.title_popup.isVisible() and isinstance(self.title_popup.anchor, Text):
            self.title_popup.reveal(self.title_popup.anchor)


class PetWindow(QWidget):
    def __init__(self, pet_dir, panel):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle(APP_NAME)
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
    set_process_name()
    identify_windows_app()
    app = QApplication.instance() or QApplication([APP_NAME])
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setDesktopFileName(DESKTOP_ID)
    app.setWindowIcon(QIcon(str(ICON)))
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
