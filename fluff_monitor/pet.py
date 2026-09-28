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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vendor/claude-pet"))
os.environ.setdefault("CODEX_ROUTING_PET_CONFIG", str(Path.home() / ".config/codex-routing-detector/pet"))

from claude_pet import config, sprites
from claude_pet.overlay import Overlay, PetView, Gdk, GLib, Gtk, Pango
from gi.repository import GdkPixbuf
from . import views
from .storage import atomic_json, read_json, state_dir, SnapshotReader
from .catalog import display_title, SnapshotCatalog
from .presentation import age_text, usage_text, route_status, menu_actions, model_text
from .pet_state import MismatchAlerts, MotionCycle
from .menu import MenuWindow, TitleReveal, menu_action, beside_position

CSS = b"""
#routing-popup { background: transparent; }
#routing-panel, #routing-actions { font-family: 'Pretendard Variable', 'Noto Sans CJK KR', Sans; background: transparent; }
#routing-card { background: #232427; border: 1px solid #3a3b3f; border-radius: 12px; padding: 10px 11px; }
#routing-panel label, #routing-actions label { color: #eeeeef; font-size: 13px; font-weight: 400; }
#routing-panel .title { font-size: 13px; font-weight: 600; }
#routing-panel .model { font-size: 13px; font-weight: 500; }
#routing-panel .muted, #routing-panel .chip { font-size: 12px; color: #a9aab1; }
#routing-panel .task-title { font-size: 12px; font-weight: 500; color: #c6c7ce; }
#routing-panel .provider-openai { color: #eeeeef; font-weight: 600; }
#routing-panel .provider-anthropic { color: #e8ab95; font-weight: 600; }
#routing-panel .neutral { color: #a9aab1; }
#routing-panel .good { color: #86c9a5; }
#routing-panel .bad { color: #f09b9f; }
#routing-panel .waiting { color: #e4c18b; }
#routing-panel .blue { color: #a4bbed; }
#routing-panel button, #routing-actions menuitem { border: none; border-radius: 6px; background-image: none;
  background: transparent; color: #c6c7ce; box-shadow: none; text-shadow: none; padding: 3px 5px; min-height: 18px; min-width: 18px; }
#routing-panel button:hover, #routing-actions menuitem:hover { background: #36373c; color: #ffffff; }
#routing-panel separator { background: #3a3b3f; min-height: 1px; margin: 7px 0; }
#routing-actions { background: #292a2e; border: 1px solid #43444a; border-radius: 10px; padding: 5px; }
#routing-actions menuitem { padding: 7px 10px; }
#routing-panel button.session-selector { padding: 0 8px; min-width: 0; min-height: 24px; }
#routing-panel button.panel-menu { padding: 0; min-width: 24px; min-height: 24px; }
#routing-actions .muted { font-size: 11px; color: #a9aab1; }
#routing-actions separator { background: #43444a; min-height: 1px; margin: 4px 6px; }
#routing-panel.light #routing-card { background: #fafafa; border-color: #d8d9de; }
#routing-panel.light label { color: #24252a; }
#routing-panel.light .muted, #routing-panel.light .chip, #routing-panel.light .neutral { color: #686b75; }
#routing-panel.light .task-title { color: #50535c; }
#routing-panel.light .provider-openai { color: #24252a; }
#routing-panel.light .provider-anthropic { color: #98492e; }
#routing-panel.light .good { color: #21734e; }
#routing-panel.light .bad { color: #b44653; }
#routing-panel.light .waiting { color: #886020; }
#routing-panel.light .blue { color: #486798; }
#routing-panel.light button { color: #72757c; }
#routing-panel.light button:hover { background: #eeeeef; color: #24252a; }
#routing-panel.light separator { background: #dedee2; }
#routing-actions.light { background: #fafafa; border-color: #d8d9de; }
#routing-actions.light label { color: #24252a; }
#routing-actions.light .muted { color: #71747d; }
#routing-actions.light menuitem:hover { background: #eeeeef; }
#routing-actions button { border: none; border-radius: 6px; background-image: none;
  background: transparent; color: #c6c7ce; box-shadow: none; text-shadow: none;
  padding: 7px 10px; min-height: 18px; min-width: 18px; }
#routing-actions button:hover, #routing-actions button:checked:hover { background: #36373c; }
#routing-actions button:checked { background: transparent; }
#routing-actions button.action { padding-left: 34px; }
#routing-actions.pet-context button.action { padding-left: 12px; padding-right: 12px; }
#routing-actions.menu-window separator { margin: 3px 6px; }
#routing-actions .selection-dot { font-size: 13px; color: #c6c7ce; }
#routing-actions.light button:hover, #routing-actions.light button:checked:hover { background: #eeeeef; }
#routing-title-reveal { background: transparent; }
#routing-title-strip { background: #36373c; border-radius: 6px; padding: 0; }
#routing-title-strip label { font-family: 'Pretendard Variable', 'Noto Sans CJK KR', Sans;
  font-size: 12px; font-weight: 500; color: #c6c7ce; }
#routing-title-strip image { color: #a9aab1; }
#routing-title-reveal.light #routing-title-strip { background: #eeeeef; }
#routing-title-reveal.light #routing-title-strip label { color: #50535c; }
"""


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
        self.set_title("Fluff · 모델명 불일치 알림")
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
        self.title_timer = None
        self.set_title("Fluff · 라우팅 모니터")
        self.set_wmclass("codex-routing-panel", "Codex-routing-pet")
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
        card.set_size_request(288, -1)
        self.set_default_size(288, 1)
        self.add(card)

        def leading(caption, style="muted", icon=None, caption_width=54):
            # The same 16px icon slot + 54px label column is used on every row.
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            slot = Gtk.Box()
            slot.set_size_request(16, -1)
            if icon is not None:
                slot.set_halign(Gtk.Align.START)
                slot.pack_start(icon, True, True, 0)
            box.pack_start(slot, False, False, 0)
            caption.set_size_request(caption_width, -1)
            caption.set_max_width_chars(1)
            caption.get_style_context().add_class(style)
            box.pack_start(caption, False, False, 0)
            return box

        def table_rows(values):
            grid = Gtk.Grid(column_spacing=6, row_spacing=3)
            grid.set_hexpand(True)
            for index, (name, value) in enumerate(values):
                caption = leading(label(name))
                caption.set_valign(Gtk.Align.BASELINE)
                value.set_valign(Gtk.Align.BASELINE)
                value.set_hexpand(True)
                value.set_max_width_chars(1)
                grid.set_row_baseline_position(index, Gtk.BaselinePosition.CENTER)
                grid.attach(caption, 0, index, 1, 1)
                grid.attach(value, 1, index, 1, 1)
            return grid

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

        self.gpt_mark, self.claude_mark = Gtk.Image(), Gtk.Image()
        self.gpt_mark.get_accessible().set_name("OpenAI")
        self.claude_mark.get_accessible().set_name("Anthropic")
        # The control begins 8px before the shared text column; its padding
        # brings the title back into alignment with model and usage values.
        self.gpt_brand = leading(label("GPT"), "provider-openai", self.gpt_mark, caption_width=46)
        self.claude_brand = leading(label("Claude"), "provider-anthropic", self.claude_mark, caption_width=46)
        gpt_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        card.pack_start(gpt_section, False, False, 0)
        self.session_button = Gtk.ToggleButton()
        self.session_button.get_style_context().add_class("session-selector")
        self.session_button.get_accessible().set_name("표시할 Codex 작업 선택")
        self.session_button.connect("enter-notify-event", lambda b, e: self._title_hover(b, True, e))
        self.session_button.connect("leave-notify-event", lambda b, e: self._title_hover(b, False, e))
        self.session_title = label("작업 선택", "task-title")
        self.session_title.set_max_width_chars(1)
        arrow = Gtk.Image.new_from_icon_name("pan-down-symbolic", Gtk.IconSize.MENU)
        arrow.set_pixel_size(10)
        self.session_button.add(row(self.session_title, arrow, spacing=4))
        self.session_menu = MenuWindow(self)
        self.session_menu.items.set_size_request(250, -1)
        source_drag = Gtk.EventBox()
        source_drag.set_visible_window(False)
        source_drag.add(self.gpt_brand)
        source_drag.connect("button-press-event", self._drag)
        self.menu_button = Gtk.ToggleButton(label="⋯")
        self.menu_button.get_style_context().add_class("panel-menu")
        self.menu_button.set_tooltip_text("정보창 테마와 고정")
        self.menu_button.get_accessible().set_name("라우팅 모니터 메뉴")
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.pack_start(source_drag, False, False, 0)
        header.pack_start(self.session_button, True, True, 0)
        header.pack_start(self.menu_button, False, False, 0)
        gpt_section.pack_start(header, False, False, 0)
        self.requested, self.served = label("—", "model"), label("—", "model")
        gpt_section.pack_start(table_rows((("요청", self.requested), ("응답", self.served))), False, False, 0)
        self.chip, self.cache = label("대기", "chip"), label("", "muted")
        self.gpt_dot = state_dot()
        self.chip.status_marker = self.gpt_dot
        self.cache.set_max_width_chars(1)
        footer = row(leading(self.chip, "chip", self.gpt_dot), self.cache, spacing=6)
        footer.set_child_packing(footer.get_children()[0], False, False, 0, Gtk.PackType.START)
        footer.set_child_packing(self.cache, True, True, 0, Gtk.PackType.START)
        gpt_section.pack_start(footer, False, False, 0)
        self.history = label("", "muted")
        gpt_section.pack_start(self.history, False, False, 0)
        card.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)
        claude_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        card.pack_start(claude_section, False, False, 0)
        self.claude = label("로그 연결 전", "chip")
        self.claude_button = Gtk.ToggleButton()
        self.claude_button.get_style_context().add_class("session-selector")
        self.claude_title = label("작업 선택", "task-title")
        self.claude_title.set_max_width_chars(1)
        arrow = Gtk.Image.new_from_icon_name("pan-down-symbolic", Gtk.IconSize.MENU)
        arrow.set_pixel_size(10)
        self.claude_button.add(row(self.claude_title, arrow, spacing=4))
        self.claude_button.get_accessible().set_name("표시할 Claude 작업 선택")
        self.claude_button.connect("enter-notify-event", lambda b, e: self._title_hover(b, True, e))
        self.claude_button.connect("leave-notify-event", lambda b, e: self._title_hover(b, False, e))
        self.claude_menu = MenuWindow(self)
        self.claude_menu.items.set_size_request(250, -1)
        self.claude_button.connect("toggled", lambda b: self._toggle_menu(b, self.claude_menu))
        self.claude_menu.connect("hide", lambda *_: self._menu_closed(self.claude_button))
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.pack_start(self.claude_brand, False, False, 0)
        header.pack_start(self.claude_button, True, True, 0)
        spacer = Gtk.Box()
        spacer.set_size_request(24, -1)
        self.header_action_sizes = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        self.header_action_sizes.add_widget(self.menu_button)
        self.header_action_sizes.add_widget(spacer)
        header.pack_start(spacer, False, False, 0)
        claude_section.pack_start(header, False, False, 0)
        self.claude_requested, self.claude_served = label("—", "model"), label("—", "model")
        self.claude_models = table_rows((("설정", self.claude_requested), ("응답", self.claude_served)))
        claude_section.pack_start(self.claude_models, False, False, 0)
        self.claude_cache = label("", "muted")
        self.claude_cache.set_max_width_chars(1)
        self.claude_dot = state_dot()
        self.claude.status_marker = self.claude_dot
        footer = row(leading(self.claude, "chip", self.claude_dot), self.claude_cache, spacing=6)
        footer.set_child_packing(footer.get_children()[0], False, False, 0, Gtk.PackType.START)
        footer.set_child_packing(self.claude_cache, True, True, 0, Gtk.PackType.START)
        claude_section.pack_start(footer, False, False, 0)
        self.action_menu = MenuWindow(self)
        self.action_menu.items.set_size_request(182, -1)
        callbacks = {"theme": self.toggle_theme,
                     "pin": lambda: (self.toggle_pin(), self.close_menu())}
        buttons = {}
        for key, title in menu_actions("panel", theme=self.settings.get("theme", "dark"), pinned=self.pinned):
            button = menu_action(title)
            button.connect("clicked", lambda _, callback=callbacks[key]: callback())
            self.action_menu.append(button)
            buttons[key] = button
        self.theme_button, self.pin_button = buttons["theme"], buttons["pin"]
        self.action_menu.items.show_all()
        self.title_reveal = TitleReveal(self, self._schedule_title_reveal)
        self.connect("hide", lambda *_: self.title_reveal.hide())
        self.session_button.connect("toggled", lambda b: self._toggle_menu(b, self.session_menu))
        self.menu_button.connect("toggled", lambda b: self._toggle_menu(b, self.action_menu, True))
        self.apply_theme()
        self.connect("delete-event", lambda *_: self.pet.quit() if self.pet else False)
        self.connect("button-release-event", self._save_position)
        self.connect("size-allocate", self._align_after_resize)
        self.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.connect("enter-notify-event", lambda _, e: self.crossing("panel", True, e))
        self.connect("leave-notify-event", lambda _, e: self.crossing("panel", False, e))
        self.action_menu.connect("hide", lambda *_: self._menu_closed(self.menu_button))
        self.session_menu.connect("hide", lambda *_: self._menu_closed(self.session_button))
        self.show_all()
        self.hide()

    def _toggle_menu(self, button, menu, align_right=False):
        if button.get_active():
            self.title_reveal.hide()
            pet_menu = getattr(self.pet, "local_menu", None)
            if pet_menu:
                pet_menu.popdown()
            for other in (self.action_menu, self.session_menu, self.claude_menu):
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
        if any(b.get_active() for b in (self.session_button, self.claude_button)):
            self.title_reveal.hide()
            return False
        button = next(iter(self.title_hovered), None)
        if button:
            title = self.session_title if button is self.session_button else self.claude_title
            self.title_reveal.reveal(button, title.get_text())
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
        self.resize(288, 1)
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
        if not self.pinned and not self.hovered and not any(b.get_active() for b in (self.menu_button, self.session_button, self.claude_button)):
            self.hide()
        return False

    def toggle_pin(self):
        self.pinned = not self.pinned
        self.pin_button.set_label("정보창 고정 해제" if self.pinned else "정보창 고정")
        self.menu_button.set_tooltip_text("정보창 고정됨 · 다시 펫을 클릭하면 해제" if self.pinned else "정보창 테마와 고정")
        if self.pinned:
            self.reveal()
        else:
            self.schedule_hide()

    def apply_theme(self):
        light = self.settings.get("theme") == "light"
        context = self.get_style_context()
        context.add_class("light") if light else context.remove_class("light")
        for menu in (self.action_menu, self.session_menu, self.claude_menu):
            popup = menu.items.get_style_context()
            popup.add_class("light") if light else popup.remove_class("light")
        self.theme_button.set_label("어두운 화면" if light else "밝은 화면")
        provider_mark(self.gpt_mark, "openai", light)
        provider_mark(self.claude_mark, "anthropic", light)

    def toggle_theme(self):
        self.settings["theme"] = "dark" if self.settings.get("theme") == "light" else "light"
        self.apply_theme()
        self._save_position()
        self.close_menu()

    def close_menu(self):
        self.action_menu.popdown()
        self.menu_button.set_active(False)

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
            detail=model_text(item.get("requested")) or "요청 대기"
            if item.get("effort"):detail+=" · "+item["effort"]
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
        for menu in (self.action_menu, self.session_menu, self.claude_menu):
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
        text, details = usage_text(usage, available)
        widget.set_text(text)
        widget.set_tooltip_text(details)
        widget.get_accessible().set_description(details)

    def update(self, route, claude):
        self.update_sessions(route)
        self.update_claude_sessions(claude)
        self.update_usage(self.cache, route.get("usage"), route.get("metrics_available", False))
        self.cache.set_visible("metrics_available" in route)
        children = route.get("children") or {}
        children_detail = "\n".join(f"{item['name']} · {item['state']}" for item in children.get("details", []))
        self.claude_models.set_visible(bool(claude.get("session_id")))
        request = model_text(claude.get("requested_model")) or "확인 안 됨"
        if claude.get("requested_effort"):
            request += " · " + claude["requested_effort"]
        self.claude_requested.set_text(request)
        self.claude_requested.set_tooltip_text("현재 CLI의 시작 모델·추론 설정입니다. 실행 중 설정을 바꾸면 실제 응답 로그의 모델과 다를 수 있습니다.")
        self.claude_served.set_text(model_text(claude.get("served")) or "기록 대기")
        self.claude_served.set_tooltip_text("Claude의 응답 로그에 기록된 모델명입니다.")
        self.claude_title.set_text(claude.get("title") or "작업 선택")
        self.claude_button.get_accessible().set_description(claude.get("title") or "현재 작업 없음")
        self.update_usage(self.claude_cache, claude.get("usage"))
        verdict = route.get("verdict")
        text, tone = route_status(route)
        at = route.get("observed_at")
        if verdict == "PENDING":
            text = "응답 중" if route.get("status") == "in_progress" else "응답 대기"
            if at:
                text += " · " + elapsed_text(route.get("request_started_at") or at)
        elif verdict in ("ok", "OK", "REROUTED", "ERROR") and at:
            text += " · " + age_text(at)
        self._chip(self.chip, text.split(" · ", 1)[0], tone)
        self.chip.set_tooltip_text("서버 응답이 진행 중입니다. 완료되면 모델명 일치 여부를 표시합니다."
                                  if verdict == "PENDING" else "서버 응답의 모델명 " + text)
        if verdict == "OBSERVATION_GAP":
            self.chip.set_tooltip_text(route.get("detail"))
        if children.get("running"):
            self.chip.set_tooltip_text(self.chip.get_tooltip_text() + "\n" + children_detail)
        requested = model_text(route.get("requested")) or "—"
        if route.get("effort"):
            requested += " · " + str(route["effort"])
        self.requested.set_text(requested)
        self.requested.set_tooltip_text(requested)
        self.served.set_text(model_text(route.get("served")) or ("모델명 미수집" if verdict == "OBSERVATION_GAP" else "—"))
        self.served.set_tooltip_text(route.get("served") or "응답 모델명을 아직 확인하지 못했습니다")
        stamp = time.strftime("%H:%M:%S", time.localtime(at)) if at else ""
        receiving = route.get("active") and route.get("connected") and not route.get("capture_lost")
        source_text = "실시간 연결" if receiving else route["label"]
        coverage = route.get("detail") or "Codex 앱 응답 메타데이터 · 별도 검사 요청 없음"
        timing = ("모델 정보 수신: " + stamp + " · " + age_text(at)) if at else "모델 정보 수신 전"
        timing += "\n연결 확인: " + age_text(route.get("updated_at"))
        self.gpt_brand.set_tooltip_text("OpenAI · " + source_text + "\n" + coverage + "\n" + timing)
        mismatches = route.get("historical_mismatches", 0)
        self.history.set_text(f"이 연결에서 모델명 불일치 {mismatches}건" if mismatches else "")
        history_changed = self.history.get_visible() != bool(mismatches)
        self.history.set_visible(bool(mismatches))
        if history_changed:
            self.resize(288, 1)
        activity_text = claude["label"] + " · " + age_text(claude.get("observed_at"))
        self._chip(self.claude, claude["label"], "blue" if claude["state"] == "running" else "neutral")
        self.claude.set_tooltip_text("Claude 로컬 로그 · " + activity_text)
        self.claude_brand.set_tooltip_text("Anthropic · Claude Code\n" + self.claude_requested.get_tooltip_text())

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
        self.set_title("Fluff")
        self.set_wmclass("codex-routing-fluff", "Codex-routing-pet")

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
        for other in (self.panel.action_menu, self.panel.session_menu, self.panel.claude_menu):
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
    ap = argparse.ArgumentParser(description="Fluff with routing status on hover or click")
    ap.add_argument("--pet-dir", type=Path, default=Path.home() / ".codex/pets/fluff")
    ap.add_argument("--test-seconds", type=float)
    ap.add_argument("--screenshot", type=Path)
    args = ap.parse_args(argv)
    directory = state_dir()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = (directory / "pet.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("Fluff is already running")
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
    print(f"Fluff ready: {view.pet.frame_counts}", flush=True)
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
