"""Claude Code hook bridge.

Invoked once per hook event with the event JSON on stdin. It runs inside every
Claude turn, so it stays stdlib-only, does no network or image work, and always
exits 0 -- a broken pet must never break a session.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from . import agents, config, launch, locate, state

#: Events on which the session's location is (re)recorded. Cheap -- a handful
#: of /proc reads -- but pointless on every tool call, and doing it on prompt
#: submit as well means sessions that predate `install-hooks` still get one.
LOCATE_ON = {"SessionStart", "UserPromptSubmit"}

#: Events that may bring the overlay back if it is not running.
AUTOSTART_ON = {"SessionStart", "UserPromptSubmit"}

#: Sentinel: report the detail but leave the state as it was.
KEEP = "__keep__"

#: `Notification` covers two situations that could hardly be less alike.
#:
#: Claude Code sends one when it is genuinely blocked -- it wants permission
#: and cannot go on without an answer -- and another about a minute after a
#: turn ends simply because nobody has typed since. Only the first is `needs
#: you`. The second arrives long after `Stop` has been reported and shown, and
#: `waiting` deliberately never times out, so treating them alike left the pet
#: demanding attention for the sole offence of you reading its output.
#:
#: Matching on the text is safe here in a way it is not for a desktop
#: notification: this is Claude Code's own fixed string, not localised prose
#: from another application.
IDLE_NOTIFICATION = "waiting for your input"

#: Hook event -> pet state. `None` means "forget this session", `KEEP` means
#: "this says nothing about whether Claude is working".
EVENT_STATES: dict[str, str | None] = {
    "SessionStart": "waving",
    "UserPromptSubmit": "running",
    "PreToolUse": "running",
    "PostToolUse": "running",
    "Notification": "waiting",
    "Stop": "review",
    # A subagent finishing does not mean the main agent is working -- and
    # background subagents finish *after* Stop, which used to re-arm `running`
    # on a turn that was already over and leave the pet stuck there.
    "SubagentStop": KEEP,
    "PreCompact": "running",
    "PostCompact": "running",
    "SessionEnd": None,
}


def _detail(event: str, payload: dict[str, Any]) -> str:
    """Extra line for the speech bubble.

    The bubble already shows the state label, so this stays empty whenever the
    label alone says everything -- otherwise the bubble reads like a stutter.
    """
    if event in {"PreToolUse", "PostToolUse"}:
        return str(payload.get("tool_name") or "")
    if event == "Notification":
        # The only place Claude gives us real prose worth surfacing.
        return str(payload.get("message") or "")
    if event == "SubagentStop":
        return "subagent finished"
    if event in {"PreCompact", "PostCompact"}:
        return "compacting context"
    return ""


def _is_idle_nudge(payload: dict[str, Any], session_id: str) -> bool:
    """Whether this `Notification` is only "you have not typed yet".

    The same wording means two things depending on when it arrives. After
    `Stop` the turn is over, the pet has already said `done`, and this is
    Claude noting the silence since -- nothing the user does not already know.
    Arriving mid-turn it means Claude has asked a question and cannot continue,
    which is precisely what `needs you` is for.

    So the message alone does not decide it; whether the turn had ended does.
    """
    message = str(payload.get("message") or "").lower()
    if IDLE_NOTIFICATION not in message:
        return False
    session = state.read().get("sessions", {}).get(session_id)
    # Only a turn actually in progress -- a prompt sent (`turn_over` False)
    # and no `Stop` yet -- turns this wording into a real block. It used to be
    # the other way round: ignored only after a `Stop`, so a session that had
    # never been prompted at all counted as needing you. Claude sends this
    # nudge after a minute of idling whether or not you ever typed, and the
    # VS Code extension opens sessions that sit exactly like that -- so one
    # idle panel put the pet on `needs you`, which never expires and outranks
    # everything, and every click went to VS Code no matter which terminal
    # had actually finished.
    turn_over = session.get("turn_over") if isinstance(session, dict) else None
    return turn_over is not False


def _tool_failed(payload: dict[str, Any]) -> bool:
    """Best-effort detection of a failed tool call in a PostToolUse payload."""
    response = payload.get("tool_response")
    if isinstance(response, dict):
        if response.get("is_error") is True or response.get("success") is False:
            return True
    return False


#: A hook taking longer than this is logged, with what it was doing. Claude
#: Code waits on every hook synchronously, so a slow one is a frozen terminal
#: -- and a report of "the terminal freezes sometimes" is unanswerable without
#: knowing whether the hook was the thing that took the time.
SLOW_SECONDS = 0.5


def main(argv: list[str] | None = None) -> int:
    import time

    started = time.monotonic()
    phases: list[str] = []
    try:
        return _main(argv, phases)
    finally:
        elapsed = time.monotonic() - started
        if elapsed >= SLOW_SECONDS:
            _log_slow(elapsed, phases)


def _log_slow(elapsed: float, phases: list[str]) -> None:
    """Append one line about a slow hook to the state dir. Never raises."""
    try:
        import datetime

        path = state.state_dir() / "hook-slow.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{stamp}  {elapsed:.2f}s  {' > '.join(phases) or '-'}\n")
        # Bounded: the last 200 lines are plenty to see a pattern.
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) > 200:
            path.write_text("\n".join(lines[-200:]) + "\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _main(argv: list[str] | None, phases: list[str]) -> int:
    import time

    argv = sys.argv[1:] if argv is None else argv
    forced_event = argv[0] if argv else None
    _t = time.monotonic()

    def mark(name: str) -> None:
        nonlocal _t
        now = time.monotonic()
        phases.append(f"{name}={now - _t:.2f}s")
        _t = now

    try:
        raw = sys.stdin.read()
    except (OSError, ValueError):
        raw = ""
    mark("stdin")
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    # One bridge serves every agent, so the event arrives in the caller's own
    # vocabulary -- Gemini says BeforeAgent where Claude says UserPromptSubmit
    # -- and is translated on the way in.
    event = agents.to_canonical(forced_event or str(payload.get("hook_event_name") or ""))
    if event not in EVENT_STATES:
        return 0

    pet_state = EVENT_STATES[event]
    if event == "PostToolUse" and _tool_failed(payload):
        pet_state = "failed"

    session_id = str(payload.get("session_id") or "default")

    if event == "Notification" and _is_idle_nudge(payload, session_id):
        return 0
    fields: dict[str, Any] = {
        "detail": _detail(event, payload) if pet_state else None,
        "cwd": str(payload.get("cwd") or "") or None,
        "locator": (locate.locator() if event in LOCATE_ON else None),
        # Which event put the session where it is. Costs a word in the state
        # file and turns "why does it say working?" into one `status` call.
        "event": event,
    }

    # Tracked rather than inferred from the last event, because events keep
    # arriving after a turn ends -- a background subagent finishing, say -- and
    # any of them would otherwise look like the turn had resumed.
    if event == "Stop":
        fields["turn_over"] = True
    elif event == "UserPromptSubmit":
        fields["turn_over"] = False
    # Omitting `state` entirely leaves the stored one alone; passing None
    # deletes the session.
    if pet_state != KEEP:
        fields["state"] = pet_state

    mark("locate")
    try:
        state.update(session_id, **fields)
    except OSError:
        return 0
    mark("state")

    # `UserPromptSubmit` as well as `SessionStart`, because a session outlives
    # the pet: quit it overnight, or lose it to a crash, and resuming the
    # conversation you already had would never bring it back -- only starting a
    # brand new one would. Costs a pidfile read when the pet is already up.
    if event in AUTOSTART_ON and config.load().get("autostart", True):
        launch.spawn_detached(reason=event)
        mark("spawn")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
