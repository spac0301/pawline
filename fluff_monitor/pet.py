"""Fluff desktop companion. GTK rendering, gestures and motion reuse claude-pet.

Routing and usage are projections of the shared passive snapshots. Redrawing the
pet never starts a model request or reads a second task database.
"""
from __future__ import annotations

import argparse
import fcntl
import os
import signal
import sys
import time
from pathlib import Path
from collections import deque
from string import Template

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vendor/claude-pet"))
os.environ.setdefault("CODEX_ROUTING_PET_CONFIG", str(Path.home() / ".config/codex-routing-detector/pet"))

from claude_pet import config, sprites
from claude_pet.overlay import Overlay, PetView, Gdk, GLib, Gtk, Pango
from gi.repository import GdkPixbuf
from . import views
from .storage import atomic_json, read_json, state_dir, SnapshotReader
from .catalog import display_title, SnapshotCatalog
from .presentation import age_text, usage_text, route_status, menu_actions, model_text, gpt_request
from .presentation import CARD_WIDTH, CARD_PADDING, ROW_HEIGHT, CONTROL_HEIGHT
from .presentation import CONTENT_INSET, FIELD_WIDTH, FIELD_GAP, STATUS_WIDTH
from .presentation import progress_status, observation_status, selection_text, usage_parts, cache_breakdown, FONT_SIZES, THEMES, toolbar_icon
from .pet_state import MismatchAlerts, MotionCycle
from .menu import MenuWindow, TitleReveal, menu_action, beside_position
from .identity import APP_NAME, ICON, set_process_name

CSS = Template("""
#routing-popup { background: transparent; }
#routing-panel, #routing-actions { font-family: 'Pretendard Variable', 'Noto Sans CJK KR', Sans; background: transparent; }
#routing-card { background: $dark_background; border: 1px solid $dark_edge; border-radius: 12px; padding: 12px; }
#routing-panel label, #routing-actions label { color: $dark_foreground; font-size: ${body}px; font-weight: 400; }
#routing-panel .title { font-size: ${body}px; font-weight: 600; }
#routing-panel .model { font-size: ${body}px; font-weight: 500; color: $dark_secondary; }
#routing-panel .requested, #routing-panel .muted, #routing-panel .chip { font-size: ${secondary}px; color: $dark_muted; }
#routing-panel .task-title { font-size: ${heading}px; font-weight: 600; }
#routing-panel .cache-value { font-size: ${body}px; font-weight: 600; }
#routing-panel .provider-openai, #routing-panel .provider-anthropic { font-size: ${body}px; font-weight: 500; color: $dark_secondary; }
#routing-panel .neutral { color: $dark_muted; }
#routing-panel .good { color: $dark_positive; }
#routing-panel .bad { color: $dark_danger; }
#routing-panel .waiting { color: $dark_warning; }
#routing-panel .blue { color: $dark_accent; }
#routing-panel button, #routing-actions menuitem { border: none; border-radius: 6px; background-image: none;
  background: transparent; color: $dark_foreground; box-shadow: none; text-shadow: none; padding: 3px 5px; min-height: 18px; min-width: 18px; }
#routing-panel button:hover, #routing-actions menuitem:hover { background: $dark_hover; }
#routing-panel separator { background: $dark_edge; min-height: 1px; margin: 7px 0; }
#routing-actions { background: $dark_background; border: 1px solid $dark_edge; border-radius: 12px; padding: 5px; }
#routing-actions menuitem { padding: 7px 10px; }
#routing-panel button.session-selector { padding: 0 8px; min-width: 0; min-height: 28px; }
#routing-panel button.cache-control { background: $dark_inset; padding: 0 8px; min-height: 28px; min-width: 0; }
#routing-panel.light button.cache-control { background: $light_inset; }
#routing-panel button.cache-control:hover { background: $dark_hover; }
#routing-panel.light button.cache-control:hover { background: $light_selected; }
#routing-panel button.status-control { padding: 0 8px; min-height: 28px; min-width: 0; }
#routing-panel button.pin-toggle { padding: 0; min-height: 28px; min-width: 0; }
#routing-panel button.pin-toggle:checked { background: $dark_selected; }
#routing-panel.light button.pin-toggle:checked { background: $light_selected; }
#routing-panel button:focus { outline: 1px solid $dark_muted; outline-offset: -1px; }
#routing-actions.detail-menu { padding: 12px; }
#routing-actions.detail-menu .detail-heading { font-size: ${body}px; font-weight: 600; margin-bottom: 8px; }
#routing-actions.detail-menu .cache-number { font-size: ${metric}px; font-weight: 600; }
#routing-panel button.toolbar-control { padding: 0; min-width: 28px; min-height: 28px; }
#routing-actions .muted { font-size: ${secondary}px; color: $dark_muted; }
#routing-actions separator { background: $dark_edge; min-height: 1px; margin: 4px 6px; }
#routing-panel.light #routing-card { background: $light_background; border-color: $light_edge; }
#routing-panel.light label { color: $light_foreground; }
#routing-panel.light .model, #routing-panel.light .provider-openai, #routing-panel.light .provider-anthropic { color: $light_secondary; }
#routing-panel.light .muted, #routing-panel.light .chip, #routing-panel.light .neutral, #routing-panel.light .requested { color: $light_muted; }
#routing-panel.light .good { color: $light_positive; }
#routing-panel.light .bad { color: $light_danger; }
#routing-panel.light .waiting { color: $light_warning; }
#routing-panel.light .blue { color: $light_accent; }
#routing-panel.light button { color: $light_foreground; }
#routing-panel.light button:hover { background: $light_hover; }
#routing-panel.light separator { background: $light_edge; }
#routing-actions.light { background: $light_background; border-color: $light_edge; }
#routing-actions.light label { color: $light_foreground; }
#routing-actions.light .muted { color: $light_muted; }
#routing-actions.light menuitem:hover { background: $light_hover; }
#routing-actions button { border: none; border-radius: 6px; background-image: none;
  background: transparent; color: $dark_foreground; box-shadow: none; text-shadow: none;
  padding: 7px 10px; min-height: 18px; min-width: 18px; }
#routing-actions button:hover, #routing-actions button:checked:hover { background: $dark_hover; }
#routing-actions button:checked { background: transparent; }
#routing-actions button.action { padding-left: 34px; }
#routing-actions.pet-context button.action { padding-left: 12px; padding-right: 12px; }
#routing-actions.menu-window separator { margin: 3px 6px; }
#routing-actions .selection-dot { font-size: ${body}px; color: $dark_foreground; }
#routing-actions.light button:hover, #routing-actions.light button:checked:hover { background: $light_hover; }
#routing-title-reveal { background: transparent; }
#routing-title-strip { background: $dark_hover; border-radius: 6px; padding: 0; }
#routing-title-strip label { font-family: 'Pretendard Variable', 'Noto Sans CJK KR', Sans;
  font-size: ${heading}px; font-weight: 600; color: $dark_foreground; }
#routing-title-strip image { color: $dark_muted; }
#routing-title-reveal.light #routing-title-strip { background: $light_hover; }
#routing-title-reveal.light #routing-title-strip label { color: $light_foreground; }
""").substitute(FONT_SIZES, **{theme+"_"+key:value for theme, colors in THEMES.items()
                              for key, value in colors.items()}).encode()



def load_app_font():
    """Register the bundled OFL font for this process only; no system font changes."""
    import ctypes
    import ctypes.util
    library = ctypes.util.find_library("fontconfig")
    font = ROOT / "assets/fonts/PretendardVariable.ttf"
    if not library or not font.exists():
        return False
    fc = ctypes.CDLL(library)
    fc.FcConfigGetCurrent.restype = ctypes.c_void_p
    fc.FcConfigAppFontAddFile.argtypes = (ctypes.c_void_p, ctypes.c_char_p)
    fc.FcConfigAppFontAddFile.restype = ctypes.c_int
    return bool(fc.FcConfigAppFontAddFile(fc.FcConfigGetCurrent(), os.fsencode(font)))


def elapsed_text(at):
    seconds = max(0, int(time.time() - at))
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}분 {seconds:02d}초" if minutes else f"{seconds}초"


def label(text="", style=None):
    item = Gtk.Label(label=text, xalign=0)
    item.set_single_line_mode(True)
    item.set_line_wrap(False)
    item.set_ellipsize(Pango.EllipsizeMode.END)
    item.set_max_width_chars(28)
    if style:
        item.get_style_context().add_class(style)
    return item


def row(*widgets, spacing=8):
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=spacing)
    for index, widget in enumerate(widgets):
        if isinstance(widget, Gtk.Label):
            widget.set_valign(Gtk.Align.BASELINE)
        box.pack_start(widget, index == 0, index == 0, 0)
    return box


def provider_mark(image, provider, light=False):
    """Use the official vector geometry; only the monochrome OpenAI ink follows theme."""
    filename = "openai.svg" if provider == "openai" else "claude-spark.svg"
    svg = (ROOT / "assets/providers" / filename).read_text()
    svg = svg.replace("currentColor", "#24252a" if light else "#eeeeef")
    loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
    loader.set_size(16, 16)
    loader.write(svg.encode())
    loader.close()
    image.set_from_pixbuf(loader.get_pixbuf())


def control_mark(image, name, color, active=False, size=18):
    svg = toolbar_icon(name, color, active)
    if getattr(image, "control_svg", None) == (svg, size):
        return
    loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
    loader.set_size(size, size)
    loader.write(svg);loader.close()
    image.set_from_pixbuf(loader.get_pixbuf())
    image.control_svg = svg, size


def notice_content():
    """The approved 256 × 88 layout, shared by live notifications and preview renders."""
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
    card.set_name("routing-card")
    card.set_size_request(256, 88)
    close = Gtk.Button()
    close.set_focus_on_click(False)
    close.get_accessible().set_name("알림 닫기")
    icon = Gtk.Image.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU)
    icon.set_pixel_size(10)
    close.add(icon)
    card.pack_start(row(label("응답 모델명 불일치", "title"), close), False, False, 0)
    task, models = label(style="muted"), label(style="model")
    card.pack_start(task, False, False, 0)
    card.pack_start(models, False, False, 0)
    return card, close, task, models


def update_notice(task, models, notice, *, light=False):
    task.set_text(display_title(notice.get("title")) or "이름 없는 작업")
    task.set_tooltip_text(task.get_text())
    if notice.get("detailed"):
        requested = GLib.markup_escape_text(str(notice["requested"]))
        served = GLib.markup_escape_text(str(notice["served"]))
        effort = GLib.markup_escape_text(str(notice.get("effort") or ""))
        middle = (" · "+effort if effort else "")+"  →  "
        muted, bad = ("#71747d", "#b44653") if light else ("#a9aab1", "#f09b9f")
        models.set_markup(requested+f'<span font="Pretendard Variable 11px" foreground="{muted}">'
                          +middle+f'</span><span foreground="{bad}">'+served+'</span>')
        models.set_tooltip_text(f"요청: {notice['requested']}"+(" · "+notice['effort'] if notice.get('effort') else "")
                                +f"\n응답: {notice['served']}")
    else:
        models.set_text(f"최근 응답에서 불일치 {notice['count']}건")
        models.set_tooltip_text("다음 요청이 먼저 표시되어, 불일치한 응답의 모델명은 확인하지 못했습니다.")


class RoutingNotice(Gtk.Window):
    def __init__(self, pet):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.pet, self.pending, self.current = pet, deque(), None
        self.deadline, self.hovered = 0.0, False
        self.light = None
        self.set_name("routing-panel")
        self.set_title("Pawline · 모델명 불일치 알림")
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_default_size(256, 88)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(False)
        self.set_focus_on_map(False)
        self.stick()
        self.set_transient_for(pet)
        self.set_destroy_with_parent(True)
        self.set_app_paintable(True)
        visual = self.get_screen().get_rgba_visual()
        if visual:
            self.set_visual(visual)
        card, close, self.task, self.models = notice_content()
        self.add(card)
        close.connect("clicked", lambda *_: self.dismiss())
        self.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.connect("enter-notify-event", lambda _, e: self._hover(True, e))
        self.connect("leave-notify-event", lambda _, e: self._hover(False, e))

    def _hover(self, entered, event=None):
        if event is not None and event.detail == Gdk.NotifyType.INFERIOR:
            return False
        self.hovered = entered
        if not entered:
            self.deadline = max(self.deadline, time.monotonic()+2)
        return False

    def enqueue(self, notice):
        if self.current and self.current["thread_id"] == notice["thread_id"]:
            self.current = notice
            update_notice(self.task, self.models, notice, light=self.light)
            self.deadline = time.monotonic()+8
            return
        self.pending = deque(n for n in self.pending if n["thread_id"] != notice["thread_id"])
        self.pending.append(notice)
        if self.current is None:
            self._next()

    def _next(self):
        if not self.pending:
            return
        self.current = self.pending.popleft()
        self.apply_theme()
        update_notice(self.task, self.models, self.current, light=self.light)
        self.deadline = time.monotonic()+8
        self.show_all()
        self.place()

    def dismiss(self):
        self.hide()
        self.current, self.hovered = None, False
        self._next()

    def place(self):
        if not self.get_visible():
            return
        pet, size = self.pet, self.get_size()
        area = pet._workarea()
        x = pet.sprite_x-size.width-14
        if x<area.x:
            x = pet.sprite_x+pet.view.width+14
        y = pet.sprite_y+(pet.view.height-size.height)//2
        panel = pet.panel
        if panel.get_visible():
            px, py = panel.get_position(); ps = panel.get_size()
            if x<px+ps.width and x+size.width>px and y<py+ps.height and y+size.height>py:
                y = py-size.height-8 if py-size.height-8>=area.y else py+ps.height+8
        self.move(max(area.x,min(int(x),area.x+area.width-size.width)),
                  max(area.y,min(int(y),area.y+area.height-size.height)))

    def tick(self):
        self.apply_theme()
        if self.current and not self.hovered and time.monotonic()>=self.deadline:
            self.dismiss()
        self.place()

    def apply_theme(self):
        light = self.pet.panel.settings.get("theme") == "light"
        if light == self.light:
            return
        self.light = light
        context = self.get_style_context()
        context.add_class("light") if light else context.remove_class("light")
        if self.current:
            update_notice(self.task, self.models, self.current, light=light)


class CacheDetails(Gtk.Box):
    """A two-part graph of the last input, with no unrelated output metrics."""
    def __init__(self, owner):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.owner, self.data = owner, {"known": False}
        self.number = label("", "cache-number")
        self.age = label("", "muted")
        self.age.set_halign(Gtk.Align.END)
        self.hero = row(self.number, self.age, spacing=8)
        self.bar = Gtk.DrawingArea()
        self.bar.set_size_request(-1, 8)
        self.bar.connect("draw", self._draw)
        self.cached, self.other = label("", "muted"), label("", "muted")
        self.other.set_halign(Gtk.Align.END)
        def legend_item(text, role):
            dot = Gtk.DrawingArea()
            dot.set_size_request(5, 5);dot.set_valign(Gtk.Align.CENTER)
            def draw(widget, cr):
                colors = THEMES.get(self.owner.settings.get("theme"), THEMES["dark"])
                rgb = [int(colors[role][i:i+2],16)/255 for i in (1,3,5)]
                cr.set_source_rgb(*rgb);cr.arc(2.5,2.5,2.5,0,6.2831853072);cr.fill()
            dot.connect("draw", draw)
            group = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            group.pack_start(dot,False,False,0);group.pack_start(text,False,False,0)
            return group
        self.legend = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.legend.pack_start(legend_item(self.cached,"accent"),False,False,0)
        self.legend.pack_end(legend_item(self.other,"muted"),False,False,0)
        self.empty = label("입력 기록을 기다리고 있어요.", "muted")
        self.parts = (self.hero, self.bar, self.legend)
        for widget in (*self.parts, self.empty):
            self.pack_start(widget, False, False, 0)
        self.show_all()

    def update_data(self, usage, available=True):
        self.data = cache_breakdown(usage, available)
        for widget in self.parts:
            widget.set_visible(self.data["known"])
        self.empty.set_visible(not self.data["known"])
        if self.data["known"]:
            self.number.set_text(self.data["percent"])
            self.age.set_text(self.data["age"])
            self.cached.set_text(f"적중 {self.data['cached']:,}")
            self.other.set_text(f"미적중 {self.data['other']:,}")
            self.cached.set_tooltip_text(f"캐시에서 읽은 입력 {self.data['cached']:,} 토큰")
            self.other.set_tooltip_text(f"캐시에서 읽지 않은 입력 {self.data['other']:,} 토큰")
            self.get_accessible().set_name(f"입력 토큰 {self.data['total']:,}개 중 {self.data['percent']} 재사용")
        else:
            self.empty.set_text(self.data["message"])
        self.bar.queue_draw()

    def _draw(self, widget, cr):
        if not self.data["known"]:
            return False
        width, height = widget.get_allocated_width(), widget.get_allocated_height()
        radius = height/2
        cr.arc(radius, radius, radius, 1.5707963268, 4.7123889804)
        cr.arc(width-radius, radius, radius, 4.7123889804, 7.8539816340)
        cr.close_path();cr.clip()
        colors = THEMES.get(self.owner.settings.get("theme"), THEMES["dark"])
        rgb = [int(colors["edge"][i:i+2],16)/255 for i in (1,3,5)]
        cr.set_source_rgb(*rgb);cr.paint()
        import cairo
        filled = width*self.data["fraction"]
        gradient = cairo.LinearGradient(0,0,max(filled,1),0)
        for stop, role in ((0,"accent_start"),(1,"accent_end")):
            rgb = [int(colors[role][i:i+2],16)/255 for i in (1,3,5)]
            gradient.add_color_stop_rgb(stop,*rgb)
        cr.set_source(gradient);cr.rectangle(0,0,filled,height);cr.fill()
        return False


class Panel(Gtk.Window):
    def __init__(self, settings):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.settings = settings
        self.pet = None
        self.pinned = False
        self.hovered = set()
        self.show_timer = self.hide_timer = None
        self.session_signature = None
        self.claude_signature = None
        self.title_hovered = set()
        self.reveal_labels = {}
        self.title_timer = None
        self.set_title("Pawline · 작업 정보")
        self.set_wmclass("pawline", "Pawline")
        self.set_name("routing-panel")
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(True)
        self.set_focus_on_map(False)
        self.stick()
        self.set_app_paintable(True)
        visual = self.get_screen().get_rgba_visual()
        if visual:
            self.set_visual(visual)
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(self.get_screen(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        card.set_name("routing-card")
        card.set_size_request(CARD_WIDTH, -1)
        self.set_default_size(CARD_WIDTH, 1)
        self.add(card)

        self.detail_controls = []
        self.detail_control = None
        self.provider_context = {}
        self.chevrons = []
        self.theme_button = Gtk.Button()
        self.theme_button.get_style_context().add_class("toolbar-control")
        self.theme_button.set_size_request(CONTROL_HEIGHT, CONTROL_HEIGHT)
        self.theme_image = Gtk.Image()
        self.theme_button.add(self.theme_image)
        self.theme_button.connect("clicked", lambda *_: self.toggle_theme())
        self.app_name = label("Pawline", "muted")
        source_drag = Gtk.EventBox()
        source_drag.set_margin_start(CONTENT_INSET)
        source_drag.set_visible_window(False)
        source_drag.add(self.app_name)
        source_drag.connect("button-press-event", self._drag)
        self.pin_toggle = Gtk.ToggleButton()
        self.pin_image = Gtk.Image()
        self.pin_toggle.add(self.pin_image)
        self.pin_toggle.get_style_context().add_class("pin-toggle")
        self.pin_toggle.set_size_request(CONTROL_HEIGHT, CONTROL_HEIGHT)
        self.pin_toggle.get_accessible().set_name("창 고정")
        self.pin_toggle.connect("clicked", lambda *_: self.toggle_pin() if not getattr(self, "_syncing_pin", False) else None)
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        toolbar.set_size_request(-1, CONTROL_HEIGHT)
        toolbar.set_margin_bottom(6)
        toolbar.pack_start(source_drag, True, True, 0)
        toolbar.pack_end(self.theme_button, False, False, 0)
        toolbar.pack_end(self.pin_toggle, False, False, 0)
        card.pack_start(toolbar, False, False, 0)

        def state_dot():
            dot = Gtk.DrawingArea()
            dot.set_size_request(5, 5)
            dot.set_halign(Gtk.Align.CENTER)
            dot.set_valign(Gtk.Align.CENTER)
            def draw(widget, cr):
                color = widget.get_style_context().get_color(Gtk.StateFlags.NORMAL)
                cr.set_source_rgba(color.red, color.green, color.blue, color.alpha)
                cr.arc(2.5, 2.5, 2.5, 0, 6.283185307179586)
                cr.fill()
                return False
            dot.connect("draw", draw)
            return dot

        def section(provider):
            name = "GPT" if provider == "gpt" else "Claude"
            section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            header.set_margin_start(CONTENT_INSET)
            header.set_margin_end(CONTENT_INSET)
            header.set_size_request(-1, ROW_HEIGHT)
            mark = Gtk.Image()
            mark.get_accessible().set_name("OpenAI" if provider == "gpt" else "Anthropic")
            brand = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            brand.pack_start(mark, False, False, 0)
            brand.pack_start(label(name, "provider-openai" if provider == "gpt" else "provider-anthropic"), True, True, 0)
            brand.set_size_request(68, -1)
            mode = label("자동 추적", "muted")
            progress = label("작업 없음", "chip")
            progress.set_halign(Gtk.Align.END)
            self.provider_context[provider] = (mode, progress)
            header.pack_start(brand, False, False, 0)
            header.pack_start(mode, False, False, 0)
            header.pack_end(progress, True, True, 0)
            section.pack_start(header, False, False, 0)
            button = Gtk.ToggleButton()
            button.get_style_context().add_class("session-selector")
            button.get_accessible().set_name("표시할 "+name+" 작업 선택")
            title = label("작업 선택", "task-title")
            title.set_max_width_chars(1)
            arrow = Gtk.Image()
            self.chevrons.append((arrow, "chevron-down"))
            button.add(row(title, arrow, spacing=4))
            button.connect("enter-notify-event", lambda w, e: self._title_hover(w, True, e))
            button.connect("leave-notify-event", lambda w, e: self._title_hover(w, False, e))
            menu = MenuWindow(self)
            menu.items.set_size_request(276, -1)
            button.connect("toggled", lambda w: self._toggle_menu(w, menu))
            menu.connect("hide", lambda *_: self._menu_closed(button))
            section.pack_start(button, False, False, 0)
            requested, served = label("—", "requested"), label("—", "model")
            grid = Gtk.Grid(column_spacing=FIELD_GAP, row_spacing=4)
            grid.set_margin_start(CONTENT_INSET)
            for index, (caption, value) in enumerate((("응답 모델", served), ("요청" if provider == "gpt" else "설정", requested))):
                height = CONTROL_HEIGHT if index == 0 else ROW_HEIGHT
                value.caption_label = label(caption, "muted")
                value.caption_label.set_size_request(FIELD_WIDTH, height)
                value.caption_label.set_max_width_chars(1)
                value.set_size_request(-1, height)
                value.set_hexpand(True)
                value.set_max_width_chars(1)
                grid.attach(value.caption_label, 0, index, 1, 1)
                grid.attach(value, 1, index, 1 if index == 0 else 2, 1)
                if value is requested:
                    # The request remains available in the model detail, without
                    # repeating the same model in the compact overview.
                    value.set_no_show_all(True)
                    value.caption_label.set_no_show_all(True)
            status = label("대기", "chip")
            dot = state_dot()
            status.status_marker = dot
            status_button = Gtk.ToggleButton()
            status_button.get_style_context().add_class("status-control")
            status_button.set_size_request(STATUS_WIDTH, CONTROL_HEIGHT)
            status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            status_box.pack_start(dot, False, False, 0)
            status_box.pack_start(status, True, True, 0)
            status_arrow = Gtk.Image()
            self.chevrons.append((status_arrow, "chevron-right"))
            status_box.pack_end(status_arrow, False, False, 0)
            status_button.add(status_box)
            grid.attach(status_button, 2, 0, 1, 1)
            section.pack_start(grid, False, False, 0)
            cache = label("", "muted")
            cache.set_size_request(FIELD_WIDTH, -1)
            cache.set_max_width_chars(1)
            cache.metric = label("", "cache-value")
            cache.age = label("", "muted")
            cache_button = Gtk.ToggleButton()
            cache_button.get_style_context().add_class("cache-control")
            cache_button.set_size_request(-1, CONTROL_HEIGHT)
            cache_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=FIELD_GAP)
            cache_row.pack_start(cache, False, False, 0)
            cache_row.pack_start(cache.metric, False, False, 0)
            cache.age.set_halign(Gtk.Align.END)
            cache_row.pack_start(cache.age, True, True, 0)
            cache_arrow = Gtk.Image()
            self.chevrons.append((cache_arrow, "chevron-right"))
            cache_row.pack_end(cache_arrow, False, False, 0)
            cache_button.add(cache_row)
            section.pack_start(cache_button, False, False, 0)
            for control, text, heading in ((status_button, status, "모델 관측"), (cache_button, cache, "캐시 적중률")):
                control.get_accessible().set_name(name+" "+heading+" 상세")
                control.detail_title, control.detail_source = name+" · "+heading, text
                control.detail_kind = "cache" if control is cache_button else "model"
                text.detail_button = control
                control.connect("toggled", self._detail_toggled)
                self.detail_controls.append(control)
            card.pack_start(section, False, False, 0)
            return mark, brand, button, title, menu, requested, served, grid, status, dot, cache, status_button, cache_button

        (self.gpt_mark, self.gpt_brand, self.session_button, self.session_title, self.session_menu,
         self.requested, self.served, self.gpt_models, self.chip, self.gpt_dot, self.cache,
         self.gpt_status_button, self.gpt_cache_button) = section("gpt")
        self.history = label("", "muted")
        card.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)
        (self.claude_mark, self.claude_brand, self.claude_button, self.claude_title, self.claude_menu,
         self.claude_requested, self.claude_served, self.claude_models, self.claude, self.claude_dot,
         self.claude_cache, self.claude_status_button, self.claude_cache_button) = section("claude")
        self.detail_menu = MenuWindow(self)
        self.detail_menu.set_title("Pawline · 상세 정보")
        self.detail_menu.items.get_style_context().add_class("detail-menu")
        self.detail_menu.items.set_size_request(CARD_WIDTH, -1)
        self.detail_title = Gtk.Label(xalign=0)
        self.detail_title.get_style_context().add_class("detail-heading")
        self.detail_body = Gtk.Label(xalign=0, yalign=0)
        self.detail_body.set_line_wrap(True)
        self.detail_body.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.detail_body.set_max_width_chars(42)
        self.detail_body.get_style_context().add_class("muted")
        self.detail_body.set_no_show_all(True)
        self.cache_detail = CacheDetails(self)
        self.cache_detail.set_no_show_all(True)
        self.cache_detail.hide()
        self.detail_menu.append(self.detail_title)
        self.detail_menu.append(self.detail_body)
        self.detail_menu.append(self.cache_detail)
        self.detail_menu.connect("hide", self._detail_closed)
        self.title_reveal = TitleReveal(self, self._schedule_title_reveal)
        self.connect("hide", lambda *_: self.title_reveal.hide())
        self.apply_theme()
        self.connect("delete-event", lambda *_: self.pet.quit() if self.pet else False)
        self.connect("button-release-event", self._save_position)
        self.connect("size-allocate", self._align_after_resize)
        self.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.connect("enter-notify-event", lambda _, e: self.crossing("panel", True, e))
        self.connect("leave-notify-event", lambda _, e: self.crossing("panel", False, e))
        self.show_all()
        self.hide()

    def _menus(self):
        return self.session_menu, self.claude_menu, self.detail_menu

    def _detail_toggled(self, button):
        if button.get_active():
            previous = self.detail_control
            self.detail_control = button
            if previous is not None and previous is not button:
                previous.set_active(False)
            self.detail_title.set_text(button.detail_title)
            self._update_details(button)
            self._toggle_menu(button, self.detail_menu)
        elif self.detail_control is button:
            self.detail_menu.popdown()

    def _update_details(self, button):
        source = button.detail_source
        cache = button.detail_kind == "cache"
        self.detail_body.set_visible(not cache)
        self.cache_detail.set_visible(cache)
        if cache:
            self.cache_detail.update_data(getattr(source, "cache_record", None),
                                          getattr(source, "cache_available", True))
        else:
            self.detail_body.set_text(source.get_tooltip_text() or "새 기록을 기다립니다.")

    def _detail_closed(self, *_):
        button, self.detail_control = self.detail_control, None
        if button is not None:
            button.set_active(False)
        self.schedule_hide()

    def _toggle_menu(self, button, menu, align_right=False):
        if button.get_active():
            self.title_reveal.hide()
            pet_menu = getattr(self.pet, "local_menu", None)
            if pet_menu:
                pet_menu.popdown()
            for other in self._menus():
                if other is not menu:
                    other.popdown()
            menu.popup(button, align_right=align_right, within_owner=True,
                       avoid=((self.pet.sprite_x,self.pet.sprite_y,self.pet.view.width,self.pet.view.height),) if self.pet else ())
        else:
            menu.popdown()

    def _title_hover(self, button, entered, event=None):
        if event is not None and event.detail == Gdk.NotifyType.INFERIOR:
            return False
        if entered:
            self.title_hovered.add(button)
        else:
            self.title_hovered.discard(button)
        self._schedule_title_reveal()
        return False

    def _schedule_title_reveal(self):
        self._cancel_timer("title_timer")
        self.title_timer = GLib.timeout_add(180, self._expand_title)

    def _expand_title(self):
        self.title_timer = None
        if not self.get_visible():
            self.title_reveal.hide()
            return False
        if any(m.get_visible() for m in self._menus()):
            self.title_reveal.hide()
            return False
        button = next(iter(self.title_hovered), None)
        if button:
            status = self.reveal_labels.get(button)
            title = status if status is not None else (self.session_title if button is self.session_button else self.claude_title)
            self.title_reveal.reveal(button, title.get_text(), source=status)
        elif not self.title_reveal.hovered:
            self.title_reveal.hide()
        return False

    def _menu_closed(self, button):
        button.set_active(False)
        self._schedule_title_reveal()
        self.schedule_hide()

    def crossing(self, surface, entered, event=None):
        if event is not None and event.detail == Gdk.NotifyType.INFERIOR:
            return False
        if entered:
            self.hovered.add(surface)
            self._cancel_timer("hide_timer")
            if not self.get_visible() and self.show_timer is None:
                self.show_timer = GLib.timeout_add(180, self._show_after_hover)
        else:
            self.hovered.discard(surface)
            if not self.hovered:
                self._cancel_timer("show_timer")
                self.schedule_hide()
        return False

    def _cancel_timer(self, name):
        timer = getattr(self, name)
        if timer is not None:
            GLib.source_remove(timer)
            setattr(self, name, None)

    def _show_after_hover(self):
        self.show_timer = None
        if self.hovered:
            self.reveal()
        return False

    def reveal(self):
        self._cancel_timer("hide_timer")
        self.resize(CARD_WIDTH, 1)
        self.show()
        if self.pet:
            self.place_near(self.pet, restore=False)
            GLib.idle_add(lambda: self.place_near(self.pet, restore=False) or False)

    def schedule_hide(self):
        self._cancel_timer("hide_timer")
        if not self.pinned and not self.hovered:
            self.hide_timer = GLib.timeout_add(420, self._hide_after_leave)

    def _hide_after_leave(self):
        self.hide_timer = None
        if not self.pinned and not self.hovered and not any(m.get_visible() for m in self._menus()):
            self.hide()
        return False

    def toggle_pin(self):
        self.pinned = not self.pinned
        self._sync_pin()
        if self.pinned:
            self.reveal()
        else:
            self.schedule_hide()

    def _sync_pin(self):
        self._syncing_pin = True
        self.pin_toggle.set_active(self.pinned)
        colors = THEMES.get(self.settings.get("theme"), THEMES["dark"])
        control_mark(self.pin_image, "pin", colors["foreground"] if self.pinned else colors["muted"], self.pinned)
        self.pin_toggle.set_tooltip_text("창 고정 해제" if self.pinned else "창 고정")
        self.pin_toggle.get_accessible().set_description("고정됨" if self.pinned else "고정되지 않음")
        self._syncing_pin = False

    def apply_theme(self):
        light = self.settings.get("theme") == "light"
        context = self.get_style_context()
        context.add_class("light") if light else context.remove_class("light")
        for menu in self._menus():
            popup = menu.items.get_style_context()
            popup.add_class("light") if light else popup.remove_class("light")
        colors = THEMES["light" if light else "dark"]
        action = "어두운 화면으로 전환" if light else "밝은 화면으로 전환"
        control_mark(self.theme_image, "moon" if light else "sun", colors["muted"])
        for image, name in self.chevrons:
            control_mark(image, name, colors["muted"], size=12)
        self.theme_button.set_tooltip_text(action)
        self.theme_button.get_accessible().set_name(action)
        self._sync_pin()
        provider_mark(self.gpt_mark, "openai", light)
        provider_mark(self.claude_mark, "anthropic", light)

    def toggle_theme(self):
        self.settings["theme"] = "dark" if self.settings.get("theme") == "light" else "light"
        self.apply_theme()
        self._save_position()
        for menu in self._menus():
            menu.popdown()
        pet_menu = getattr(self.pet, "local_menu", None)
        if pet_menu:
            pet_menu.popdown()

    def select_session(self, thread_id):
        if thread_id:
            self.settings["codex_thread"] = thread_id
        else:
            self.settings.pop("codex_thread", None)
        self._save_position()
        self.session_menu.popdown()
        self.session_button.set_active(False)
        self.session_signature = None

    def update_sessions(self, route):
        selected=self.settings.get("codex_thread")
        title=display_title(route.get("title")) or ("이름 없는 작업" if route.get("thread_id") else "현재 작업 없음")
        self.session_title.set_text(title)
        self.session_button.get_accessible().set_description(title+" · "+("선택한 작업 고정" if selected else "최근 요청 자동 선택"))
        entries=[]
        for item in route.get("sessions") or []:
            _, model, effort, _ = gpt_request(item)
            detail=model_text(model) or "기록 대기"
            if effort:detail+=" · "+effort
            entries.append(dict(key=item["thread_id"],title=display_title(item.get("title")) or "이름 없는 작업",detail=detail))
        signature=(selected,tuple((x["key"],x["title"],x["detail"]) for x in entries))
        if signature!=self.session_signature:
            self.session_menu.set_choices(entries,selected,self.select_session)
            self.session_signature=signature

    def _drag(self, widget, event):
        if event.button == 1:
            self.title_hovered.clear()
            self.title_reveal.hide()
            self.begin_move_drag(1, int(event.x_root), int(event.y_root), event.time)
            return True
        return False

    def _save_position(self, *_):
        self.settings["panel_position"] = list(self.get_position())
        atomic_json(Path(os.environ["CODEX_ROUTING_PET_CONFIG"]) / "panel.json", self.settings)
        return False

    def place_near(self, pet, restore=True):
        area = pet._workarea()
        size = self.get_size()
        # Anchor to the fixed sprite canvas, not changing animation-frame bounds.
        point = beside_position((pet.sprite_x, pet.sprite_y, pet.view.width, pet.view.height), size, area)
        if point is None:
            return
        x, y = point
        stored = self.settings.get("panel_position") if restore else None
        if isinstance(stored, list) and len(stored) == 2:
            x, y = stored
        self.move(max(area.x, min(int(x), area.x + area.width - size.width)),
                  max(area.y, min(int(y), area.y + area.height - size.height)))

    def _align_after_resize(self, *_):
        if self.pet and self.get_visible():
            self.place_near(self.pet, restore=False)
        for menu in self._menus():
            if menu.get_visible():
                menu._place()

    @staticmethod
    def _chip(widget, text, tone):
        widget.set_text(text.removeprefix("● "))
        for target in (widget, getattr(widget, "status_marker", widget)):
            context = target.get_style_context()
            for name in ("neutral", "good", "bad", "waiting", "blue"):
                context.remove_class(name)
            context.add_class(tone)
            target.queue_draw()

    def update_claude_sessions(self, value):
        selected=self.settings.get("claude_session")
        entries=[]
        for item in value.get("sessions") or []:
            detail=model_text(item.get("requested_model")) or "담당 연결 대기"
            if item.get("requested_effort"):detail+=" · "+item["requested_effort"]
            entries.append(dict(key=item.get("selection_key",item.get("session_id")),
                                title=item.get("title") or "Claude 작업",detail=detail))
        signature=(selected,tuple((x["key"],x["title"],x["detail"]) for x in entries))
        if signature==self.claude_signature:return
        def choose(key):
            if key:self.settings["claude_session"]=key
            else:self.settings.pop("claude_session",None)
            self._save_position();self.claude_menu.popdown();self.claude_signature=None
        self.claude_menu.set_choices(entries,selected,choose)
        self.claude_signature=signature

    @staticmethod
    def update_usage(widget, usage, available=True):
        widget.cache_record, widget.cache_available = usage, available
        text, metric, age, details = usage_parts(usage, available)
        widget.set_text(text)
        widget.metric.set_text(metric)
        widget.age.set_text(age)
        widget.set_tooltip_text(details)
        widget.get_accessible().set_description(details)
        widget.set_has_tooltip(False)
        if hasattr(widget, "detail_button"):
            widget.detail_button.get_accessible().set_description(details)

    def update_provider_context(self, provider, value):
        mode, progress = self.provider_context[provider]
        selected = self.settings.get("codex_thread" if provider == "gpt" else "claude_session")
        mode.set_text(selection_text(selected))
        mode.set_tooltip_text("메뉴에서 고른 작업을 표시합니다." if selected else "최근 활동이 있는 작업을 자동으로 표시합니다.")
        self._chip(progress, *progress_status(provider, value))
        progress.set_tooltip_text("작업의 진행 상태입니다. 응답 모델 확인 여부는 아래에 따로 표시합니다.")

    def update(self, route, claude):
        self._sync_pin()
        self.update_provider_context("gpt", route)
        self.update_provider_context("claude", claude)
        self.update_sessions(route)
        self.update_claude_sessions(claude)
        self.update_usage(self.cache, route.get("usage"), route.get("metrics_available", False))
        self.cache.set_visible(True)
        children = route.get("children") or {}
        children_detail = "\n".join(f"{item['name']} · {item['state']}" for item in children.get("details", []))
        self.claude_models.set_visible(True)
        request = model_text(claude.get("requested_model")) or "확인 안 됨"
        if claude.get("requested_effort"):
            request += " · " + claude["requested_effort"]
        self.claude_requested.set_text(request)
        self.claude_requested.set_tooltip_text("현재 CLI의 시작 모델·추론 설정입니다. 실행 중 설정을 바꾸면 실제 응답 로그의 모델과 다를 수 있습니다.")
        self.claude_served.set_text(model_text(claude.get("served")) or "기록 대기")
        self.claude_served.set_tooltip_text("Claude의 응답 로그에 기록된 모델명입니다.")
        self.claude_title.set_text(claude.get("title") or "현재 작업 없음")
        self.claude_button.get_accessible().set_description(claude.get("title") or "현재 작업 없음")
        self.update_usage(self.claude_cache, claude.get("usage"))
        verdict = route.get("verdict")
        at = route.get("observed_at")
        text, tone, detail = observation_status("gpt", route)
        self._chip(self.chip, text, tone)
        if route.get("historical_mismatches"):
            detail += f"\n관측된 모델명 불일치: {route['historical_mismatches']}건"
        self.chip.set_tooltip_text(detail)
        caption, model, effort, request_detail = gpt_request(route)
        self.requested.caption_label.set_text(caption)
        requested = model_text(model) or "—"
        if effort:
            requested += " · " + str(effort)
        self.requested.set_text(requested)
        self.requested.set_tooltip_text(request_detail)
        self.served.set_text(model_text(route.get("served")) or ("모델명 미수집" if verdict == "OBSERVATION_GAP" else "—"))
        self.served.set_tooltip_text(route.get("served") or "응답 모델명을 아직 확인하지 못했습니다")
        stamp = time.strftime("%H:%M:%S", time.localtime(at)) if at else ""
        receiving = route.get("active") and route.get("connected") and not route.get("capture_lost")
        source_text = "실시간 연결" if receiving else route["label"]
        coverage = route.get("detail") or "Codex 앱 응답 메타데이터 · 별도 검사 요청 없음"
        timing = ("모델 정보 수신: " + stamp + " · " + age_text(at)) if at else "모델 정보 수신 전"
        timing += "\n연결 확인: " + age_text(route.get("updated_at"))
        self.gpt_brand.set_tooltip_text("OpenAI · " + source_text + "\n" + coverage + "\n" + timing)
        text, tone, detail = observation_status("claude", claude)
        self._chip(self.claude, text, tone)
        self.claude.set_tooltip_text(detail)
        self.claude_brand.set_tooltip_text("Anthropic · Claude Code\n" + self.claude_requested.get_tooltip_text())
        for status in (self.chip, self.claude):
            status.get_accessible().set_description(status.get_tooltip_text() or status.get_text())
            status.set_has_tooltip(False)
            status.detail_button.get_accessible().set_description(status.get_tooltip_text() or status.get_text())
        if self.detail_menu.get_visible() and self.detail_control is not None:
            self._update_details(self.detail_control)
            self.detail_menu.resize(1, 1)
            self.detail_menu._place()
        if self.title_reveal.get_visible():
            source = self.reveal_labels.get(self.title_reveal.anchor)
            if source is not None:
                self.title_reveal.reveal(self.title_reveal.anchor, source.get_text(), source=source)

class Fluff(Overlay):
    def __init__(self, view, settings, panel):
        self.panel = panel
        self.closed = False
        super().__init__(view, settings, poll=False)
        self.motion = MotionCycle({name:self._row_duration(name) for name in view.animations})
        self.notice = RoutingNotice(self)
        self.connect("enter-notify-event", lambda _, e: panel.crossing("pet", True, e))
        self.connect("leave-notify-event", lambda _, e: panel.crossing("pet", False, e))
        self.get_accessible().set_description("마우스를 올려 상태 보기 · 클릭해 고정 / 해제")

    def _configure_window(self):
        super()._configure_window()
        self.set_title(APP_NAME)
        self.set_wmclass("pawline", "Pawline")

    def _bubble_visible(self):
        return False  # status belongs to the fixed panel, not transient upstream phrases

    def _tick_body(self):
        if getattr(self, "local_menu", None) and self.local_menu.get_visible():
            self._halt_walk()  # Keep the menu's anchor still while it is being used.
        super()._tick_body()
        if self._ambient_motion():
            pose, resting = self.motion.sample(time.monotonic())
            if pose != self.visual_state:
                self.visual_state, self.frame_index = pose, 0
            if resting:
                self.frame_index = 0

    def _ambient_motion(self):
        return (self.visual_until is None and not self.walking and not self.dragging
                and self.press_origin is None and self.throw is None and self.walk_target is None)

    def _schedule_frame(self):
        if getattr(self, "motion", None) and self._ambient_motion():
            frames = max(1, len(self.view.frames(self.visual_state)))
            interval = self._row_duration(self.visual_state)/frames/MotionCycle.PLAYBACK_RATE
            GLib.timeout_add(max(20, round(interval*1000)), self._tick)
        else:
            super()._schedule_frame()

    def _on_click(self):
        self._react("waving")
        self.panel.toggle_pin()

    def _end_drag(self):
        super()._end_drag()
        self.panel.place_near(self, restore=False)

    def _show_menu(self, event):
        previous = getattr(self, "local_menu", None)
        if previous:
            previous.destroy()
        self._halt_walk()
        for other in self.panel._menus():
            other.popdown()
        menu = MenuWindow(self)
        menu.items.set_size_request(164, -1)
        menu.items.get_style_context().add_class("pet-context")
        if self.panel.settings.get("theme") == "light":
            menu.items.get_style_context().add_class("light")
        callbacks = {"pet": self._enjoy_petting, "walk": self.toggle_walk, "quit": self.quit}
        for key, text in menu_actions("pet", walking=self.settings["walk"]):
            if key == "quit":
                menu.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
            item = menu_action(text)
            item.connect("clicked", lambda _, c=callbacks[key]: (menu.popdown(), c()))
            menu.append(item)
        self.local_menu = menu
        bounds = (self.sprite_x, self.sprite_y, self.view.width, self.view.height)
        avoid = []
        if self.panel.get_visible():
            avoid.append((*self.panel.get_position(), *self.panel.get_size()))
        if not menu.popup(beside=bounds, avoid=avoid, workarea=self._workarea()) and avoid:
            # Only when the screen cannot fit all three surfaces: temporarily
            # fold the panel, keeping the pet and its controls accessible.
            self.panel.hide()
            menu.connect("hide", lambda *_: self.panel.reveal() if self.panel.pinned else None)
            menu.popup(beside=bounds, workarea=self._workarea())

    def toggle_walk(self):
        self.settings["walk"] = not self.settings["walk"]
        config.update(walk=self.settings["walk"])
        self._halt_walk()

    def quit(self, restart=False):
        if self.closed:
            return
        self.closed = True
        self.panel._cancel_timer("show_timer")
        self.panel._cancel_timer("hide_timer")
        self.panel._cancel_timer("title_timer")
        self.panel.title_reveal.hide()
        self.panel.destroy()
        self.destroy()
        if Gtk.main_level():
            Gtk.main_quit()


def capture_widgets(panel, pet, path):
    """Render our own GTK panel and current sprite for visual QA, not the user's desktop."""
    import cairo
    size = panel.get_size()
    height = max(size.height, pet.view.height)
    width = size.width + pet.view.width + 12
    if panel.title_reveal.get_visible():
        width = max(width, panel.title_reveal.get_position().root_x-panel.get_position().root_x+panel.title_reveal.get_size().width)
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    cr = cairo.Context(surface)
    cr.save()
    cr.translate(0, (height - size.height) // 2)
    panel.draw(cr)
    cr.restore()
    frames = pet.view.frames(pet.visual_state)
    Gdk.cairo_set_source_pixbuf(cr, frames[pet.frame_index % len(frames)], size.width + 12, (height - pet.view.height) // 2)
    cr.paint()
    reveal = panel.title_reveal
    if reveal.get_visible():
        px, py = panel.get_position()
        tx, ty = reveal.get_position()
        cr.save()
        cr.translate(tx-px, ty-py+(height-size.height)//2)
        reveal.draw(cr)
        cr.restore()
    surface.write_to_png(str(path))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Pawline — local agent status beside your pet")
    ap.add_argument("--pet-dir", type=Path, default=Path.home() / ".codex/pets/fluff")
    ap.add_argument("--test-seconds", type=float)
    ap.add_argument("--screenshot", type=Path)
    args = ap.parse_args(argv)
    set_process_name()
    GLib.set_prgname("pawline")
    GLib.set_application_name(APP_NAME)
    Gtk.Window.set_default_icon_from_file(str(ICON))
    directory = state_dir()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = (directory / "pet.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("Pawline is already running")
        return 0
    os.umask(0o077)
    load_app_font()
    settings = config.load()
    settings.update(language="ko", bubble="never", on_top=True, update_check=False, desktop=False,
                    usage=False, notifications=False, autostart=False, call=False, teleport=False,
                    throwing=False, exit_when_no_sessions=False)
    if not config.config_path().exists():
        settings["walk"] = False
    panel_settings = read_json(Path(os.environ["CODEX_ROUTING_PET_CONFIG"]) / "panel.json")
    view = PetView(sprites.load_pet(args.pet_dir), height=120)
    panel = Panel(panel_settings)
    pet = Fluff(view, settings, panel)
    panel.pet = pet
    panel.place_near(pet)
    catalog = SnapshotCatalog()
    snapshots = SnapshotReader()
    alerts = MismatchAlerts()

    def refresh():
        selected = panel.settings.get("codex_thread")
        activity = snapshots.read(directory / "activity.json")
        capture = snapshots.read(directory / "desktop.json")
        catalog.update(activity)
        route = views.desktop_view(thread_id=selected, catalog=catalog, activity=activity,capture=capture)
        changed_selection = False
        if route.get("selection_expired"):
            panel.settings.pop("codex_thread", None)
            route = views.desktop_view(catalog=catalog, activity=activity,capture=capture)
            changed_selection = True
        claude = views.claude_view(activity, panel.settings.get("claude_session"))
        if claude.get("selection_expired"):
            panel.settings.pop("claude_session", None)
            claude = views.claude_view(activity)
            changed_selection = True
        if changed_selection:
            atomic_json(Path(os.environ["CODEX_ROUTING_PET_CONFIG"]) / "panel.json", panel.settings)
        panel.update(route, claude)
        for notice in alerts.poll(route):
            pet.notice.enqueue(notice)
        pet.notice.tick()
        verdict = route.get("verdict")
        fresh = time.time() - (route.get("observed_at") or 0) < 60
        state = "running" if verdict == "PENDING" or route.get("activity") == "running" or claude["state"] == "running" else "idle"
        if fresh and verdict in ("ERROR", "REROUTED"):
            state = "failed"
        if state != pet.state:
            pet.state = state
            if pet.visual_until is not None:
                pet.visual_return = "idle"
        return not pet.closed

    refresh()
    GLib.timeout_add(500, refresh)
    if args.screenshot:
        panel.pinned = True
        panel.reveal()
        GLib.timeout_add(1500, lambda: capture_widgets(panel, pet, args.screenshot) or False)
    if args.test_seconds:
        GLib.timeout_add(int(args.test_seconds * 1000), lambda: pet.quit() or False)
    for sig in (signal.SIGINT, signal.SIGTERM):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, lambda: pet.quit() or False)
    print(f"Pawline ready: {view.pet.frame_counts}", flush=True)
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
