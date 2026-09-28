"""Compact managed popup windows that leave desktop shortcuts available."""
import ctypes
import ctypes.util
from functools import lru_cache
from .layout import beside_position
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Pango


@lru_cache(maxsize=1)
def _x11_library():
    library = ctypes.util.find_library("X11")
    if not library:
        return None
    x11 = ctypes.CDLL(library)
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XGetInputFocus.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong),
                                  ctypes.POINTER(ctypes.c_int)]
    x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    x11.XDefaultRootWindow.restype = ctypes.c_ulong
    x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    x11.XInternAtom.restype = ctypes.c_ulong
    x11.XGetWindowProperty.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong,
        ctypes.c_long, ctypes.c_long, ctypes.c_int, ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_void_p)]
    x11.XFree.argtypes = [ctypes.c_void_p]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    return x11


def retains_focus(window):
    """Keep logical X11 focus and the WM's active application distinct from a grab.

    GDK's focus-out event hides the distinction between NotifyGrab and a real
    focus transfer. Query through a separate read-only client; never grab input.
    https://www.x.org/releases/X11R7.5/doc/libX11/libX11.html#Focus_Events
    """
    if not hasattr(window, "get_xid"):
        return False
    x11 = _x11_library()
    if x11 is None:
        return False
    display = x11.XOpenDisplay(window.get_display().get_name().encode())
    if not display:
        return False
    try:
        focus, revert = ctypes.c_ulong(), ctypes.c_int()
        x11.XGetInputFocus(display, ctypes.byref(focus), ctypes.byref(revert))
        if focus.value == window.get_xid():
            return True
        atom = x11.XInternAtom(display, b"_NET_ACTIVE_WINDOW", 1)
        if not atom:
            return False
        kind, count, remaining = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_ulong()
        fmt, data = ctypes.c_int(), ctypes.c_void_p()
        result = x11.XGetWindowProperty(display, x11.XDefaultRootWindow(display), atom,
            0, 1, 0, 33, ctypes.byref(kind), ctypes.byref(fmt), ctypes.byref(count),
            ctypes.byref(remaining), ctypes.byref(data))  # 33 = XA_WINDOW
        try:
            return bool(result == 0 and kind.value == 33 and fmt.value == 32
                        and count.value == 1 and data.value
                        and ctypes.cast(data, ctypes.POINTER(ctypes.c_ulong))[0] == window.get_xid())
        finally:
            if data.value:
                x11.XFree(data)
    finally:
        x11.XCloseDisplay(display)

def menu_action(text=""):
    button = Gtk.Button(label=text)
    button.set_alignment(0.0, 0.5)
    button.get_style_context().add_class("action")
    return button




class TitleReveal(Gtk.Window):
    """Extend only the hovered title row, without reallocating the parent card."""
    def __init__(self, owner, changed):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.owner, self.changed = owner, changed
        self.anchor = None
        self.hovered = False
        self.timer = None
        self.set_name("routing-title-reveal")
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(False)
        self.set_focus_on_map(False)
        self.set_type_hint(Gdk.WindowTypeHint.TOOLTIP)
        self.set_transient_for(owner)
        self.set_destroy_with_parent(True)
        self.set_app_paintable(True)
        visual = self.get_screen().get_rgba_visual()
        if visual:
            self.set_visual(visual)
        self.strip = Gtk.EventBox()
        self.strip.set_name("routing-title-strip")
        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        content.set_margin_start(8)
        content.set_margin_end(8)
        self.title = Gtk.Label(xalign=0)
        self.title.set_single_line_mode(True)
        self.title.set_ellipsize(Pango.EllipsizeMode.END)
        self.title.set_max_width_chars(1)
        arrow = Gtk.Image.new_from_icon_name("pan-down-symbolic", Gtk.IconSize.MENU)
        arrow.set_pixel_size(10)
        content.pack_start(self.title, True, True, 0)
        content.pack_start(arrow, False, False, 0)
        self.strip.add(content)
        self.add(self.strip)
        self.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK | Gdk.EventMask.BUTTON_PRESS_MASK)
        self.connect("enter-notify-event", lambda _, e: self._hover(True, e))
        self.connect("leave-notify-event", lambda _, e: self._hover(False, e))
        self.connect("button-press-event", self._click)
        self.connect("hide", self._stop)

    def _hover(self, entered, event):
        if event.detail != Gdk.NotifyType.INFERIOR:
            self.hovered = entered
            if entered:
                self.owner.hovered.add("title")
                self.owner._cancel_timer("hide_timer")
            else:
                self.owner.hovered.discard("title")
                self.owner.schedule_hide()
            self.changed()
        return False

    def _click(self, _, event):
        if event.button == 1 and self.anchor:
            anchor = self.anchor
            self.hide()
            anchor.set_active(True)
            return True
        return False

    def _stop(self, *_):
        if self.timer is not None:
            GLib.source_remove(self.timer)
            self.timer = None
        self.hovered = False
        self.owner.hovered.discard("title")

    def reveal(self, anchor, title):
        if self.get_visible() and self.anchor is anchor and self.title.get_text() == title:
            return
        self._stop()
        self.anchor = anchor
        self.title.set_text(title)
        px, py = self.owner.get_position()
        dx, dy = anchor.translate_coordinates(self.owner, 0, 0)
        allocation = anchor.get_allocation()
        # The background gains breathing room; the title text keeps the exact
        # same column as its compact label underneath.
        inset = 8
        x, y = px + dx, py + dy
        natural = self.title.create_pango_layout(title).get_pixel_size()[0] + 18 + 2*inset
        area = self.get_display().get_monitor_at_point(x, y).get_workarea()
        limit = min(420, area.x + area.width - x)
        pet = self.owner.pet
        if pet and y < pet.sprite_y + pet.view.height and y + allocation.height > pet.sprite_y:
            if x < pet.sprite_x:
                limit = min(limit, pet.sprite_x - 12 - x)
        target = min(natural, limit)
        if target <= allocation.width:
            self.hide()
            return
        current = allocation.width
        self.strip.set_size_request(current, allocation.height)
        self.resize(current, allocation.height)
        self.move(x, y)
        context = self.get_style_context()
        if self.owner.settings.get("theme") == "light":
            context.add_class("light")
        else:
            context.remove_class("light")
        self.show_all()
        def step():
            nonlocal current
            delta = target - current
            current = target if abs(delta) <= 2 else current + round(delta * .4)
            self.strip.set_size_request(current, allocation.height)
            self.resize(current, allocation.height)
            self.move(x, y)
            if current == target:
                self.timer = None
                return False
            return True
        self.timer = GLib.timeout_add(16, step)


class MenuWindow(Gtk.Window):
    """A separate surface without Gtk.Menu's X11 keyboard/pointer grabs.

    Uses the managed-window pattern in claude-pet's context menu. Global shortcuts
    stay with GNOME. Temporary screenshot grabs keep the menu visible; an actual
    move to another application or Escape dismisses it.
    """
    def __init__(self, owner):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.owner, self.anchor, self.point = owner, None, None
        self.align_right = False
        self.beside, self.avoid, self.workarea = None, (), None
        self.within_owner = False
        self.focus_check = None
        self.set_name("routing-popup")
        self.set_title("Fluff · 메뉴")
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_type_hint(Gdk.WindowTypeHint.POPUP_MENU)
        self.set_transient_for(owner)
        self.set_destroy_with_parent(True)
        self.set_modal(False)
        self.set_app_paintable(True)
        visual = self.get_screen().get_rgba_visual()
        if visual:
            self.set_visual(visual)
        self.items = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.items.set_name("routing-actions")
        self.items.get_style_context().add_class("menu-window")
        self.add(self.items)
        self.connect("focus-out-event", self._focus_out)
        self.connect("focus-in-event", self._cancel_focus_check)
        self.connect("hide", self._cancel_focus_check)
        self.connect("destroy", self._cancel_focus_check)
        self.connect("key-press-event", self._key_press)
        self.connect("size-allocate", lambda *_: self._place() if self.get_visible() else None)
        self.connect("delete-event", lambda *_: self.popdown() or True)

    def append(self, item):
        self.items.pack_start(item, False, False, 0)

    def set_choices(self, entries, selected, choose):
        """Both providers use identical columns and controls; details stay in tooltips."""
        for child in self.items.get_children():child.destroy()
        self.choice_items={};group=None;callbacks=[]
        for index,entry in enumerate([dict(key=None,title="자동 선택",detail="현재 활동을 자동으로 표시합니다."),*entries]):
            if index==1:self.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
            item=Gtk.RadioButton.new_with_label_from_widget(group,"");item.set_mode(False)
            if group is None:group=item
            item.remove(item.get_child())
            marker=Gtk.Label(label="•" if entry['key']==selected else "");marker.set_size_request(12,-1)
            title=Gtk.Label(label=entry['title'],xalign=0)
            title.set_single_line_mode(True);title.set_ellipsize(Pango.EllipsizeMode.END);title.set_max_width_chars(1)
            content=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=8)
            content.pack_start(marker,False,False,0);content.pack_start(title,True,True,0);item.add(content)
            item.get_accessible().set_description(entry['title']+" · "+entry.get('detail',''))
            item.set_active(entry['key']==selected)
            item.connect('toggled',lambda w,dot=marker:dot.set_text('•' if w.get_active() else ''))
            item.connect('key-press-event',self._key_press)
            self.append(item);self.choice_items[entry['key']]=item;callbacks.append((item,entry['key']))
        for item,key in callbacks:item.connect('toggled',lambda w,k=key:choose(k) if w.get_active() else None)
        if not entries:
            empty=menu_action('현재 작업 없음');empty.set_sensitive(False);self.append(empty)
        self.items.show_all()
        if self.get_visible():self.resize(1,1)

    def popup(self, anchor=None, *, point=None, align_right=False, beside=None, avoid=(), workarea=None, within_owner=False):
        self.anchor, self.point, self.align_right = anchor, point, align_right
        self.beside, self.avoid, self.workarea = beside, avoid, workarea
        self.within_owner=within_owner
        self.resize(1, 1)
        self.show_all()
        if self._place() is False:
            self.hide()
            return False
        self.present()
        return True

    def popdown(self):
        self.hide()

    def _anchor_rect(self):
        if not self.anchor:
            return None
        x, y = self.owner.get_position()
        dx, dy = self.anchor.translate_coordinates(self.owner, 0, 0)
        size = self.anchor.get_allocation()
        return x + dx, y + dy, size.width, size.height

    def _place(self):
        if self.beside:
            bx, by, bw, bh = self.beside
            area = self.workarea or self.get_display().get_monitor_at_point(int(bx+bw/2), int(by+bh/2)).get_workarea()
            point = beside_position(self.beside, self.get_size(), area, self.avoid)
            if point is None:
                self.hide()
                return False
            self.move(*point)
            return True
        anchor = self._anchor_rect()
        if anchor:
            ax, ay, aw, ah = anchor
            x, y = ax, ay + ah + 4
        elif self.point:
            x, y = self.point
        else:
            return
        area = self.get_display().get_monitor_at_point(int(x), int(y)).get_workarea()
        width, height = self.get_size()
        if anchor:
            if self.align_right:
                x = ax + aw - width
            if self.within_owner:
                owner_x,_=self.owner.get_position();owner_width=self.owner.get_size().width
                x=max(owner_x,min(x,owner_x+owner_width-width))
            if y + height > area.y + area.height:
                y = ay - height - 4
        x=max(area.x,min(int(x),area.x+area.width-width));y=max(area.y,min(int(y),area.y+area.height-height))
        if any(x<bx+bw and x+width>bx and y<by+bh and y+height>by for bx,by,bw,bh in self.avoid):
            bounds=(*self.owner.get_position(),*self.owner.get_size())
            point=beside_position(bounds,(width,height),area,self.avoid)
            if point is None:self.hide();return False
            x,y=point
        self.move(x,y)

    def _focus_out(self, *_):
        # Let a second click on the anchor toggle it off, rather than clearing its
        # state on mouse-down and reopening it on mouse-up.
        anchor = self._anchor_rect()
        if anchor:
            pointer = self.get_display().get_default_seat().get_pointer()
            _, x, y, mask = Gdk.get_default_root_window().get_device_position(pointer)
            ax, ay, aw, ah = anchor
            if mask & Gdk.ModifierType.BUTTON1_MASK and ax <= x < ax + aw and ay <= y < ay + ah:
                return False
        if self.focus_check is None:
            # Wait for the window manager's active-window property to settle.
            self.focus_check = GLib.timeout_add(80, self._check_focus)
        return False

    def _cancel_focus_check(self, *_):
        if self.focus_check is not None:
            GLib.source_remove(self.focus_check)
            self.focus_check = None
        return False

    def _check_focus(self):
        if not self.get_visible() or self.has_toplevel_focus():
            self.focus_check = None
            return False
        window = self.get_window()
        # Print Screen first grabs its shortcut, then the screenshot overlay.
        # Neither is an outside click. GNOME may focus its shell stage while
        # retaining this managed window as the active application.
        if retains_focus(window):
            return True
        self.focus_check = None
        self.popdown()
        return False

    def _key_press(self, _, event):
        if event.keyval == Gdk.KEY_Escape:
            self.popdown()
            return True
        if event.keyval in (Gdk.KEY_Up, Gdk.KEY_Down):
            self.child_focus(Gtk.DirectionType.TAB_BACKWARD if event.keyval == Gdk.KEY_Up
                             else Gtk.DirectionType.TAB_FORWARD)
            return True
        return False
