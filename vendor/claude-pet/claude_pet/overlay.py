"""The floating desktop pet window.

GTK3 on X11. Under a GNOME Wayland session this runs through XWayland, because
`_NET_WM_STATE_ABOVE` is the only always-on-top mechanism mutter honours for a
regular client -- `gtk-layer-shell` is wlroots-only.
"""

from __future__ import annotations

import math
import os
import random
import signal
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

import gi

for _namespace, _version in (
    ("Gtk", "3.0"),
    ("Gdk", "3.0"),
    ("GdkPixbuf", "2.0"),
    ("Pango", "1.0"),
    ("PangoCairo", "1.0"),
):
    gi.require_version(_namespace, _version)

from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango, PangoCairo  # noqa: E402

from . import config, desktop, jump, motion, petting, sprites, state, tray  # noqa: E402
from . import pointer as pointer_visibility  # noqa: E402

BUBBLE_WIDTH = 260
BUBBLE_GAP = 8

#: How small and large the pet may be made from the size slider, in on-screen
#: pixels of sprite height. The default (config.DEFAULTS["height"]) sits inside
#: this. Too small and it cannot be clicked; too large and it eats the screen.
SIZE_MIN = 64
SIZE_MAX = 320
SIZE_STEP = 4
POLL_INTERVAL_MS = 250

#: How often the Claude Desktop app is looked for. It has no hooks to announce
#: itself, so presence has to be polled -- but that is a /proc sweep, and the
#: state poll runs four times a second, so the two are deliberately decoupled.
DESKTOP_CHECK_SECONDS = 5.0

#: How often its entry is re-stamped while it stays open. Only needs to beat
#: the session TTL; more often would just be lock contention with the hooks.
DESKTOP_KEEPALIVE_SECONDS = 300.0

#: Shown where a session's project name goes. The app has no working directory.
DESKTOP_LABEL = "Claude Desktop"

#: Look frames ordered left-to-right. A v2 sheet stores 16 yaw poses across
#: rows 9-10; row 10 holds the left half of the sweep and row 9 the right half.
LOOK_ORDER: tuple[int, ...] = (8, 9, 10, 11, 12, 13, 14, 15, 0, 1, 2, 3, 4, 5, 6, 7)

#: Animation row played once when a state is entered, before settling into the
#: state's own row. Finishing a turn earns a hop.
INTRO = {"review": "jumping"}

#: How long each state stays interesting lives in `state.DWELL_SECONDS`, since
#: it has to be applied per session while aggregating, not to the result.

#: Frames per second per state -- working should read as busier than idle.
STATE_FPS = {
    "idle": 6,
    "waiting": 8,
    "review": 8,
    "running": 12,
    "failed": 8,
    "waving": 10,
    "jumping": 12,
    # A stroll, not a sprint: at 12fps with a 6px step the pet tore along the
    # bottom of the screen, which reads as agitated rather than idle.
    "running-right": 8,
    "running-left": 8,
}

#: Seconds between wanders, and how long each lasts.
WALK_PAUSE_RANGE = (12.0, 30.0)
WALK_DURATION_RANGE = (2.0, 5.0)

#: Bubble state labels. `language: auto` picks by locale and falls back to en.
LABELS: dict[str, dict[str, str]] = {
    "en": {
        "idle": "idle",
        "running": "working",
        "waiting": "needs you",
        "review": "done",
        "failed": "failed",
        "waving": "session started",
        "menu.walk": "Wander around",
        "menu.petting": "Enjoy being petted",
        "menu.throwing": "Can be thrown",
        "menu.call": "Come when called",
        "menu.teleport": "Appear at a drawn star",
        "menu.on_top": "Always on top",
        "usage.line": "5h {five}% · 7d {seven}%",
        "usage.line_cost": "5h {five}% · 7d {seven}% · ${cost}",
        "toast.usage_warn": "5h limit {five}%",
        "toast.bridge_relogin": "log out & back in to finish setup",
        "menu.behaviour": "Behaviour…",
        "menu.tuning": "Tuning…",
        "menu.tune_reset": "Reset to defaults",
        "tune.throw_flick": "Throw · flick needed",
        "tune.throw_friction": "Throw · friction",
        "tune.throw_bounce": "Throw · bounce",
        "tune.call_pace": "Call · walking speed",
        "tune.call_seconds": "Call · draw it within",
        "tune.call_size": "Call · circle size",
        "tune.call_roundness": "Call · how round",
        "tune.star_size": "Star · size needed",
        "tune.size": "Pet · size",
        "menu.look": "Watch the pointer",
        "menu.notify": "Desktop notifications",
        "menu.autostart": "Start with Claude",
        "menu.desktop": "Follow Claude Desktop",
        "menu.exit_idle": "Quit when no sessions",
        "menu.browse": "Browse the gallery…",
        "menu.install": "Install a pet…",
        "menu.install_file": "Install from a file…",
        "menu.remove": "Remove this pet…",
        "menu.update_check": "Check for updates…",
        "menu.update_current": "Up to date",
        "menu.update_available": "Update to {version}",
        "menu.updating": "updating…",
        "toast.uptodate": "already up to date",
        "toast.update_failed": "update failed — see the terminal",
        "menu.quit": "Quit",
        "dialog.install": "Pet id, or a codex-pets.net link:",
        "dialog.remove": "Remove {pet}? It can be installed again from the gallery.",
        "dialog.ok": "OK",
        "dialog.cancel": "Cancel",
        "toast.installed": "installed {pet}",
        "toast.removed": "removed {pet}",
        "toast.reset": "back to its corner",
        "toast.coming": "coming",
        "toast.arrived": "here",
        "toast.teleported": "poof!",
        "menu.pets": "Pets…",
        "menu.language": "Language…",
        "menu.reset": "Reset position",
        "menu.back": "‹ Back",
        "menu.settings": "Settings",
        "lang.auto": "Automatic",
        "lang.en": "English",
        "lang.ko": "한국어",
        "toast.failed": "failed: {reason}",
        "jump.hint": "click to jump",
    },
    "ko": {
        "idle": "대기 중",
        "running": "작업 중",
        "waiting": "입력 대기",
        "review": "응답 완료",
        "failed": "실패",
        "waving": "세션 시작",
        "menu.walk": "돌아다니기",
        "menu.petting": "쓰담쓰담 받기",
        "menu.throwing": "던지기 허용",
        "menu.call": "부르면 오기",
        "menu.behaviour": "동작…",
        "menu.look": "포인터 쳐다보기",
        "menu.notify": "데스크톱 알림",
        "menu.autostart": "클로드와 함께 시작",
        "menu.desktop": "Claude Desktop 연동",
        "menu.exit_idle": "세션 없으면 종료",
        "menu.browse": "갤러리 열기…",
        "menu.install": "펫 설치…",
        "menu.install_file": "파일에서 설치…",
        "menu.remove": "이 펫 삭제…",
        "menu.update_check": "업데이트 확인…",
        "menu.update_current": "최신 버전",
        "menu.update_available": "{version} 로 업데이트",
        "menu.updating": "업데이트 중…",
        "toast.uptodate": "이미 최신이에요",
        "toast.update_failed": "업데이트 실패 — 터미널 확인",
        "menu.quit": "종료",
        "dialog.install": "펫 id 또는 codex-pets.net 링크:",
        "dialog.remove": "{pet} 을(를) 삭제할까요? 갤러리에서 다시 받을 수 있습니다.",
        "dialog.ok": "확인",
        "dialog.cancel": "취소",
        "toast.installed": "{pet} 설치됨",
        "toast.removed": "{pet} 삭제됨",
        "toast.reset": "제자리로 돌아왔어요",
        "toast.coming": "가는 중",
        "toast.arrived": "왔어요",
        "toast.teleported": "슝!",
        "menu.pets": "펫 관리…",
        "menu.language": "언어…",
        "menu.reset": "위치 초기화",
        "menu.teleport": "별 그리면 순간이동",
        "menu.on_top": "항상 위에",
        "usage.line": "5시간 {five}% · 주간 {seven}%",
        "usage.line_cost": "5시간 {five}% · 주간 {seven}% · ${cost}",
        "toast.usage_warn": "5시간 한도 {five}%",
        "toast.bridge_relogin": "로그아웃 후 다시 로그인하면 완료",
        "menu.tuning": "세부 조정…",
        "menu.tune_reset": "기본값으로",
        "tune.throw_flick": "던지기 · 필요한 세기",
        "tune.throw_friction": "던지기 · 마찰",
        "tune.throw_bounce": "던지기 · 튕김",
        "tune.call_pace": "부르기 · 걸음 속도",
        "tune.call_seconds": "부르기 · 그리는 시간",
        "tune.call_size": "부르기 · 원 크기",
        "tune.call_roundness": "부르기 · 원형 정도",
        "tune.star_size": "별 · 필요한 크기",
        "tune.size": "펫 · 크기",
        "menu.back": "‹ 뒤로",
        "menu.settings": "설정",
        "lang.auto": "자동",
        "lang.en": "English",
        "lang.ko": "한국어",
        "toast.failed": "실패: {reason}",
        "jump.hint": "클릭하면 이동",
    },
}


#: Things the pet can say per state, one picked when the state is entered.
#: `{tool}` is filled with the tool Claude is using, when there is one.
PHRASES: dict[str, dict[str, tuple[str, ...]]] = {
    "en": {
        # Nothing here may sound like the pet wants something. `idle` used to
        # offer "waiting for you", a hair away from `waiting`'s "needs you" --
        # so the one state that means *nothing is happening* read as the one
        # state that means *go and deal with this*.
        "idle": ("nothing running", "all quiet", "nothing to do"),
        "running": ("working", "on it", "{tool}…", "busy with {tool}"),
        "waiting": ("needs you",),
        "review": ("done", "all yours", "have a look"),
        "failed": ("that failed", "something broke"),
        "waving": ("hello", "session started"),
    },
    "ko": {
        # Worse in Korean than in English: `idle` said 기다리는 중 and
        # `waiting` says 입력 대기 -- both read as "waiting" at a glance, and
        # they are opposites.
        "idle": ("할 일 없음", "조용함", "쉬는 중"),
        "running": ("작업 중", "하는 중", "{tool} 중", "{tool} 돌리는 중"),
        "waiting": ("입력 대기",),
        # Not 확인해 주세요: an imperative asking the user to go and do
        # something, which is `waiting`'s job. `review` is only announcing that
        # output exists.
        "review": ("응답 완료", "다 됐어요", "결과 나왔어요"),
        "failed": ("실패했어요", "뭔가 깨졌어요"),
        "waving": ("안녕", "세션 시작"),
    },
}

#: Below this, saying how long something has been going is just noise.
ELAPSED_FLOOR_SECONDS = 45


#: What it says when stroked. Its own table rather than an entry in PHRASES,
#: because being petted is not one of the states the pet reports -- those come
#: from the agents, and this one comes from you.
PETTED: dict[str, tuple[str, ...]] = {
    "en": ("♥", "that's nice", "again", "hello you"),
    "ko": ("♥", "좋아", "더 해줘", "헤헤"),
}


def resolve_petted(language: str) -> tuple[str, ...]:
    if language in PETTED:
        return PETTED[language]
    if language == "auto":
        for variable in ("LC_ALL", "LC_MESSAGES", "LANG"):
            value = os.environ.get(variable, "")
            if value:
                return PETTED.get(value.split("_")[0].lower(), PETTED["en"])
    return PETTED["en"]


def resolve_phrases(language: str) -> dict[str, tuple[str, ...]]:
    if language in PHRASES:
        return PHRASES[language]
    if language == "auto":
        for variable in ("LC_ALL", "LC_MESSAGES", "LANG"):
            value = os.environ.get(variable, "")
            if value:
                return PHRASES.get(value.split("_")[0].lower(), PHRASES["en"])
    return PHRASES["en"]


def resolve_labels(language: str) -> dict[str, str]:
    if language in LABELS:
        return LABELS[language]
    if language == "auto":
        for variable in ("LC_ALL", "LC_MESSAGES", "LANG"):
            value = os.environ.get(variable, "")
            if value:
                return LABELS.get(value.split("_")[0].lower(), LABELS["en"])
    return LABELS["en"]

NOTIFY_ON = {"waiting", "review", "failed"}

#: States whose bubble advertises the jump. Clicking tries it whenever a
#: location is known, but only these are worth cluttering the bubble for.
JUMPABLE = {"waiting", "review", "failed"}

#: Pointer travel, in pixels, that turns a click into a drag.
DRAG_THRESHOLD = 5

#: How far from the screen edge the pet keeps, when anchored and while
#: wandering. Enough that it does not look wedged against the side.
EDGE_MARGIN = 12

#: How often the pointer is read while watching for a summons.
#:
#: Fast, and on its own timer rather than the animation frame. A wave is a
#: short gesture -- under a second -- and at five reads a second the samples
#: land either side of a reversal and miss it entirely: measured, a 0.8s
#: wave never registered at 0.2s and always did at 0.05s. Tying it to the
#: frame rate would also make a user's `fps` setting decide whether the pet
#: can hear them.
CALL_POLL_MS = 50

#: How often a pet in motion is moved, in milliseconds.
#:
#: Sixty times a second, and nothing to do with the animation frame. A
#: throw covers hundreds of pixels a second, so advancing it once per
#: animation frame moved it in forty-pixel jumps and looked like
#: stuttering -- the sprite was fine, the position was not. Runs only while
#: something is moving, so an idle pet is not waking the machine sixty
#: times a second for nothing.
MOTION_POLL_MS = 16

#: How near the pointer counts as arrived: touching it, near enough. Not
#: centred on it -- the sprite swallows clicks, so a pet parked exactly
#: under the cursor is a pet in the way -- but close enough to have plainly
#: come when called, which stopping a whole sprite's width short did not
#: look like.
CALL_ARRIVAL_PIXELS = 8

#: How long it stays put after arriving. Wandering off the moment it got
#: there read as never having stopped at all.
CALL_REST_SECONDS = 25.0

#: Give up an errand that has gone this long without a usable pointer reading.
#: The pause-on-None guard holds the pet still when the compositor cannot say
#: where the pointer is; without a limit a reading that never returns leaves
#: the pet standing with its "coming" bubble up forever, which is how it was
#: reported. Long enough to ride out any real hitch, short enough not to sulk.
ERRAND_STALL_SECONDS = 4.0

#: And a plain backstop on the whole errand, in case it can read the pointer
#: but somehow never arrives. Generous: a slow walk across a wide desk is a
#: minute of legitimate travelling.
ERRAND_MAX_SECONDS = 120.0

#: How still the pointer must be before the pet counts as having arrived,
#: in pixels of movement between one look and the next.
#:
#: Arrival was distance alone, and a pointer being moved about the screen
#: passes near the pet on its way -- so the pet declared it had arrived at
#: a cursor that was merely sweeping past, and gave up following. Coming to
#: you means coming to where you stopped.
CALL_SETTLED_PIXELS = 6

#: How much brisker than a wander a summoned pet is.
#:
#: Barely: it is walking, not running. Making the speed depend on the
#: distance was tried and is worse -- a pet that crosses two monitors in a
#: second and a half is a cursor with a sprite on it. What makes the walk
#: bearable is that it answers the moment it hears you, so the time it takes
#: to arrive is time you can spend on something else.
CALL_PACE = 2.0

#: Set CLAUDE_PET_DEBUG=1 to trace menu dismissal decisions. The signals here
#: differ between X11 and Wayland surfaces and cannot be reasoned about from the
#: outside, so this stays available rather than being guesswork each time.
DEBUG = bool(os.environ.get("CLAUDE_PET_DEBUG"))


def trace(message: str) -> None:
    if DEBUG:
        print(f"[claude-pet] {message}", flush=True)


def _guard(func, keep):
    """Wrap a GLib timeout callback so one exception cannot kill it for good.

    A raised exception propagates out of the callback, and GLib then removes
    the source -- so a single bad frame stopped the animation, a single bad
    poll stopped state updates, and the pet sat there alive but frozen with
    nothing on screen to say why (measured: a ZeroDivisionError in the motion
    loop did exactly this). The error is logged to overlay.log and the source
    kept alive with its intended repeat value, so the pet recovers on the next
    tick instead of freezing until it is restarted.
    """
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception:  # noqa: BLE001 -- a live pet beats a correct crash
            traceback.print_exc()
            return keep
    return wrapper


def _xid(menu) -> str:
    """X window id of a menu's toplevel, for matching against xwininfo."""
    toplevel = menu.get_toplevel()
    surface = toplevel.get_window() if toplevel is not None else None
    try:
        return hex(surface.get_xid())
    except AttributeError:
        return "?"


def _to_pixbuf(image) -> GdkPixbuf.Pixbuf:
    data = GLib.Bytes.new(image.tobytes())
    return GdkPixbuf.Pixbuf.new_from_bytes(
        data, GdkPixbuf.Colorspace.RGB, True, 8, image.width, image.height, image.width * 4
    )


class PetView:
    """Scaled, pixbuf-ready frames for one pet pack."""

    def __init__(self, pet: sprites.Pet, height: int) -> None:
        self.pet = pet
        self.animations: dict[str, list[GdkPixbuf.Pixbuf]] = {}
        for name, frames in pet.animations.items():
            scaled = sprites.scale_frames(frames, height)
            self.animations[name] = [_to_pixbuf(frame) for frame in scaled]

        self.looks: list[GdkPixbuf.Pixbuf] = []
        if len(pet.looks) >= max(LOOK_ORDER) + 1:
            ordered = [pet.looks[index] for index in LOOK_ORDER]
            self.looks = [_to_pixbuf(frame) for frame in sprites.scale_frames(ordered, height)]

        reference = next((frames for frames in self.animations.values() if frames), None)
        if reference is None:
            raise sprites.SpriteError(f"{pet.id}: spritesheet has no visible frames")
        self.width = reference[0].get_width()
        self.height = reference[0].get_height()

    def frames(self, name: str) -> list[GdkPixbuf.Pixbuf]:
        for candidate in (name, "idle", "running", "waiting"):
            frames = self.animations.get(candidate)
            if frames:
                return frames
        return next(frames for frames in self.animations.values() if frames)


class Overlay(Gtk.Window):
    def __init__(self, view: PetView, settings: dict[str, Any], poll: bool = True) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.view = view
        self.settings = settings
        language = str(settings.get("language") or "auto")
        self.labels = resolve_labels(language)
        self.phrases = resolve_phrases(language)
        self.phrase = ""
        self.project = ""
        self.since = time.time()

        self.state = "idle"
        self.detail = ""
        self.sessions = 0
        self.visual_state = "idle"
        self.frame_index = 0
        self.visual_until: float | None = None
        self.visual_return: str | None = None
        self.bubble_pinned = False
        #: The (state, since) episode already jumped to, so its bubble
        #: stays down until the state next changes -- you have seen it.
        self.jumped_episode: tuple[str, float] | None = None
        self.empty_since: float | None = None
        self.locator: dict[str, Any] | None = None
        self.desktop_watcher: desktop.NotifyWatcher | None = None
        self.desktop_checked_at = 0.0
        self.desktop_written_at = 0.0
        self.cpu_sampled_at = 0.0
        self.tray: tray.Tray | None = None
        #: What the code on disk looked like when it was imported.
        self.code_stamp: tuple[float, int] | None = None
        # A short-lived bubble override, for telling the user how a click went.
        self.flash_text = ""
        self.flash_until = 0.0
        self.press_origin: tuple[int, int] | None = None
        self.menu_opened_at = 0.0
        self.menu: Gtk.Window | None = None
        #: None until a check has run; then {current, latest, available}.
        self.update_info: dict[str, str | bool] | None = None
        self.busy = ""  # a one-word note shown in the menu while work is running

        self.dragging = False
        # The overlay owns its position. Reading it back from GTK on every walk
        # step accumulates frame-vs-client offset error and the pet drifts off
        # the floor and past its bounds.
        self.pos_x = 0
        self.pos_y = 0
        self.walking = 0          # -1 left, 0 still, +1 right
        self.walk_until = 0.0
        self.next_walk_at = time.monotonic() + random.uniform(*WALK_PAUSE_RANGE)
        self.look_index: int | None = None

        self.window_width = max(view.width, BUBBLE_WIDTH)
        self.window_height = view.height + BUBBLE_GAP + 78
        self.sprite_left = (self.window_width - view.width) // 2
        #: Room the bubble needs, on whichever side of the sprite it is on.
        self.bubble_space = self.window_height - view.height
        self.bubble_below = False
        self.sprite_top = self.bubble_space
        #: Offset from the pointer to the sprite's corner, held during a drag.
        self.drag_offset: tuple[int, int] | None = None

        #: Recognises the back-and-forth of a hovering pointer -- over the
        #: pet it means affection, away from it means come here.
        self.stroke = petting.Stroke()
        self.call_stroke = petting.Stroke()  # both replaced by _rebuild_gestures
        self.star_stroke = petting.Star()
        self.called_at = 0.0
        self.errand_seen_at = 0.0
        self.pointer_checked_at = 0.0
        #: The tail of a drag, and the throw it may have ended in.
        self.flick = motion.Flick()
        #: Pending config write for a slider being dragged.
        self.tune_save: int | None = None
        #: Pending pet-resize for the size slider being dragged. Rebuilding the
        #: view rescales every frame, so it is debounced rather than run on
        #: each of the many events one drag fires.
        self.size_apply: int | None = None
        #: The size slider itself, so "reset to defaults" can move it too.
        self._size_scale: Gtk.Scale | None = None
        #: The tuning window, while it is open.
        self.tuning: Gtk.Window | None = None
        #: The 5h window (its resets_at) we have already warned about, so
        #: the heads-up fires once per window rather than every check.
        self.usage_warned_window: int | None = None
        self._rebuild_gestures()
        self.throw: motion.Throw | None = None
        self.thrown_at = 0.0
        #: Where to walk to when called, as a sprite position. None means
        #: wander as usual.
        self.walk_target: tuple[int, int] | None = None
        self.motion_running = False
        self.moved_at = 0.0
        #: Where the walk has got to, kept as floats. At a wander's pace on
        #: a sixty-hertz timer each step is half a pixel, and rounding that
        #: to whole pixels per axis rounds it to nothing: the pet stood
        #: still forever, having been told to move.
        self.errand_at: tuple[float, float] | None = None
        #: Where the pointer was at the previous look, for telling a cursor
        #: that has stopped from one that is passing through.
        self.pointer_was: tuple[int, int] | None = None
        self.pointer_settled = True
        self.petted_count = int(settings.get("petted_count") or 0)
        self.phrases_petted = resolve_petted(language)

        self._configure_window()
        self._place_initial()

        self.connect("draw", self._on_draw)
        self.connect("button-press-event", self._on_button_press)
        self.connect("button-release-event", self._on_button_release)
        self.connect("motion-notify-event", self._on_motion)
        self.connect("configure-event", self._on_configure)
        self.connect("destroy", lambda *_: self.quit())
        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )

        self.show_all()
        self._place_initial()  # a move() before realize is not always honoured
        self._apply_input_shape()
        if poll:
            GLib.timeout_add(POLL_INTERVAL_MS, _guard(self._poll_state, True))
            self._schedule_update_check()
            self._start_desktop_watch()
            self._start_tray()
            GLib.timeout_add(CALL_POLL_MS, _guard(self._listen_for_a_call, True))
            self.code_stamp = self._code_stamp()
            GLib.timeout_add_seconds(60, self._check_for_new_code)
            # First look soon after start, then every couple of minutes.
            GLib.timeout_add_seconds(15, self._check_usage_once)
            GLib.timeout_add_seconds(120, lambda: self._check_usage_once() or True)
            # If the pointer bridge is installed but not yet loaded (a fresh
            # install or a menu update, which cannot re-login for you),
            # say so once -- the only channel those paths have.
            GLib.timeout_add_seconds(8, self._remind_bridge_relogin)
        self._schedule_frame()

    # ---------------------------------------------------------------- window

    def _configure_window(self) -> None:
        self.set_title("claude-pet")
        self.set_wmclass("claude-pet", "claude-pet")
        self.set_default_size(self.window_width, self.window_height)
        self.set_size_request(self.window_width, self.window_height)
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        # Normally what an overlay is for, but a pet in front of what you are
        # reading is a pet in the way, and nothing else can put it behind a
        # window -- so it is a toggle.
        self.set_keep_above(bool(self.settings.get("on_top", True)))
        self.set_accept_focus(False)
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.set_app_paintable(True)
        self.stick()

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None:
            self.set_visual(visual)

    # ------------------------------------------------------------- geometry
    #
    # Everything here reasons about where the *sprite* is, not where the window
    # is. They are not the same: the window reserves room for the bubble, so
    # the visible pet sits inset from the window it lives in, and using window
    # coordinates for questions about the pet gets the answer wrong by that
    # inset -- most visibly at the top of the screen.

    @property
    def sprite_x(self) -> int:
        return self.pos_x + self.sprite_left

    @property
    def sprite_y(self) -> int:
        return self.pos_y + self.sprite_top

    def _monitors(self) -> list[Gdk.Rectangle]:
        display = Gdk.Display.get_default()
        areas = []
        for index in range(display.get_n_monitors()):
            monitor = display.get_monitor(index)
            if monitor is not None:
                areas.append(monitor.get_workarea())
        if not areas:
            monitor = display.get_primary_monitor() or display.get_monitor(0)
            areas.append(monitor.get_workarea())
        return areas

    def _workarea_for_sprite(self, x: int | None = None, y: int | None = None) -> Gdk.Rectangle:
        """Work area of the screen the pet is on, not always the primary one.

        Answering "primary" regardless meant the walk bounds dragged a pet on
        the second screen back across the seam, and a position remembered on a
        screen since unplugged was clamped against a rectangle it was nowhere
        near -- which is one of the ways the pet became unreachable.
        """
        x = self.sprite_x if x is None else x
        y = self.sprite_y if y is None else y
        centre_x = x + self.view.width // 2
        centre_y = y + self.view.height // 2
        monitors = self._monitors()
        for area in monitors:
            if (area.x <= centre_x < area.x + area.width
                    and area.y <= centre_y < area.y + area.height):
                return area

        # Past every edge -- being dragged out of the far side of a screen.
        # The nearest one, not the primary: falling back to primary teleported
        # a pet leaving the right of the second monitor onto the first.
        def distance(area: Gdk.Rectangle) -> float:
            dx = max(area.x - centre_x, 0, centre_x - (area.x + area.width))
            dy = max(area.y - centre_y, 0, centre_y - (area.y + area.height))
            return math.hypot(dx, dy)

        return min(monitors, key=distance)

    def _workarea(self) -> Gdk.Rectangle:
        return self._workarea_for_sprite()

    def _on_screen(self, x: int, y: int) -> bool:
        """Whether a sprite placed here would be visible on some monitor.

        Asked of a remembered position before trusting it: screens get
        unplugged and resolutions change, and a pet restored onto a screen that
        is no longer there is a pet with no way back.
        """
        for area in self._monitors():
            if (x + self.view.width > area.x and x < area.x + area.width
                    and y + self.view.height > area.y and y < area.y + area.height):
                return True
        return False

    def _set_bubble_side(self, below: bool) -> None:
        """Put the bubble under the sprite instead of over it, or back again."""
        if below == self.bubble_below:
            return
        self.bubble_below = below
        self.sprite_top = 0 if below else self.bubble_space
        self._apply_input_shape()
        self.queue_draw()

    def _span(self) -> tuple[int, int, int, int]:
        """Everything the pet may occupy, across every screen.

        Clamping to one monitor is right for wandering, which should stay
        on the screen it is on, and wrong for anything crossing between
        them. A pet walking left off the second monitor cannot get onto the
        first: to count as being on the first its centre must pass the
        seam, and the second monitor's own bounds forbid exactly that. It
        sticks at the join, which looks like a summons being ignored.
        """
        areas = self._monitors()
        return (
            min(a.x for a in areas) + EDGE_MARGIN,
            min(a.y for a in areas),
            max(a.x + a.width for a in areas) - EDGE_MARGIN - self.view.width,
            max(a.y + a.height for a in areas) - EDGE_MARGIN - self.view.height,
        )

    def _clamp_across(self, x: int, y: int) -> tuple[int, int]:
        """Nearest spot that is actually on a screen, anywhere across them all.

        `_span()` is the bounding box of every monitor, which is the right
        answer only when the monitors fill it. Arrange three in an L and they
        do not: the box covers a corner with no screen behind it, and clamping
        into that corner puts the pet somewhere the user cannot see -- reported
        as it wandering off to the empty bottom-left.

        So each monitor is asked where it would put the sprite, and the nearest
        of those answers wins. A point already on a screen is its own answer
        (distance nought) and comes back unchanged, so nothing about a plain
        side-by-side layout changes.
        """
        best: tuple[float, int, int] | None = None
        for area in self._monitors():
            left = area.x + EDGE_MARGIN
            top = area.y
            # A monitor narrower or shorter than the sprite would give a right
            # edge left of its left one; keep the range degenerate rather than
            # inverted so the clamp stays inside the screen.
            right = max(left, area.x + area.width - EDGE_MARGIN - self.view.width)
            bottom = max(top, area.y + area.height - EDGE_MARGIN - self.view.height)
            near_x = min(max(x, left), right)
            near_y = min(max(y, top), bottom)
            distance = (near_x - x) ** 2 + (near_y - y) ** 2
            if best is None or distance < best[0]:
                best = (distance, near_x, near_y)
        if best is None:
            return x, y
        return best[1], best[2]

    def _place_sprite(self, x: int, y: int, across_screens: bool = False) -> None:
        """Put the *sprite* here, and work out where the window has to go.

        The window is taller than the sprite because the bubble needs somewhere
        to live, and that room used to be above it unconditionally. So the
        window's top met the panel while the sprite was still 86 pixels short
        of it, and the pet could not be moved any higher however hard you
        pulled. When there is no room above, the bubble goes below instead and
        the sprite reaches the top of the screen.
        """
        area = self._workarea_for_sprite(x, y)
        if across_screens:
            x, y = self._clamp_across(x, y)
        else:
            x = min(max(x, area.x), area.x + area.width - self.view.width)
            y = min(max(y, area.y), area.y + area.height - self.view.height)

        self._set_bubble_side(y - area.y < self.bubble_space)
        self.pos_x = x - self.sprite_left
        self.pos_y = y - self.sprite_top
        self.move(self.pos_x, self.pos_y)

    def _anchored_sprite(self) -> tuple[int, int]:
        area = self._monitors()[0]
        anchor = str(self.settings.get("anchor") or "bottom-right")
        if anchor.endswith("left"):
            x = area.x + EDGE_MARGIN
        else:
            x = area.x + area.width - EDGE_MARGIN - self.view.width
        if anchor.startswith("top"):
            y = area.y + EDGE_MARGIN
        else:
            y = area.y + area.height - EDGE_MARGIN - self.view.height
        return x, y

    def _place_initial(self) -> None:
        stored = self.settings.get("position")
        x = y = None
        if isinstance(stored, (list, tuple)) and len(stored) == 2:
            try:
                x, y = int(stored[0]), int(stored[1])
            except (TypeError, ValueError):
                x = y = None

        # Stored positions are the window's corner, for compatibility with what
        # earlier versions wrote; the sprite is what has to land on a screen.
        if x is not None and self._on_screen(x + self.sprite_left, y + self.bubble_space):
            self._place_sprite(x + self.sprite_left, y + self.bubble_space)
            return
        if x is not None:
            # Forgotten, not just ignored, or the warning repeats on every
            # start and the pet keeps being restored from a bad memory.
            print("claude-pet: remembered position is off screen, re-anchoring", flush=True)
            self.settings["position"] = None
            config.update(position=None)
        self._place_sprite(*self._anchored_sprite())

    def reset_position(self) -> None:
        """Forget where the pet was dragged and send it back to its corner.

        The way out of "it wandered onto a screen that is no longer there".
        Reachable without touching the pet, which is the whole point: from the
        tray icon, or `claude-pet reset-position`.
        """
        self.settings["position"] = None
        config.update(position=None)
        self._halt_walk()
        self._place_sprite(*self._anchored_sprite())
        self._flash(self.labels["toast.reset"])

    def _apply_input_shape(self) -> None:
        """Only the sprite (and a visible bubble) should swallow clicks."""
        window = self.get_window()
        if window is None:
            return
        import cairo

        if self.dragging:
            # The whole window, so a pointer that gets slightly ahead of the
            # pet keeps sending motion events instead of dropping it.
            region = cairo.Region(
                cairo.RectangleInt(0, 0, self.window_width, self.window_height)
            )
        else:
            # Only the sprite takes clicks. The bubble is on screen most of the
            # time now, and a talkative pet must not become a 260px dead zone
            # over whatever is underneath it.
            region = cairo.Region(
                cairo.RectangleInt(
                    self.sprite_left, self.sprite_top, self.view.width, self.view.height
                )
            )
        window.input_shape_combine_region(region, 0, 0)

    def _on_configure(self, _widget, _event) -> bool:
        # Position is owned here now, not read back from the compositor: the
        # drag places the sprite directly, and reading the window's corner back
        # accumulated the frame-vs-client offset that used to walk the pet off
        # the bottom of the screen.
        return False

    # ----------------------------------------------------------------- state

    def _poll_state(self) -> bool:
        self._refresh_desktop()
        self._sample_cpu()
        # Re-aggregated every time, not only when the file changes: dwells
        # expire on the clock, so the same file yields a different state later.
        snapshot = state.aggregate()
        self._adopt(snapshot)
        self._maybe_exit(snapshot)
        return True

    # ------------------------------------------------------------------ tray

    def _tray_icon_path(self) -> str | None:
        """Write the pet's own face out for the status bar to use.

        An icon *name* is what the indicator wants, resolved through an icon
        theme, and only the packaged install puts one there. Writing the sprite
        into a directory of our own and pointing the indicator at it works for
        every install shape -- and the icon is then the pet you actually have.
        """
        directory = state.state_dir() / "tray"
        target = directory / "claude-pet.png"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            frames = self.view.animations.get("idle") or []
            if not frames:
                return None
            # 22px is the conventional status-icon size; the shell scales it.
            frames[0].scale_simple(22, 22, GdkPixbuf.InterpType.BILINEAR).savev(
                str(target), "png", [], []
            )
        except (OSError, GLib.Error) as exc:
            trace(f"tray icon: {exc}")
            return None
        return str(directory)

    def _start_tray(self) -> None:
        if self.tray is not None or not tray.available():
            if self.tray is None:
                trace("no tray bindings; reset position via `claude-pet reset-position`")
            return

        path = self._tray_icon_path()
        indicator = tray.Tray("claude-pet", "claude-pet")
        if path is not None:
            indicator.icon_theme_path = path
        if indicator.start(self._build_tray_menu):
            self.tray = indicator
            trace("tray icon shown")
        else:
            trace(f"no tray icon: {indicator.error}")

    def _refresh_tray(self) -> None:
        if self.tray is not None:
            self.tray.set_menu(self._build_tray_menu())

    def _build_tray_menu(self):
        """The right-click menu again, in a real Gtk.Menu.

        Same model, same order, same words -- the two used to be built by hand
        separately and had drifted apart. A GtkMenu is right here and wrong on
        the pet: the shell owns and dismisses this one, whereas the pet's own
        popup had to be an ordinary window to close on an outside click at all.
        Pages become real submenus, which is what a tray menu can do.
        """
        menu = Gtk.Menu()

        def fill(target, entries) -> None:
            for entry in entries:
                kind = entry[0]
                if kind == "separator":
                    target.append(Gtk.SeparatorMenuItem())
                elif kind == "caption":
                    item = Gtk.MenuItem(label=entry[1])
                    item.set_sensitive(False)
                    target.append(item)
                elif kind == "submenu":
                    item = Gtk.MenuItem(label=entry[1].rstrip("… "))
                    child = Gtk.Menu()
                    fill(child, self._menu_model(entry[2]))
                    item.set_submenu(child)
                    target.append(item)
                elif kind == "action":
                    item = Gtk.MenuItem(label=entry[1])
                    item.connect("activate", lambda _i, run=entry[2]: run())
                    target.append(item)
                elif kind == "update":
                    item = Gtk.MenuItem(label=self._update_label())
                    item.connect("activate", lambda _i: self._update_activate())
                    target.append(item)
                elif kind in {"toggle", "choice"}:
                    if kind == "toggle":
                        key, label, default = entry[1], entry[2], entry[3]
                        checked = bool(self.settings.get(key, default))
                        handler = lambda item, name=key: (  # noqa: E731
                            None
                            if bool(item.get_active()) == bool(self.settings.get(name))
                            else self._toggle_setting(name)
                        )
                    else:
                        label, group, value = entry[1], entry[2], entry[3]
                        checked = self._is_chosen(group, value)
                        handler = lambda item, g=group, v=value: (  # noqa: E731
                            None if item.get_active() is False else self._choose(g, v)
                        )
                    item = Gtk.CheckMenuItem(label=label)
                    item.set_active(checked)
                    item.connect("activate", handler)
                    target.append(item)

        fill(menu, self._menu_model())
        menu.show_all()
        return menu

    # ---------------------------------------------------------- code changes

    def _code_stamp(self) -> tuple[float, int] | None:
        """Fingerprint of the code on disk, for noticing it has been replaced."""
        try:
            info = os.stat(Path(__file__).resolve().parent / "__init__.py")
        except OSError:
            return None
        return (info.st_mtime, info.st_size)

    def _check_for_new_code(self) -> bool:
        """Restart if the installed code is no longer the code we are running.

        An upgrade replaces files under a process that has already imported
        them, so the pet carries on with the old behaviour and the update looks
        like it did nothing. That is easy to hit without going near
        `claude-pet update` at all -- apt, or the desktop's software centre,
        upgrading the package in the background.

        Deferred while the pet is busy being used: restarting out from under a
        drag or an open menu would be its own kind of broken.
        """
        stamp = self._code_stamp()
        if stamp is None or self.code_stamp is None or stamp == self.code_stamp:
            return True
        if self.dragging or self.menu is not None or self.busy:
            return True

        print("claude-pet: the installed version changed, restarting", flush=True)
        self.quit(restart=True)
        return False

    def _sample_cpu(self) -> None:
        """Watch what the session processes are actually doing.

        The overlay is the only long-lived piece, so it is the only thing that
        can take the second reading a rate needs. `state.sample_cpu` decides
        whether enough time has passed to bother.
        """
        now = time.monotonic()
        if now - self.cpu_sampled_at < state.CPU_SAMPLE_SECONDS:
            return
        self.cpu_sampled_at = now
        try:
            state.sample_cpu()
        except OSError:
            pass

    # -------------------------------------------------------- Claude Desktop

    def _start_desktop_watch(self) -> None:
        """Begin eavesdropping on the app's desktop notifications.

        Optional in the strongest sense: if the session bus will not hand out a
        monitor connection, the app simply contributes no chat state and the
        rest of the pet is unaffected.
        """
        if self.desktop_watcher is not None:
            return
        if not self.settings.get("desktop", True) or not desktop.installed():
            return

        watcher = desktop.NotifyWatcher(self._on_desktop_notification)
        if watcher.start():
            self.desktop_watcher = watcher
            trace("watching Claude Desktop notifications")
        else:
            trace(f"no Claude Desktop notification watch: {watcher.error}")

    def _stop_desktop_watch(self) -> None:
        watcher, self.desktop_watcher = self.desktop_watcher, None
        if watcher is not None:
            watcher.stop()

    def _on_desktop_notification(self, state_name: str, detail: str) -> None:
        """A reply landed in the app. Runs on the main loop, via idle_add."""
        try:
            state.update(
                desktop.SESSION_ID,
                state=state_name,
                detail=detail,
                cwd=DESKTOP_LABEL,
                locator=desktop.locator(),
                # Named like a hook event so the log reads the same either way.
                event="DesktopNotification",
                # Carried on the entry, because nothing will ever arrive to
                # clear a state the app only announced once.
                dwell=desktop.NOTIFY_DWELL_SECONDS.get(state_name),
            )
        except OSError:
            return
        self.desktop_written_at = time.monotonic()
        self._poll_state()  # show it now rather than up to a quarter-second late

    def _apply_desktop_setting(self) -> None:
        """Start or stop following the app, after the menu toggle."""
        if self.settings.get("desktop", True):
            self.desktop_checked_at = 0.0
            self.desktop_written_at = 0.0
            self._start_desktop_watch()
            # The toggle's own row is in the tray menu, so it has to redraw.
            self._refresh_tray()
            return

        self._stop_desktop_watch()
        # Drop the entry rather than let it linger: it survives on the app's
        # pid, so switching off would otherwise keep the pet alive until the
        # app was closed.
        try:
            state.update(desktop.SESSION_ID, state=None)
        except OSError:
            pass
        self._refresh_tray()

    def _refresh_desktop(self) -> None:
        """Keep the app's entry in the state file in step with the app itself.

        Nothing else ever writes it -- there are no hooks on that side -- and
        without it an open Claude Desktop would not stop the pet concluding
        that every session had gone and quitting.
        """
        if not self.settings.get("desktop", True):
            return

        now = time.monotonic()
        if now - self.desktop_checked_at < DESKTOP_CHECK_SECONDS:
            return
        self.desktop_checked_at = now

        if desktop.main_pid() is None:
            # No deletion needed: the entry names the pid to watch, so
            # `state.is_alive` prunes it as soon as the app exits.
            return

        known = state.read().get("sessions", {}).get(desktop.SESSION_ID)
        try:
            if isinstance(known, dict):
                if now - self.desktop_written_at < DESKTOP_KEEPALIVE_SECONDS:
                    return
                # `touch`, not `update`: the entry only needs to stay alive.
                # Re-reporting it would restart the dwell as well, and a reply
                # the app announced an hour ago would come back to life every
                # few minutes and sit there as "done" or "needs you".
                state.touch(desktop.SESSION_ID)
            else:
                state.update(
                    desktop.SESSION_ID,
                    state="idle",
                    cwd=DESKTOP_LABEL,
                    locator=desktop.locator(),
                )
        except OSError:
            return
        self.desktop_written_at = now

    def _maybe_exit(self, snapshot: dict[str, Any]) -> None:
        """Shut down once every Claude session is gone.

        Waits out a grace period first, so closing one terminal and opening
        another does not kill the pet in between.
        """
        if not self.settings.get("exit_when_no_sessions", True):
            self.empty_since = None
            return
        if snapshot.get("sessions"):
            self.empty_since = None
            return

        now = time.monotonic()
        if self.empty_since is None:
            self.empty_since = now
            return

        grace = float(self.settings.get("exit_grace_seconds") or 0)
        if now - self.empty_since >= grace:
            print("claude-pet: no Claude sessions left, exiting", flush=True)
            self.quit()

    def _adopt(self, snapshot: dict[str, Any]) -> None:
        new_state = snapshot.get("state") or "idle"
        self.detail = snapshot.get("detail") or ""
        self.sessions = int(snapshot.get("sessions") or 0)
        self.locator = snapshot.get("locator")
        self.project = Path(str(snapshot.get("cwd") or "")).name
        self.since = float(snapshot.get("since") or time.time())

        if new_state == self.state:
            return
        previous = self.state
        state.log_transition(previous, new_state, snapshot)
        self.state = new_state
        self.jumped_episode = None
        self.frame_index = 0
        self.walking = 0

        options = self.phrases.get(new_state) or (self.labels.get(new_state, new_state),)
        self.phrase = random.choice(options)

        intro = INTRO.get(new_state)
        if intro and self.view.animations.get(intro):
            self.visual_state = intro
            self.visual_return = new_state
            self.visual_until = time.monotonic() + self._row_duration(intro)
        else:
            self.visual_state = new_state
            self.visual_return = None
            self.visual_until = None

        if new_state in NOTIFY_ON and self.settings.get("notifications", False):
            if previous != new_state:
                self._notify(new_state)
        self._apply_input_shape()

    def _row_duration(self, name: str) -> float:
        frames = self.view.frames(name)
        return len(frames) / max(1, STATE_FPS.get(name, 10))

    def _notify(self, name: str) -> None:
        summary = f"Claude Code · {self.labels.get(name, name)}"
        body = self.detail or ""
        urgency = "critical" if name == "waiting" else "normal"
        try:
            subprocess.Popen(  # noqa: S603,S607 - fixed argv
                [
                    "notify-send",
                    "--app-name=claude-pet",
                    f"--urgency={urgency}",
                    "--hint=string:x-canonical-private-synchronous:claude-pet",
                    summary,
                    body,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, FileNotFoundError):
            pass

    # ------------------------------------------------------------- animation

    def _schedule_frame(self) -> None:
        fps = STATE_FPS.get(self.visual_state, 10)
        GLib.timeout_add(max(20, int(1000 / fps)), self._tick)

    def _tick(self) -> bool:
        try:
            self._tick_body()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        finally:
            # Always arm the next frame. The whole freeze was this line being
            # skipped when the body raised: no next frame, animation dead, the
            # pet alive but stuck.
            self._schedule_frame()
        return False

    def _tick_body(self) -> None:
        now = time.monotonic()

        if self.visual_until is not None and now >= self.visual_until:
            self.visual_state = self.visual_return or "idle"
            self.visual_until = None
            self.visual_return = None
            self.frame_index = 0

        # A throw and an errand outrank wandering, and unlike wandering they
        # happen whatever the agents are doing: being called is a thing you
        # asked for, and it would be odd to be ignored because Claude is busy.
        if self.throw is not None or self.walk_target is not None:
            pass  # its own timer moves it; this frame only animates
        elif self.state == "idle" and self.visual_until is None:
            self._update_walk(now)
        elif self.walking:
            self.walking = 0

        frames = self.view.frames(self.visual_state)
        self.frame_index = (self.frame_index + 1) % max(1, len(frames))
        self._update_look()

        self.queue_draw()

    def _update_walk(self, now: float) -> None:
        if not self.settings.get("walk", True):
            return
        # Stop the moment it is grabbed, not merely hovered: move() during a
        # drag fights the window manager. Hovering leaves it walking, so it can
        # be picked up mid-stride.
        if self.dragging or self.press_origin is not None:
            return

        if self.walking and now >= self.walk_until:
            self.walking = 0
            self.visual_state = "idle"
            self.frame_index = 0
            self.next_walk_at = now + random.uniform(*WALK_PAUSE_RANGE)
            return

        if not self.walking and now >= self.next_walk_at:
            self.walking = random.choice((-1, 1))
            self.walk_until = now + random.uniform(*WALK_DURATION_RANGE)
            self.visual_state = "running-right" if self.walking > 0 else "running-left"
            self.frame_index = 0
            return

        if not self.walking:
            return

        # Bounds are the sprite's, on whichever monitor it is currently on.
        area = self._workarea()
        left_bound = area.x + EDGE_MARGIN
        right_bound = area.x + area.width - EDGE_MARGIN - self.view.width
        step = max(1, int(self.settings.get("walk_speed") or 3))
        new_x = self.sprite_x + step * self.walking
        if new_x <= left_bound or new_x >= right_bound:
            self.walking *= -1
            self.visual_state = "running-right" if self.walking > 0 else "running-left"
        self._place_sprite(min(max(new_x, left_bound), right_bound), self.sprite_y)

    # -------------------------------------------------------- throw and call

    def _start_motion(self) -> None:
        """Begin moving the pet on its own clock, if it is not already."""
        if self.motion_running:
            return
        self.motion_running = True
        self.moved_at = time.monotonic()
        GLib.timeout_add(MOTION_POLL_MS, _guard(self._advance_motion, True))

    def _advance_motion(self) -> bool:
        now = time.monotonic()
        elapsed = max(0.001, min(0.2, now - self.moved_at))
        self.moved_at = now
        if self.throw is not None:
            self._advance_throw(now)
        elif self.walk_target is not None:
            # Aim at where the pointer is now, not where it was when called.
            # You move on while it walks, and a pet arriving at the place
            # you used to be has answered a question nobody asked.
            here, fresh = pointer_visibility.sample(self._pointer_position)
            if (here is None and now - self.errand_seen_at > ERRAND_STALL_SECONDS) \
                    or now - self.called_at > ERRAND_MAX_SECONDS:
                # Either the pointer has been unreadable too long, or the whole
                # errand has overrun. Give up rather than stand there with the
                # "coming" bubble up for good -- which is exactly what the
                # pause-below did when a reading never came back.
                self._abandon_errand()
                return False
            if here is None:
                # No trustworthy pointer this instant -- the compositor is busy
                # enough that the bridge has not answered lately. Hold where it
                # is rather than trudge on to the last target and stop there,
                # which is the "stops at a wall on a heavy page" report. The
                # walk resumes the moment a reading returns.
                self.moved_at = now
                return True
            self.errand_seen_at = now
            # Only a fresh reading says anything about whether the pointer has
            # stopped. This runs sixty times a second and the reading is shared
            # with the gesture poll, so most ticks see a repeat -- and a repeat
            # compared against itself is nought pixels of movement, which reads
            # as settled and lets the pet decide it has arrived while the
            # pointer is still moving.
            if fresh:
                if self.pointer_was is not None:
                    self.pointer_settled = (
                        math.hypot(here[0] - self.pointer_was[0],
                                   here[1] - self.pointer_was[1])
                        <= CALL_SETTLED_PIXELS
                    )
                self.pointer_was = here
            self.walk_target = (
                here[0] - self.view.width // 2,
                here[1] - self.view.height // 2,
            )
            self._advance_errand(elapsed)
        else:
            self.motion_running = False
            return False
        return True

    def _advance_throw(self, now: float) -> None:
        """Carry a thrown pet along until it runs out of speed."""
        bounds = self._span()
        elapsed = max(0.001, min(0.2, now - self.thrown_at))
        self.thrown_at = now

        # Bounced off the screens themselves, not off the box around them: an
        # L-shaped layout has a corner with no screen behind it, and a
        # rectangle cannot say so -- a throw sailed straight into it.
        x, y = self.throw.step(
            self.sprite_x, self.sprite_y, bounds, elapsed, clamp=self._clamp_across
        )
        self._place_sprite(int(round(x)), int(round(y)), across_screens=True)

        if self.throw.moving:
            # `jumping` reads as airborne; the walking rows would look like it
            # was strolling through the air.
            self.visual_state = "jumping" if self.view.animations.get("jumping") else "idle"
            return

        self.throw = None
        self.visual_state = "idle"
        self.frame_index = 0
        self.settings["position"] = [self.pos_x, self.pos_y]
        config.update(position=self.settings["position"])
        self._halt_walk(pause=1.0)

    def _abandon_errand(self) -> None:
        """Drop an errand that cannot be finished, and clear its bubble.

        Same tidy-up as arriving, without the wave and the "here": it did not
        arrive, so it does not celebrate. Clearing walk_target is what takes
        the "coming" bubble down.
        """
        self.walk_target = None
        self.pointer_was = None
        self.errand_at = None
        self.motion_running = False
        self._halt_walk(pause=1.0)
        trace("errand abandoned: pointer unreadable or overran")

    def _advance_errand(self, elapsed: float = 0.1) -> None:
        """Walk toward wherever it was called to, then stop just short."""
        # Clamped to a screen rather than to the bounding box of them all: on
        # an L-shaped layout the box has a corner with nothing behind it, and
        # aiming into it walks the pet off where it cannot be seen.
        target_x, target_y = self._clamp_across(
            int(self.walk_target[0]), int(self.walk_target[1])
        )
        remaining_x = target_x - self.sprite_x
        remaining_y = target_y - self.sprite_y
        distance = math.hypot(remaining_x, remaining_y)

        # Beside the pointer rather than under it: the sprite swallows
        # clicks, so parking on the cursor would put the pet in the way of
        # whatever you called it over to look at.
        if distance <= self.view.width / 2 + CALL_ARRIVAL_PIXELS and self.pointer_settled:
            self.walk_target = None
            self.pointer_was = None
            self.errand_at = None
            self.walking = 0
            self.visual_state = "idle"
            self.frame_index = 0
            # And then stay. Setting off wandering the instant it arrived
            # made the whole errand look like it had never stopped.
            self._halt_walk(pause=CALL_REST_SECONDS)
            self._react("waving")
            self._flash(self.labels["toast.arrived"], seconds=2.0)
            self.settings["position"] = [self.pos_x, self.pos_y]
            config.update(position=self.settings["position"])
            return

        if distance < 1e-6:
            # Already exactly on the target but the pointer has not settled,
            # so the arrival branch above did not fire. Nothing to walk; wait
            # for the pointer to move or settle. Dividing by this distance
            # took the overlay down (ZeroDivisionError in the log).
            return

        # Straight at you, not along one axis: the pointer is somewhere on a
        # screen, not somewhere on a line, and a pet that only ever slides
        # sideways is answering half the summons.
        direction = 1 if remaining_x >= 0 else -1
        # The wander's own pace with a little on top, so that walking to you
        # and walking about look like the same animal in a mild hurry.
        # Expressed as a rate because this runs on its own timer rather than
        # the animation frame.
        wander = max(1, int(self.settings.get("walk_speed") or 3)) * max(
            1, int(self.settings.get("fps") or 10)
        )
        speed = wander * self._number("call_pace", CALL_PACE)
        step = speed * elapsed
        self.walking = direction
        # Only left and right exist as poses, so the horizontal part of the
        # journey picks which one; a pack has nothing for walking upwards.
        self.visual_state = "running-right" if direction > 0 else "running-left"
        if self.errand_at is None or math.hypot(
            self.errand_at[0] - self.sprite_x, self.errand_at[1] - self.sprite_y
        ) > self.view.width:
            # Resynced when they drift: placement clamps to the screen, and
            # an accumulator that kept walking past a wall would leave the
            # pet stuck against it, silently working off a debt of pixels.
            self.errand_at = (float(self.sprite_x), float(self.sprite_y))
        moved_x = self.errand_at[0] + step * remaining_x / distance
        moved_y = self.errand_at[1] + step * remaining_y / distance
        self.errand_at = (moved_x, moved_y)
        self._place_sprite(int(round(moved_x)), int(round(moved_y)), across_screens=True)

    def _pointer_position(self) -> tuple[int, int] | None:
        display = Gdk.Display.get_default()
        seat = display.get_default_seat() if display else None
        pointer = seat.get_pointer() if seat else None
        if pointer is None:
            return None
        _screen, x, y = pointer.get_position()
        return int(x), int(y)

    #: What the tuning page offers, and the bounds it offers it within.
    #: (settings key, low, high, step, decimals)
    TUNABLE = (
        ("throw_flick", 1000.0, 9000.0, 250.0, 0),
        ("throw_friction", 0.01, 0.5, 0.01, 2),
        ("throw_bounce", 0.0, 0.9, 0.05, 2),
        ("call_pace", 1.0, 6.0, 0.5, 1),
        ("call_seconds", 0.2, 2.0, 0.1, 1),
        ("call_size", 40.0, 300.0, 10.0, 0),
        ("call_roundness", 0.3, 0.9, 0.05, 2),
        ("star_size", 120.0, 500.0, 20.0, 0),
    )

    def _number(self, key: str, default: float) -> float:
        """A setting as a number, whatever ended up in the file.

        `set` used to store "0.08" as a string, and a slider fed a string
        raises rather than moving. Anything unreadable falls back to the
        default rather than taking the pet down.
        """
        try:
            return float(self.settings.get(key, default))
        except (TypeError, ValueError):
            return default

    def _rebuild_gestures(self) -> None:
        """Remake the throw and summons detectors from the current settings.

        Called on startup and whenever a slider moves, so tuning applies as
        you drag it rather than at the next restart -- which is the whole
        point of a slider over a config key.
        """
        self.flick = motion.Flick(
            self._number("throw_flick", motion.THROW_SPEED),
            self._number("throw_friction", motion.FRICTION),
            self._number("throw_bounce", motion.BOUNCE),
        )
        self.call_stroke = petting.Stroke(
            petting.CALL_TURN_RADIANS,
            int(self._number("call_size", petting.CALL_SPAN_PIXELS)),
            one_way=True,
            seconds=self._number("call_seconds", petting.CALL_SECONDS),
            roundness=self._number("call_roundness", petting.CALL_ROUNDNESS),
        )
        self.star_stroke = petting.Star(
            span_pixels=int(self._number("star_size", petting.STAR_SPAN_PIXELS)),
        )

    def _tune(self, key: str, value: float) -> None:
        """Apply a slider straight away, and save it shortly after.

        Dragging a slider fires continuously, and writing the config file on
        every step would be a few hundred writes for one adjustment. The
        behaviour changes immediately; the file catches up when you stop.
        """
        self.settings[key] = round(value, 3)
        self._rebuild_gestures()
        if self.tune_save is not None:
            GLib.source_remove(self.tune_save)
        self.tune_save = GLib.timeout_add(500, self._save_tuning)

    def _save_tuning(self) -> bool:
        self.tune_save = None
        config.update(**{key: self.settings[key] for key, *_rest in self.TUNABLE})
        return False

    def _tune_size(self, value: float) -> None:
        """Resize the pet from the size slider, debounced.

        Unlike the gesture sliders, this rebuilds the scaled frames and the
        window, so it is not run on every event of a drag; it settles first.
        """
        if self.size_apply is not None:
            GLib.source_remove(self.size_apply)
        self.size_apply = GLib.timeout_add(
            120, _guard(lambda: self._apply_size(int(round(value))), keep=False)
        )

    def _apply_size(self, height: int) -> bool:
        """Rescale the pet to `height` pixels tall, live, without a restart.

        The pet is kept anchored by its feet -- the sprite's bottom centre stays
        put -- so growing it does not shove it across the screen or off the
        floor, and the window is resized and clamped to match.
        """
        self.size_apply = None
        height = max(SIZE_MIN, min(SIZE_MAX, int(height)))
        if height != self.view.height:
            old_centre_x = self.sprite_x + self.view.width // 2
            old_bottom_y = self.sprite_y + self.view.height
            try:
                self.view = PetView(self.view.pet, height)
            except sprites.SpriteError:
                return False

            self.window_width = max(self.view.width, BUBBLE_WIDTH)
            self.window_height = self.view.height + BUBBLE_GAP + 78
            self.sprite_left = (self.window_width - self.view.width) // 2
            self.bubble_space = self.window_height - self.view.height
            self.sprite_top = 0 if self.bubble_below else self.bubble_space
            self.set_size_request(self.window_width, self.window_height)
            self.set_default_size(self.window_width, self.window_height)
            self.resize(self.window_width, self.window_height)

            self._place_sprite(
                old_centre_x - self.view.width // 2, old_bottom_y - self.view.height
            )
            self._apply_input_shape()
            self.queue_draw()

        self.settings["height"] = height
        config.update(height=height)
        return False

    def _slider_row(self, key: str, low: float, high: float,
                    step: float, digits: int) -> Gtk.Widget:
        """One labelled slider, applied as it moves."""
        holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        holder.set_margin_start(12)
        holder.set_margin_end(12)
        label = Gtk.Label(label=self.labels[f"tune.{key}"])
        label.set_alignment(0.0, 0.5)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, low, high, step)
        scale.set_digits(digits)
        scale.set_value(self._number(key, config.DEFAULTS[key]))
        scale.set_size_request(260, -1)
        scale.set_value_pos(Gtk.PositionType.RIGHT)
        scale.connect("value-changed", lambda widget: self._tune(key, widget.get_value()))
        holder.pack_start(label, False, False, 0)
        holder.pack_start(scale, False, False, 0)
        holder.scale = scale
        holder.tune_key = key
        return holder

    def _size_row(self) -> Gtk.Widget:
        """The pet-size slider. Separate from the gesture sliders because it
        rebuilds the view rather than the gesture detectors."""
        holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        holder.set_margin_start(12)
        holder.set_margin_end(12)
        label = Gtk.Label(label=self.labels["tune.size"])
        label.set_alignment(0.0, 0.5)
        scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, SIZE_MIN, SIZE_MAX, SIZE_STEP
        )
        scale.set_digits(0)
        scale.set_value(self._number("height", config.DEFAULTS["height"]))
        scale.set_size_request(260, -1)
        scale.set_value_pos(Gtk.PositionType.RIGHT)
        scale.connect("value-changed", lambda widget: self._tune_size(widget.get_value()))
        holder.pack_start(label, False, False, 0)
        holder.pack_start(scale, False, False, 0)
        self._size_scale = scale
        return holder

    def _open_tuning(self) -> None:
        """A window of sliders, not a page of them.

        It was a page in the pet's own popup, which worked there and could
        never work in the status bar: that menu is serialised over DBusMenu and
        has no slider to serialise, so the entry opened an empty submenu. A
        window is drawn by us either way, is not clipped by the panel, and can
        be left open beside the pet while you drag things and watch what
        happens.
        """
        if self.tuning is not None:
            self.tuning.present()
            return

        window = Gtk.Window(title=self.labels["menu.tuning"].rstrip("… "))
        window.set_keep_above(True)
        window.set_resizable(False)
        window.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(10)
        box.set_margin_bottom(10)
        # Size first: it is the one most people came here to change.
        box.pack_start(self._size_row(), False, False, 2)
        box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 4)
        rows = [self._slider_row(key, low, high, step, digits)
                for key, low, high, step, digits in self.TUNABLE]
        for holder in rows:
            box.pack_start(holder, False, False, 2)
        box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 4)

        reset = self._menu_row(self.labels["menu.tune_reset"],
                               lambda _b: self._reset_tuning(rows))
        box.pack_start(reset, False, False, 0)
        window.add(box)

        def forget(*_args) -> bool:
            self.tuning = None
            self._size_scale = None
            return False

        window.connect("destroy", forget)
        window.show_all()
        # The pet refuses focus on purpose, and a window transient for it would
        # inherit that; this one needs the keyboard for arrow keys on a slider.
        window.set_accept_focus(True)
        window.present()
        self.tuning = window

    def _reset_tuning(self, rows) -> None:
        for key, *_rest in self.TUNABLE:
            self.settings[key] = config.DEFAULTS[key]
        self._rebuild_gestures()
        self._save_tuning()
        # Size resets too -- it lives in the same window and "defaults" should
        # mean all of it. Moving the slider fires value-changed, which resizes.
        if self._size_scale is not None:
            self._size_scale.set_value(config.DEFAULTS["height"])
        else:
            self._apply_size(config.DEFAULTS["height"])
        # Move the sliders themselves, or the window would show the old values
        # while the pet behaved by the new ones.
        for holder in rows:
            holder.scale.set_value(self._number(holder.tune_key,
                                                config.DEFAULTS[holder.tune_key]))

    def _trusted_pointer_position(self) -> tuple[int, int] | None:
        """The pointer, but only when X11 still knows where it is.

        On Wayland it stops being told once the pointer moves onto a
        Wayland-native window, and reports the place it was last seen
        instead. Read as a gesture that is a stream of identical samples,
        which is harmless; read as somewhere to walk to, it is a pet
        marching to a spot the pointer left -- which is exactly how this was
        described, the pet behaving as though the pointer had stopped where
        it crossed between screens.
        """
        return pointer_visibility.trusted_position(self._pointer_position)

    def _listen_for_a_call(self) -> bool:
        """Watch the pointer, anywhere on screen, for being waved at.

        A keyboard shortcut would be the obvious way to call a pet, and is not
        available: global shortcuts need a desktop portal this Wayland session
        does not provide, and the window refuses focus on purpose so that an
        always-on-top pet never swallows a keystroke. The pointer, though, can
        be read wherever it is -- which is already how a v2 pack watches where
        to look.

        So the gesture is the petting one, moved: rubbing *on* the pet is
        affection, waving *away from* it is a summons. Same detector, and the
        same reason it is a wave rather than a pause -- a resting pointer is
        someone reading, and a pet that came every time you stopped to read
        would be unbearable.
        """
        wants_call = bool(self.settings.get("call", True))
        wants_star = bool(self.settings.get("teleport", True))
        if not (wants_call or wants_star) or self.dragging or self.throw is not None:
            self.call_stroke.reset()
            self.star_stroke.reset()
            return True

        now = time.monotonic()
        position = self._trusted_pointer_position()
        if position is None:
            # Over a window X11 cannot see. Whatever was part-drawn is lost,
            # which is better than finishing it with coordinates that have
            # stopped moving.
            self.call_stroke.reset()
            self.star_stroke.reset()
            return True
        x, y = position

        # Only the sprite itself is excluded, plus a little. Petting arrives
        # as motion events over the pet, which the window's input shape
        # already confines to the sprite -- so a wide exclusion zone bought
        # nothing and swallowed the natural thing to do, which is to wave at
        # the pet from just beside it.
        margin = CALL_ARRIVAL_PIXELS
        if (self.sprite_x - margin <= x <= self.sprite_x + self.view.width + margin
                and self.sprite_y - margin <= y <= self.sprite_y + self.view.height + margin):
            self.call_stroke.reset()
            self.star_stroke.reset()
            return True

        # The star first. The two cannot really be confused -- a star's
        # corners are excluded from the turning a circle is judged by, so a
        # star accumulates almost none of it -- but a sloppy one drawn very
        # fast could reach both, and appearing where it was drawn is the more
        # specific of the two requests.
        if wants_star and self.star_stroke.feed(x, now, y):
            self.teleport_to(x, y)
        elif wants_call and self.call_stroke.feed(x, now, y):
            self.come_here(x, y)
        return True

    def teleport_to(self, x: int, y: int) -> None:
        """Appear where the star was drawn, rather than walking there.

        The other half of being called, and the reason both exist: crossing a
        wide desk on foot is the charm of it most of the time and a wait the
        rest of the time. A star is more trouble to draw than a circle, which
        is about right for the shortcut.
        """
        self.throw = None
        self.walk_target = None
        self.errand_at = None
        self.pointer_was = None
        self._place_sprite(
            int(x) - self.view.width // 2,
            int(y) - self.view.height // 2,
            across_screens=True,
        )
        self.settings["position"] = [self.pos_x, self.pos_y]
        config.update(position=self.settings["position"])
        self._halt_walk(pause=CALL_REST_SECONDS)
        self._react("waving")
        self._flash(self.labels["toast.teleported"], seconds=2.0)
        trace(f"teleported to x={x} y={y}")

    def come_here(self, x: int | None = None, y: int | None = None) -> None:
        """Send the pet to the pointer, or to a given place."""
        if x is None or y is None:
            position = self._pointer_position()
            if position is None:
                return
            x, y = position
        self.throw = None
        self.walk_target = (
            int(x) - self.view.width // 2,
            int(y) - self.view.height // 2,
        )
        self.errand_at = None
        self.pointer_was = None
        self.pointer_settled = True
        self.called_at = time.monotonic()
        self.errand_seen_at = self.called_at
        # Answer before setting off. Crossing a wide desk takes a moment
        # even at speed, and a gesture with no acknowledgement for a second
        # and a half is one you assume did not work.
        # Answer visibly: a wave, and then the bubble says so for as long as
        # the walk takes. A gesture you cannot tell was heard is one you
        # assume did not work.
        self._react("waving")
        self._start_motion()
        trace(f"called to x={x}")

    def _update_look(self) -> None:
        """Face the pointer while idle, using the v2 look sweep."""
        self.look_index = None
        if not (self.view.looks and self.settings.get("look_at_mouse", True)):
            return
        if self.visual_state != "idle" or self.walking:
            return

        display = Gdk.Display.get_default()
        seat = display.get_default_seat() if display else None
        pointer = seat.get_pointer() if seat else None
        if pointer is None:
            return
        _screen, pointer_x, pointer_y = pointer.get_position()

        center_x = self.pos_x + self.sprite_left + self.view.width / 2
        center_y = self.pos_y + self.sprite_top + self.view.height / 2
        dx, dy = pointer_x - center_x, pointer_y - center_y
        if math.hypot(dx, dy) < self.view.width * 0.6:
            return  # pointer is on top of the pet; keep the neutral pose

        span = self.view.width * 6
        fraction = min(1.0, max(-1.0, dx / span))
        count = len(self.view.looks)
        self.look_index = min(count - 1, max(0, int(round((fraction + 1) / 2 * (count - 1)))))

    # --------------------------------------------------------------- drawing

    def _bubble_visible(self) -> bool:
        # Tied to the state rather than a timer: the state's own dwell already
        # decides how long it lasts, so the bubble cannot outlive its news.
        # Anything but idle is worth saying out loud -- while Claude works, the
        # tool name in the bubble is the most useful thing on screen.
        if self.bubble_pinned or time.monotonic() < self.flash_until:
            return True
        # Say so for the whole walk, not for a moment at the start of it.
        # A summoned pet crosses a wide desk slowly, and a three-second
        # acknowledgement followed by a minute of silent walking is one you
        # miss and then assume never happened.
        if self.walk_target is not None:
            return True
        # Already jumped to this exact alert: keep quiet until the state moves
        # on. Clicking to jump was you dealing with it, so the pet should stop
        # holding the "click to jump" bubble up for the rest of the dwell.
        if self.jumped_episode == (self.state, self.since):
            return False
        mode = str(self.settings.get("bubble") or "active")
        if mode == "never":
            return False
        if mode == "alerts":
            return self.state in NOTIFY_ON
        return self.state != "idle"

    def _bubble_text(self) -> str:
        if time.monotonic() < self.flash_until:
            return self.flash_text
        if self.walk_target is not None:
            return self.labels["toast.coming"]

        fallback = self.labels.get(self.state, self.state)
        phrase = self.phrase or fallback

        if "{tool}" in phrase:
            # The phrase is built around a tool name, so it needs one.
            text = phrase.format(tool=self.detail) if self.detail else fallback
        elif self.detail:
            text = f"{phrase} · {self.detail}"
        else:
            text = phrase

        # `waiting` carries Claude's own prose, which is long and already the
        # most useful thing on screen; leave it room.
        if self.state != "waiting":
            if self.project:
                text = f"{text} · {self.project}"
            elapsed = time.time() - self.since
            if self.state == "running" and elapsed >= ELAPSED_FLOOR_SECONDS:
                text = f"{text} · {int(elapsed // 60)}m"

        if self.sessions > 1:
            text = f"{text}  ({self.sessions} sessions)"
        if self.state in JUMPABLE and self.locator:
            text = f"{text}  ↩ {self.labels['jump.hint']}"
        return text

    def _on_draw(self, _widget, cr) -> bool:
        cr.set_operator(1)  # cairo.OPERATOR_SOURCE
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(2)  # cairo.OPERATOR_OVER

        if self._bubble_visible():
            self._draw_bubble(cr)

        if self.look_index is not None and self.view.looks:
            pixbuf = self.view.looks[self.look_index]
        else:
            frames = self.view.frames(self.visual_state)
            pixbuf = frames[self.frame_index % len(frames)]

        Gdk.cairo_set_source_pixbuf(cr, pixbuf, self.sprite_left, self.sprite_top)
        cr.paint()
        return False

    def _draw_bubble(self, cr) -> None:
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(Pango.FontDescription("Sans 10"))
        layout.set_width((BUBBLE_WIDTH - 24) * Pango.SCALE)
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        layout.set_height(-3)
        layout.set_text(self._bubble_text(), -1)
        _ink, logical = layout.get_pixel_extents()

        box_width = min(BUBBLE_WIDTH, logical.width + 24)
        box_height = logical.height + 16
        box_x = (self.window_width - box_width) / 2
        if self.bubble_below:
            # No room overhead -- the pet is at the top of the screen.
            box_y = min(
                self.sprite_top + self.view.height + BUBBLE_GAP,
                self.window_height - box_height,
            )
        else:
            box_y = max(0, self.sprite_top - BUBBLE_GAP - box_height)

        radius = 10
        cr.new_sub_path()
        cr.arc(box_x + box_width - radius, box_y + radius, radius, -math.pi / 2, 0)
        cr.arc(box_x + box_width - radius, box_y + box_height - radius, radius, 0, math.pi / 2)
        cr.arc(box_x + radius, box_y + box_height - radius, radius, math.pi / 2, math.pi)
        cr.arc(box_x + radius, box_y + radius, radius, math.pi, 3 * math.pi / 2)
        cr.close_path()

        accent = {
            "waiting": (0.98, 0.72, 0.18),
            "failed": (0.93, 0.35, 0.35),
            "review": (0.42, 0.78, 0.52),
        }.get(self.state, (0.66, 0.68, 0.76))
        cr.set_source_rgba(0.09, 0.09, 0.12, 0.92)
        cr.fill_preserve()
        cr.set_source_rgba(*accent, 0.9)
        cr.set_line_width(1.5)
        cr.stroke()

        cr.set_source_rgba(0.94, 0.95, 0.97, 1.0)
        cr.move_to(box_x + 12, box_y + 8)
        PangoCairo.show_layout(cr, layout)

    # ----------------------------------------------------------------- input

    def _on_button_press(self, _widget, event) -> bool:
        if event.button == 3:
            self._show_menu(event)
            return True
        if event.button == 1 and event.type == Gdk.EventType.BUTTON_PRESS:
            # Held, not committed: the drag only starts once the pointer has
            # actually travelled, so a plain click stays available for jumping.
            self._halt_walk()
            self.press_origin = (int(event.x_root), int(event.y_root))
            # Reaching for the pet is not a summons. Without this, the
            # movement of going to grab it can finish a gesture, and the
            # call cancels the throw you were about to make.
            self.call_stroke.reset()
            self.walk_target = None
            return True
        return False

    def _halt_walk(self, pause: float = 0.0) -> None:
        """Stop mid-stride and stay put for at least `pause` seconds.

        Needed before any interaction: while walking, every animation tick calls
        move(), which fights the window manager's drag and yanks the pet back.
        Worse, the pet walks out from under the pointer, so the drag threshold
        is never crossed and it cannot be picked up at all.
        """
        if self.walking:
            self.walking = 0
            self.visual_state = "idle"
            self.frame_index = 0
        self.next_walk_at = max(
            self.next_walk_at, time.monotonic() + max(pause, WALK_PAUSE_RANGE[0])
        )

    def _on_motion(self, _widget, event) -> bool:
        """Carry the pet with the pointer.

        Done here rather than handing the window to the compositor with
        `begin_move_drag`, because the compositor moves the *window* and keeps
        it on screen -- so the pet stopped 86 pixels short of the top, that
        being the room the bubble takes up above it. Placing the sprite
        ourselves lets the bubble move to the other side at the same instant,
        with no jump, and keeps the pet on whichever monitor it is over.

        No pointer grab is taken. If the pointer outruns the pet the drag
        simply stops, which is a great deal better than a grab that escapes.
        """
        if not self.dragging:
            if self.press_origin is None:
                # Not a drag and not on the way to one: the pointer is just
                # over the pet, which is where being stroked happens.
                self._note_stroke(int(event.x_root), int(event.y_root))
                return False
            start_x, start_y = self.press_origin
            if math.hypot(event.x_root - start_x, event.y_root - start_y) < DRAG_THRESHOLD:
                return False
            self.press_origin = None
            self.dragging = True
            self.drag_offset = (int(start_x) - self.sprite_x, int(start_y) - self.sprite_y)
            self.stroke.reset()
            self.flick.clear()
            # Widen the target while dragging: the sprite is a small thing to
            # keep a pointer inside, and losing it mid-drag drops the pet.
            self._apply_input_shape()

        offset_x, offset_y = self.drag_offset or (0, 0)
        self._place_sprite(int(event.x_root) - offset_x, int(event.y_root) - offset_y)
        self.flick.record(event.x_root, event.y_root, time.monotonic())
        return True

    def _on_button_release(self, _widget, event) -> bool:
        if event.button != 1:
            return False
        if self.press_origin is not None:
            self.press_origin = None
            self._on_click()
        elif self.dragging:
            self._end_drag()
        return False

    def _end_drag(self) -> None:
        self.dragging = False
        self.drag_offset = None
        self._apply_input_shape()
        self._halt_walk()  # settle where it was dropped before wandering on

        # Let go slowly and the pet stays where it was put; flick it and it
        # keeps going. Speed at the moment of release is the whole
        # difference, which is why only the tail of the drag is measured.
        thrown = self.flick.release() if self.settings.get("throwing", True) else None
        trace(
            f"released at {self.flick.speed():.0f} px/s "
            f"(throw over {motion.THROW_SPEED:.0f}) -> "
            f"{'throw' if thrown else 'placed'}"
        )
        self.flick.clear()
        if thrown is not None:
            self.throw = thrown
            self.thrown_at = time.monotonic()
            self.walk_target = None
            self._start_motion()
            return  # position is saved where it lands, not where it left
        # Stored as the window corner, which is what earlier versions wrote and
        # what `_place_initial` still reads.
        self.settings["position"] = [self.pos_x, self.pos_y]
        config.update(position=self.settings["position"])

    def _on_click(self) -> None:
        """Take me to the session, or toggle the bubble if there is none.

        Any state, not just the alerting ones: "show me that terminal" is what
        people reach for the pet to do, whether or not Claude is asking.
        """
        # React either way, so the pet feels alive rather than inert.
        self._react("waving")

        if not self.locator:
            self.bubble_pinned = not self.bubble_pinned
            self._apply_input_shape()
            self.queue_draw()
            return

        result = jump.to_session(self.locator)
        self._flash(result.message)
        # Took you there: don't keep advertising the jump for this alert. Only
        # on success -- if it could not raise the window, the bubble stays so
        # the pending alert is not hidden behind a jump that did nothing.
        if result:
            self.jumped_episode = (self.state, self.since)

    def _note_stroke(self, x: int, y: int = 0) -> None:
        """Watch a hovering pointer for the back-and-forth of being stroked.

        Only the direction matters, not the distance: what separates stroking
        from crossing the sprite on the way elsewhere is that a stroke turns
        around. Small movements are ignored outright, so a hand resting on the
        mouse never adds up to affection.
        """
        if not self.settings.get("petting", True):
            return
        if self.stroke.feed(x, time.monotonic(), y):
            self._enjoy_petting()

    def _enjoy_petting(self) -> None:
        """React to being stroked, without pretending it changed anything.

        Deliberately a flash and an animation rather than a state: the pet's
        state belongs to what the agents are doing, and a pet that reported
        `waving` because it was tickled would be lying about the thing it
        exists to report.
        """
        self.petted_count += 1
        config.update(petted_count=self.petted_count)
        self._halt_walk(pause=2.0)  # stand still to be fussed over
        self._react("waving")
        self._flash(random.choice(self.phrases_petted), seconds=2.5)
        trace(f"petted ({self.petted_count} total)")

    def _react(self, name: str) -> None:
        """Play `name` once, then go back to whatever the state was showing."""
        if not self.view.animations.get(name):
            return
        self.visual_return = self.state
        self.visual_state = name
        self.frame_index = 0
        self.visual_until = time.monotonic() + self._row_duration(name)

    def _flash(self, message: str, seconds: float = 4.0) -> None:
        self.flash_text = message
        self.flash_until = time.monotonic() + seconds
        self._apply_input_shape()
        self.queue_draw()

    def _menu_row(self, label: str, callback, *, active: bool | None = None) -> Gtk.Widget:
        """One clickable line of the popup."""
        if active is None:
            button = Gtk.Button(label=label)
        else:
            button = Gtk.Button(label=("✓  " if active else "    ") + label)
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.set_alignment(0.0, 0.5)
        button.connect("clicked", callback)
        return button

    def _show_menu(self, event) -> None:
        """A plain window, deliberately not a Gtk.Menu.

        A GtkMenu is an override-redirect window holding a keyboard grab, and
        that grab is why clicking another application never dismissed it: focus
        cannot move while it is held, so no focus-out arrives, and a click on a
        Wayland surface never reaches an XWayland grab either. An ordinary
        focusable window is managed by the compositor and gets focus-out from
        any click, on X11 or Wayland alike.
        """
        if self.menu is not None:
            self.menu.destroy()
            self.menu = None

        popup = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        popup.set_decorated(False)
        popup.set_keep_above(True)
        popup.set_skip_taskbar_hint(True)
        popup.set_skip_pager_hint(True)
        popup.set_type_hint(Gdk.WindowTypeHint.POPUP_MENU)
        popup.set_resizable(False)
        # The pet is keep-above too, so without a parent relationship the window
        # manager is free to leave the menu underneath it. Transient windows
        # stack above the window they belong to.
        popup.set_transient_for(self)

        frame = Gtk.Frame()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.set_margin_top(4)
        box.set_margin_bottom(4)
        frame.add(box)
        popup.add(frame)
        popup.menu_box = box  # the page builders rebuild this in place

        self._render_page(popup, "main", event)

        popup.connect("focus-out-event", lambda *_: (self._dismiss(popup), False)[1])
        popup.connect(
            "key-press-event",
            lambda _w, key_event: (
                self._dismiss(popup) or True if key_event.keyval == Gdk.KEY_Escape else False
            ),
        )

        self.menu = popup
        self.menu_opened_at = time.monotonic()
        trace(f"menu popped, xid={_xid(popup)}")

    # ------------------------------------------------------------ menu pages
    #
    # Pages rather than submenus. Everything used to be one list, and with a
    # dozen packs installed the packs were most of it -- the settings were off
    # the bottom of a menu that existed to reach them. Nested popup windows
    # would each need their own dismissal handling, which took long enough to
    # get right once, so a page swaps the contents of the window already open.

    #: Every page there is. A name not on this list is a mistake, and used to
    #: draw the main page instead -- which looks like the page you asked for
    #: having the wrong contents.
    PAGES = ("main", "pets", "language", "behaviour")

    def _menu_model(self, page: str = "main") -> list[tuple]:
        """The menu, written down once for both places it gets drawn.

        The pet's popup and the status-bar menu are the same menu in two widget
        sets. Building each by hand let them drift: different order, a
        different word for the same command, and the tray quietly missing the
        update entry altogether.

        Entries are ("separator",) | ("caption", text) | ("submenu", label,
        page) | ("action", label, callback) | ("toggle", key, label, default) |
        ("update",).
        """
        if page == "pets":
            entries: list[tuple] = []
            for pet_id in config.discover():
                entries.append(("choice", pet_id, "pet", pet_id))
            entries.append(("separator",))
            entries.append(("action", self.labels["menu.browse"], self._open_gallery))
            entries.append(("action", self.labels["menu.install"], self._install_pack))
            entries.append(("action", self.labels["menu.install_file"], self._install_pack_from_file))
            entries.append(("action", self.labels["menu.remove"], self._remove_pack))
            return entries

        if page == "language":
            return [
                ("choice", self.labels[f"lang.{code}"], "language", code)
                for code in ("auto", "en", "ko")
            ]

        if page == "behaviour":
            # How it acts, all in one place. These grew one at a time onto the
            # top level until the settings you actually reach for were buried
            # under the ones you set once and forget.
            entries: list[tuple] = [
                ("toggle", "walk", self.labels["menu.walk"], True),
                ("toggle", "petting", self.labels["menu.petting"], True),
                ("toggle", "throwing", self.labels["menu.throwing"], True),
                ("toggle", "call", self.labels["menu.call"], True),
                ("toggle", "teleport", self.labels["menu.teleport"], True),
            ]
            # Only offered by a pack that has the poses for it.
            if self.view.looks:
                entries.append(("toggle", "look_at_mouse", self.labels["menu.look"], True))
            entries.append(("separator",))
            # An action rather than a page. Sliders cannot live in the status
            # bar's menu at all -- it is serialised over DBusMenu, which has no
            # such thing -- so the page there came out empty, which is how this
            # was reported. A window works from either menu and cannot be
            # clipped by the panel.
            entries.append(("action", self.labels["menu.tuning"], self._open_tuning))
            return entries

        toggles = [
            ("on_top", self.labels["menu.on_top"], True),
            ("notifications", self.labels["menu.notify"], False),
            ("autostart", self.labels["menu.autostart"], True),
            ("exit_when_no_sessions", self.labels["menu.exit_idle"], True),
        ]
        # Only offered where it means something. On a machine without the app
        # the row would be a switch wired to nothing.
        if desktop.installed():
            toggles.insert(2, ("desktop", self.labels["menu.desktop"], True))

        header = [("caption", f"{self.view.pet.display_name} · v{self.view.pet.version}")]
        usage_line = self._usage_caption()
        if usage_line:
            header.append(("caption", usage_line))
        return [
            *header,
            ("separator",),
            ("submenu", self.labels["menu.pets"], "pets"),
            ("submenu", self.labels["menu.language"], "language"),
            ("submenu", self.labels["menu.behaviour"], "behaviour"),
            ("separator",),
            # High up in both, because it is what you reach for when the pet is
            # somewhere you cannot get at it.
            # Stays at the top level on purpose: it is what you reach for
            # when the pet is somewhere you cannot click, and burying the
            # recovery a page deeper would defeat it.
            ("action", self.labels["menu.reset"], self.reset_position),
            ("separator",),
            *[("toggle", key, label, default) for key, label, default in toggles],
            ("separator",),
            ("update",),
            ("action", self.labels["menu.quit"], lambda: self.quit()),
        ]

    def _choose(self, kind: str, value: str) -> None:
        """Apply a pick from the pets or language list."""
        if kind == "pet":
            if value == self.view.pet.id:
                return
            self.settings["pet"] = value
            config.update(pet=value)
            self.quit(restart=True)
        elif kind == "language":
            if value == str(self.settings.get("language") or "auto"):
                return
            self._apply_language(value)
            self._refresh_tray()

    def _is_chosen(self, kind: str, value: str) -> bool:
        if kind == "pet":
            return value == self.view.pet.id
        return value == str(self.settings.get("language") or "auto")

    def _toggle_setting(self, key: str) -> None:
        self.settings[key] = not self.settings.get(key)
        config.update(**{key: self.settings[key]})
        if key == "desktop":
            self._apply_desktop_setting()
            return
        if key == "on_top":
            self.set_keep_above(bool(self.settings[key]))
        self._refresh_tray()

    def _render_page(self, popup, page: str, event=None) -> None:
        box = popup.menu_box
        for child in box.get_children():
            box.remove(child)

        def close(*_args) -> None:
            self._dismiss(popup)

        def go(target: str):
            return lambda *_a: self._render_page(popup, target)

        def separator() -> None:
            box.pack_start(
                Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 2
            )

        def caption(text: str) -> None:
            label = Gtk.Label(label=text)
            label.set_sensitive(False)
            label.set_margin_start(12)
            label.set_margin_end(12)
            label.set_margin_bottom(4)
            label.set_alignment(0.0, 0.5)
            box.pack_start(label, False, False, 0)

        def row(label: str, callback, *, active: bool | None = None) -> None:
            box.pack_start(self._menu_row(label, callback, active=active), False, False, 0)

        if page != "main":
            row(self.labels["menu.back"], go("main"))
            separator()

        for entry in self._menu_model(page):
            kind = entry[0]
            if kind == "separator":
                separator()
            elif kind == "caption":
                caption(entry[1])
            elif kind == "submenu":
                row(entry[1], go(entry[2]))
            elif kind == "action":
                row(entry[1], lambda _b, run=entry[2]: (close(), run()))
            elif kind == "toggle":
                key, label, default = entry[1], entry[2], entry[3]
                row(
                    label,
                    lambda _b, name=key: (self._toggle_setting(name), close()),
                    active=bool(self.settings.get(key, default)),
                )
            elif kind == "choice":
                label, group, value = entry[1], entry[2], entry[3]
                row(
                    label,
                    lambda _b, g=group, v=value: (close(), self._choose(g, v)),
                    active=self._is_chosen(group, value),
                )
            elif kind == "update":
                box.pack_start(self._update_row(close), False, False, 0)

        popup.show_all()
        # The page just changed shape; let the window shrink to fit it again
        # rather than keep the tallest page's height.
        popup.resize(1, 1)
        self._place_menu(popup, event)
        popup.present()
        surface = popup.get_window()
        if surface is not None:
            surface.raise_()

    def _apply_language(self, choice: str) -> None:
        """Switch the bubble's language without a restart.

        Only the label and phrase tables depend on it, and both are re-read
        here -- a restart would cost the pet its place on screen and its
        current state for what is a change of vocabulary.
        """
        self.settings["language"] = choice
        config.update(language=choice)
        self.labels = resolve_labels(choice)
        self.phrases = resolve_phrases(choice)
        self.phrases_petted = resolve_petted(choice)
        options = self.phrases.get(self.state) or (self.labels.get(self.state, self.state),)
        self.phrase = random.choice(options)
        self.queue_draw()

    # ------------------------------------------------------- pack management

    def _in_background(self, work, done) -> None:
        """Run `work()` off the main loop and hand its result to `done()`.

        Installing and update-checking both hit the network, and a menu that
        freezes while that happens is worse than no menu.
        """

        def runner() -> None:
            try:
                result, error = work(), None
            except Exception as exc:  # noqa: BLE001 - reported, never raised at the user
                result, error = None, exc
            GLib.idle_add(done, result, error)

        threading.Thread(target=runner, daemon=True).start()

    def _ask(self, prompt: str, entry: bool) -> str | None:
        """A small modal. Returns the text, "" for a bare confirm, or None."""
        dialog = Gtk.Dialog(transient_for=self, modal=True)
        dialog.set_decorated(False)
        dialog.set_keep_above(True)
        dialog.add_button(self.labels["dialog.cancel"], Gtk.ResponseType.CANCEL)
        dialog.add_button(self.labels["dialog.ok"], Gtk.ResponseType.OK)
        # Enter confirms only where there is something to type. A stray Return
        # over a confirmation must not delete a pack -- which is exactly how I
        # lost one while testing this.
        dialog.set_default_response(Gtk.ResponseType.OK if entry else Gtk.ResponseType.CANCEL)

        content = dialog.get_content_area()
        content.set_margin_top(12)
        content.set_margin_bottom(8)
        content.set_margin_start(12)
        content.set_margin_end(12)
        content.set_spacing(8)
        label = Gtk.Label(label=prompt)
        label.set_line_wrap(True)
        label.set_max_width_chars(40)
        content.pack_start(label, False, False, 0)

        field = None
        if entry:
            field = Gtk.Entry()
            field.set_activates_default(True)
            content.pack_start(field, False, False, 0)

        dialog.show_all()
        # The pet window refuses focus, and a dialog transient for it inherits
        # that problem: without presenting it explicitly the keyboard never
        # reaches the entry, so the dialog returns empty and nothing happens.
        dialog.set_accept_focus(True)
        dialog.present()
        if field is not None:
            field.grab_focus()

        response = dialog.run()
        text = field.get_text().strip() if field is not None else ""
        dialog.destroy()
        trace(f"dialog response={response} text={text!r} focused={dialog.has_toplevel_focus()}")
        return text if response == Gtk.ResponseType.OK else None

    def _install_pack(self) -> None:
        from . import registry

        answer = self._ask(self.labels["dialog.install"], entry=True)
        if not answer:
            return

        # Accept a bare id or anything ending in one, so a copied gallery link
        # works: https://codex-pets.net/#/pets/doro-v2-roshan
        pet_id = answer.rstrip("/").split("/")[-1].split("?")[0].strip()
        self.busy = pet_id
        self._flash(f"{pet_id}…", seconds=120)

        def work():
            directory = registry.install(pet_id, config.claude_home() / "pets")
            sprites.load_pet(directory["directory"])
            return directory["id"]

        def done(installed, error):
            self.busy = ""
            if error is not None:
                self._flash(self.labels["toast.failed"].format(reason=error))
                return False
            self.settings["pet"] = installed
            config.update(pet=installed)
            self._flash(self.labels["toast.installed"].format(pet=installed))
            self.quit(restart=True)
            return False

        self._in_background(work, done)

    def _install_pack_from_file(self) -> None:
        """Install a pack from a .codex-pet.zip picked off disk.

        The counterpart to the gallery installer for a pack someone was handed
        as a file. A file chooser rather than a typed path: nobody wants to type
        an absolute path into a tiny entry, and the chooser cannot get it wrong.
        """
        from . import registry

        path = self._ask_file()
        if not path:
            return

        name = Path(path).name
        self.busy = name
        self._flash(f"{name}…", seconds=120)

        def work():
            installed = registry.install_local(path, config.claude_home() / "pets")
            sprites.load_pet(installed["directory"])
            return installed["id"]

        def done(installed, error):
            self.busy = ""
            if error is not None:
                self._flash(self.labels["toast.failed"].format(reason=error))
                return False
            self.settings["pet"] = installed
            config.update(pet=installed)
            self._flash(self.labels["toast.installed"].format(pet=installed))
            self.quit(restart=True)
            return False

        self._in_background(work, done)

    def _ask_file(self) -> str | None:
        """A modal file chooser for a .codex-pet.zip. Returns a path or None."""
        dialog = Gtk.FileChooserDialog(
            title=self.labels["menu.install_file"].rstrip("… "),
            transient_for=self,
            action=Gtk.FileChooserAction.OPEN,
        )
        dialog.set_keep_above(True)
        dialog.add_button(self.labels["dialog.cancel"], Gtk.ResponseType.CANCEL)
        dialog.add_button(self.labels["dialog.ok"], Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)

        zips = Gtk.FileFilter()
        zips.set_name("*.zip")
        zips.add_pattern("*.zip")
        dialog.add_filter(zips)

        downloads = Path("~/Downloads").expanduser()
        if downloads.is_dir():
            dialog.set_current_folder(str(downloads))

        # The pet window refuses focus and a transient dialog inherits it, so
        # the chooser has to be presented explicitly to take the keyboard.
        dialog.show_all()
        dialog.set_accept_focus(True)
        dialog.present()
        response = dialog.run()
        path = dialog.get_filename() if response == Gtk.ResponseType.OK else None
        dialog.destroy()
        return path

    def _remove_pack(self) -> None:
        import shutil

        pet_id = self.view.pet.id
        directory = self.view.pet.directory
        if config.bundled_pets() in directory.parents:
            self._flash(self.labels["toast.failed"].format(reason="bundled pack"))
            return
        if self._ask(self.labels["dialog.remove"].format(pet=pet_id), entry=False) is None:
            return

        for root in config.pet_search_paths():
            candidate = root / pet_id
            if root != config.bundled_pets() and (candidate / "pet.json").is_file():
                try:
                    shutil.rmtree(candidate)
                except OSError as exc:
                    self._flash(self.labels["toast.failed"].format(reason=exc))
                    return
        config.update(pet=None)
        self.settings["pet"] = None
        self._flash(self.labels["toast.removed"].format(pet=pet_id))
        self.quit(restart=True)

    # --------------------------------------------------------------- updating

    def _usage_caption(self) -> str | None:
        """The menu's usage line, or None when there is nothing to show yet.

        Read straight from `usage.read()`; nothing is computed. Absent means no
        statusLine has reported a limit yet -- a fresh account, or no source at
        all -- in which case the line is simply left out.
        """
        if not self.settings.get("usage", True):
            return None
        from . import usage

        figures = usage.read()
        if not figures or figures.get("five_hour_pct") is None:
            return None
        cost = figures.get("cost_usd")
        if cost is not None:
            return self.labels["usage.line_cost"].format(
                five=figures["five_hour_pct"],
                seven=figures.get("seven_day_pct") if figures.get("seven_day_pct") is not None else "?",
                cost=f"{cost:.2f}",
            )
        return self.labels["usage.line"].format(
            five=figures["five_hour_pct"],
            seven=figures.get("seven_day_pct") if figures.get("seven_day_pct") is not None else "?",
        )

    def _check_usage_once(self) -> bool:
        """Warn once per 5h window when its limit crosses the threshold."""
        if not self.settings.get("usage", True):
            return False
        from . import usage

        figures = usage.read()
        if not figures:
            return False
        pct = figures.get("five_hour_pct")
        window = figures.get("five_hour_resets_at")
        try:
            threshold = int(self.settings.get("usage_warn_percent") or 90)
        except (TypeError, ValueError):
            threshold = 90
        if pct is None or pct < threshold:
            return False
        # Once per window: keyed by the window's reset time, so a new 5h window
        # (a different resets_at) is warned about afresh, and the same one is
        # not nagged about every couple of minutes.
        if window is not None and window == self.usage_warned_window:
            return False
        self.usage_warned_window = window
        self._flash(self.labels["toast.usage_warn"].format(five=pct), seconds=6.0)
        return False

    def _remind_bridge_relogin(self) -> bool:
        """Lay the pointer bridge down if it is missing, and say if a login is due.

        Done here because here is the one place that catches everybody. Whatever
        `update` learns to do, the person updating *from* an older version runs
        that older version's updater -- so nothing added to the update path
        reaches anyone who has not already got it. The pet, though, restarts
        onto the new code after every update, so this runs for all of them: the
        `.deb` whose autostart only ever called `install-hooks`, the tarball,
        the clone.

        Installing cannot activate it -- GNOME loads extensions only at login --
        so whenever the copy on disk is not answering, the pet says so. The
        terminal paths print the same thing; a menu-driven update has no
        terminal, and this is the only channel it has.
        """
        try:
            # The usage capture rides along for the same reason: someone
            # updating from an older version runs that version's updater, and
            # the pet restarting onto the new code is the one moment that
            # reaches everybody. Idempotent, and a no-op for oh-my-claudecode
            # users, whose cache we read as-is.
            from . import cli, config as config_module

            cli._install_statusline(config_module.claude_home() / "settings.json")
        except Exception:  # noqa: BLE001
            pass
        try:
            from . import shellext

            if not shellext.supported_here():
                return False
            shellext.ensure()
            if shellext.installed() and not shellext.active():
                self._flash(self.labels["toast.bridge_relogin"], seconds=8.0)
        except Exception:  # noqa: BLE001 -- never let this trouble the overlay
            pass
        return False

    def _schedule_update_check(self) -> None:
        if not self.settings.get("update_check", True):
            return
        GLib.timeout_add_seconds(20, self._run_update_check)
        # Returning True keeps this one repeating; the first is a one-shot.
        GLib.timeout_add_seconds(6 * 3600, lambda: self._run_update_check() or True)

    def _run_update_check(self) -> bool:
        from . import update

        def done(info, error):
            if error is None:
                self.update_info = info
                trace(f"update check: {info}")
            else:
                trace(f"update check failed: {error}")
            return False

        self._in_background(update.check, done)
        return False  # one-shot; the 6h timer re-arms itself below

    def _check_updates_now(self) -> None:
        self._flash(self.labels["menu.update_check"])

        from . import update

        def done(info, error):
            if error is not None:
                self._flash(self.labels["toast.failed"].format(reason=error))
                return False
            self.update_info = info
            key = "menu.update_available" if info["available"] else "menu.update_current"
            self._flash(self.labels[key].format(version=info["latest"]))
            return False

        self._in_background(update.check, done)

    def _apply_update(self) -> None:
        """Run the update and report how it went.

        A git checkout that succeeds respawns the overlay from under us, so a
        success there is never seen here -- but a git checkout that *cannot*
        fast-forward (local commits not pushed, which is the common case for
        whoever is hacking on this) exits without restarting anything, and the
        "updating…" toast used to sit there for its full minute saying nothing
        happened when something had: it had failed. So the outcome is waited
        for and the toast replaced with what actually occurred.

        A packaged install hands the new .deb to the system installer, which
        pops its own authority prompt; that path is left to the detached
        process since it outlives us by design.
        """
        from . import update

        # A packaged install cannot rewrite /usr itself; hand it to the
        # detached updater, which pops the system installer's authority prompt
        # and outlives us. This one path stays fire-and-forget by necessity.
        if update.is_system_install():
            self._flash(self.labels["menu.updating"], seconds=120)
            self._apply_update_detached()
            return

        self._flash(self.labels["menu.updating"], seconds=120)

        def work():
            return update.apply()

        def done(outcome, error):
            if error is not None or outcome == "failed":
                self._flash(self.labels["toast.update_failed"])
            elif outcome == "current":
                self._flash(self.labels["toast.uptodate"])
            else:
                # The files changed underneath us; restart onto them. Same
                # exec the menu's restart uses, so the pet comes back on the
                # new code with its place and state.
                self.quit(restart=True)
            return False

        self._in_background(work, done)

    def _apply_update_detached(self) -> None:
        from . import launch

        try:
            subprocess.Popen(  # noqa: S603 - fixed argv
                [str(launch.launcher_path()), "update"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                cwd=str(launch.project_root()),
            )
        except OSError as exc:
            self._flash(self.labels["toast.failed"].format(reason=exc))

    def _update_label(self) -> str:
        """What the update entry says, given what the last check found."""
        info = self.update_info
        if info is None:
            return self.labels["menu.update_check"]
        if info["available"]:
            return self.labels["menu.update_available"].format(version=info["latest"])
        return self.labels["menu.update_current"]

    def _update_activate(self) -> None:
        info = self.update_info
        if info is not None and info["available"]:
            self._apply_update()
        else:
            self._check_updates_now()

    def _update_row(self, close) -> Gtk.Widget:
        return self._menu_row(
            self._update_label(), lambda _b: (close(), self._update_activate())
        )

    def _open_gallery(self) -> None:
        """Open the pack gallery in the user's browser."""
        from . import registry

        self._open_url(registry.api_base())

    def _open_url(self, url: str) -> None:
        try:
            Gtk.show_uri_on_window(None, url, Gdk.CURRENT_TIME)
            trace(f"opened {url}")
            return
        except GLib.Error as exc:
            trace(f"show_uri failed ({exc}); falling back to xdg-open")
        try:
            subprocess.Popen(  # noqa: S603,S607 - fixed argv
                ["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except OSError:
            self._flash(f"could not open {url}")

    def _place_menu(self, popup: Gtk.Window, event) -> None:
        """Put the popup above the pet, kept inside the work area.

        Measured by what the contents ask for, not by what the window
        currently is. `get_size()` reports the size it has been given, and a
        page that has just been swapped in has not been given one yet -- it
        answered 190x417 for every page, including the tuning page that
        actually wants 244x477. Placing a window 77 pixels shorter than it is
        put it through the panel whenever the pet was near the top of the
        screen, which is how it was reported.
        """
        _minimum, natural = popup.get_preferred_size()
        width, height = natural.width, natural.height
        area = self._workarea()
        if event is None and getattr(popup, "menu_x", None) is not None:
            # A page change, not a fresh right-click. Re-centring on the pointer
            # would shuffle the window sideways under the hand about to click.
            x = popup.menu_x
        else:
            x = int(getattr(event, "x_root", self.sprite_x)) - width // 2
        y = self.sprite_y - height - 4
        if y < area.y:
            # No room above -- the pet is at the top of the screen. Better
            # under it than pinned to the panel and covering the pet.
            y = self.sprite_y + self.view.height + 4
        x = min(max(x, area.x), area.x + area.width - width)
        y = min(max(y, area.y), area.y + area.height - height)
        popup.menu_x = x
        popup.move(x, y)

    def _dismiss(self, popup) -> None:
        """Close the popup for good.

        It is an ordinary window now, so hiding it is enough -- but it is
        destroyed anyway, since a fresh one is built for every right-click and a
        leftover would linger on screen.
        """
        if popup is None:
            return
        trace(f"closing the menu xid={_xid(popup)}")
        popup.hide()
        popup.destroy()
        if self.menu is popup:
            self.menu = None
        Gdk.Display.get_default().flush()

    # ---------------------------------------------------------------- teardown

    def quit(self, restart: bool = False) -> None:
        self._stop_desktop_watch()
        if self.tray is not None:
            self.tray.stop()
            self.tray = None
        try:
            state.pid_path().unlink()
        except OSError:
            pass
        if restart:
            try:
                os.execv(sys.executable, [sys.executable, "-m", "claude_pet", "run"])
            except OSError:
                pass
        Gtk.main_quit()


def demo(pet_id: str | None = None, seconds: float = 2.0) -> int:
    """Cycle the live window through every animation row the pack provides.

    The desktop equivalent of the state buttons on a pack's gallery page: it is
    how you check that a pack drew all nine rows before trusting it.
    """
    if not os.environ.get("DISPLAY"):
        print("claude-pet: no DISPLAY", file=sys.stderr)
        return 2

    settings = dict(config.load())
    settings.update({"walk": False, "notifications": False, "look_at_mouse": False})
    if pet_id:
        settings["pet"] = pet_id

    directory = config.active_pet_dir(settings)
    if directory is None:
        print("claude-pet: no pet packs found", file=sys.stderr)
        return 2

    pet = sprites.load_pet(directory)
    view = PetView(pet, int(settings.get("height") or 132))
    window = Overlay(view, settings, poll=False)
    window.bubble_pinned = True

    rows = [name for name in sprites.ROW_STATES if view.animations.get(name)]
    print(
        f"claude-pet: {pet.id} v{pet.version} cycling {len(rows)} rows, Ctrl+C to stop",
        flush=True,
    )
    counter = {"index": 0}

    def advance() -> bool:
        name = rows[counter["index"] % len(rows)]
        counter["index"] += 1
        window.state = name
        window.visual_state = name
        window.detail = f"{len(view.animations[name])} frames"
        window.frame_index = 0
        window.visual_until = None
        window.visual_return = None
        window._apply_input_shape()
        print(f"  {name:<15} {len(view.animations[name])} frames", flush=True)
        return True

    advance()
    GLib.timeout_add(max(200, int(seconds * 1000)), advance)
    for number in (signal.SIGINT, signal.SIGTERM):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, number, lambda: (Gtk.main_quit(), True)[1])
    Gtk.main()
    return 0


def snapshot(
    output: str,
    pet_id: str | None = None,
    state_name: str | None = None,
    detail: str = "",
    delay_ms: int = 700,
) -> int:
    """Render the overlay offscreen-ish and dump its own window to a PNG.

    Used to verify drawing without a compositor screenshot API, which GNOME
    restricts to authorised callers.
    """
    if not os.environ.get("DISPLAY"):
        print("claude-pet: no DISPLAY", file=sys.stderr)
        return 2

    settings = dict(config.load())
    settings["walk"] = False
    settings["notifications"] = False
    if pet_id:
        settings["pet"] = pet_id

    directory = config.active_pet_dir(settings)
    if directory is None:
        print("claude-pet: no pet packs found", file=sys.stderr)
        return 2

    pet = sprites.load_pet(directory)
    view = PetView(pet, int(settings.get("height") or 132))
    # With no state forced, poll like the real overlay does, so a snapshot
    # shows whatever the live sessions are actually doing.
    window = Overlay(view, settings, poll=state_name is None)

    if state_name:
        window.state = window.visual_state = state_name
        window.detail = detail
        window.sessions = 1
        window.bubble_pinned = state_name in NOTIFY_ON
        window._apply_input_shape()

    destination = Path(output).expanduser()

    def grab() -> bool:
        surface = window.get_window()
        pixbuf = Gdk.pixbuf_get_from_window(
            surface, 0, 0, window.window_width, window.window_height
        )
        if pixbuf is None:
            print("claude-pet: window grab returned nothing", file=sys.stderr)
        else:
            pixbuf.savev(str(destination), "png", [], [])
            print(f"claude-pet: {pet.id} [{window.visual_state}] -> {destination}")
        Gtk.main_quit()
        return False

    GLib.timeout_add(delay_ms, grab)
    Gtk.main()
    return 0


def _claim_pidfile() -> bool:
    path = state.pid_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = int(path.read_text(encoding="utf-8").strip())
        if Path(f"/proc/{existing}").exists() and existing != os.getpid():
            return False
    except (OSError, ValueError):
        pass
    path.write_text(str(os.getpid()), encoding="utf-8")
    return True


def run(pet_id: str | None = None) -> int:
    """Launch the overlay. Returns a process exit code.

    `GDK_BACKEND=x11` is exported by the `claude-pet` launcher, because GTK
    resolves its backend before this module gets a say.
    """
    if not os.environ.get("DISPLAY"):
        print(
            "claude-pet: no DISPLAY. The overlay needs X11 or XWayland for always-on-top.",
            file=sys.stderr,
        )
        return 2

    settings = config.load()
    if pet_id:
        settings["pet"] = pet_id

    directory = config.active_pet_dir(settings)
    if directory is None:
        searched = ", ".join(str(path) for path in config.pet_search_paths())
        print(f"claude-pet: no pet packs found in {searched}", file=sys.stderr)
        print("claude-pet: install one with `claude-pet add <pet-id>`", file=sys.stderr)
        return 2

    pet = sprites.load_pet(directory)
    view = PetView(pet, int(settings.get("height") or 132))

    if not _claim_pidfile():
        print("claude-pet: overlay is already running", file=sys.stderr)
        return 0

    overlay = Overlay(view, settings)
    print(
        f"claude-pet: {pet.display_name} (v{pet.version}) on "
        f"{type(Gdk.Display.get_default()).__name__} at ({overlay.pos_x},{overlay.pos_y}) "
        f"sprite {view.width}x{view.height}",
        flush=True,
    )
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal_number, lambda: (overlay.quit(), True)[1])

    if DEBUG:
        # SIGUSR1 dismisses the menu, so the teardown can be exercised on demand
        # instead of waiting for a grab to break. Debug builds only.
        def _dismiss_on_signal() -> bool:
            if overlay.menu is not None:
                trace("SIGUSR1: dismissing the menu")
                overlay._dismiss(overlay.menu)
            else:
                trace("SIGUSR1: no menu open")
            return True

        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, _dismiss_on_signal)

    overlay._adopt(state.aggregate())
    Gtk.main()
    return 0
