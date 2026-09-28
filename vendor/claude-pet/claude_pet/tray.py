"""A status-bar icon, so the pet is reachable when the pet is not.

Every other way of controlling the pet goes through the pet: right-click it,
drag it, click it. That is fine until it is somewhere you cannot click -- off
the edge of a screen that has since been unplugged, or under a full-screen
window. Then there is no way back short of the terminal, which is exactly what
this project set out to avoid.

So the menu also lives somewhere that cannot wander off: the top bar. Reset
position is first, because that is what brings the pet back.

Optional in the strongest sense. The bindings ship separately
(`gir1.2-ayatanaappindicator3-0.1`), GNOME needs an extension to show tray
icons at all, and neither is worth a hard dependency for a desktop pet. Where
it cannot be had, `claude-pet reset-position` does the same job from a shell.
"""

from __future__ import annotations

from typing import Any, Callable

#: Tried in order. The Ayatana fork is what Debian and Ubuntu ship now; the
#: original name is kept for distributions still carrying it.
_BINDINGS = (("AyatanaAppIndicator3", "0.1"), ("AppIndicator3", "0.1"))


def _load() -> Any:
    import gi

    for name, version in _BINDINGS:
        try:
            gi.require_version(name, version)
        except ValueError:
            continue
        try:
            return getattr(__import__("gi.repository", fromlist=[name]), name)
        except (ImportError, AttributeError):
            continue
    return None


def available() -> bool:
    return _load() is not None


class Tray:
    """The pet's menu in the status bar.

    Built from a callback table rather than handed the overlay, so that what
    the tray can do to the pet stays written down in one place.
    """

    def __init__(self, icon_name: str, title: str) -> None:
        self.icon_name = icon_name
        self.title = title
        self._indicator: Any = None
        self._menu: Any = None
        self.error: str | None = None
        #: Directory holding `<icon_name>.png`. Set when the icon is not in the
        #: system theme, which is every install except the packaged one.
        self.icon_theme_path: str | None = None

    @property
    def active(self) -> bool:
        return self._indicator is not None

    def start(self, build: Callable[[], Any]) -> bool:
        """Show the icon, with the menu `build()` returns."""
        module = _load()
        if module is None:
            self.error = "no app indicator bindings (gir1.2-ayatanaappindicator3-0.1)"
            return False

        try:
            if self.icon_theme_path:
                indicator = module.Indicator.new_with_path(
                    "claude-pet",
                    self.icon_name,
                    module.IndicatorCategory.APPLICATION_STATUS,
                    self.icon_theme_path,
                )
            else:
                indicator = module.Indicator.new(
                    "claude-pet", self.icon_name, module.IndicatorCategory.APPLICATION_STATUS
                )
            indicator.set_status(module.IndicatorStatus.ACTIVE)
            indicator.set_title(self.title)
            menu = build()
            indicator.set_menu(menu)
        except Exception as exc:  # noqa: BLE001 - a tray is never worth a crash
            self.error = str(exc)
            return False

        self._indicator = indicator
        self._menu = menu
        return True

    def set_menu(self, menu: Any) -> None:
        """Swap in a rebuilt menu, after the pet list or a setting changed."""
        if self._indicator is None:
            return
        try:
            self._indicator.set_menu(menu)
            self._menu = menu
        except Exception:  # noqa: BLE001
            pass

    def set_icon(self, icon_name: str) -> None:
        if self._indicator is None:
            return
        try:
            self._indicator.set_icon_full(icon_name, self.title)
            self.icon_name = icon_name
        except Exception:  # noqa: BLE001
            pass

    def stop(self) -> None:
        indicator, self._indicator = self._indicator, None
        if indicator is None:
            return
        try:
            module = _load()
            if module is not None:
                indicator.set_status(module.IndicatorStatus.PASSIVE)
        except Exception:  # noqa: BLE001
            pass
