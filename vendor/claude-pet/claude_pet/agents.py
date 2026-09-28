"""The coding agents the pet can follow.

Claude Code, Codex and Gemini CLI have converged on one hook vocabulary.
Codex's binary carries Claude's event names verbatim, and Gemini ships a
`gemini hooks migrate` whose whole job is rewriting a Claude configuration into
its own. So a single bridge serves all three, and what actually differs is
narrow:

* **what the events are called** -- Gemini renames five of them and leaves the
  rest alone; Codex renames none
* **where the configuration lives** -- a settings file each, in its own shape
* **how to recognise the process** -- which matters more than it sounds. A
  session's entry is kept alive by checking that its process is still there,
  and `claude` is a process called `claude` while `gemini` is a process called
  `node`. Trusting `comm` alone would have every Node program on the machine
  passing for a Gemini session.

Claude Code's names are the internal vocabulary, since they came first and the
others were written against them.

Stdlib only, and no work at import: this is reached from the hook, which runs
inside every turn of every agent.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

#: The events the pet understands, in Claude Code's spelling.
CANONICAL_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "Notification",
    "Stop",
    "SubagentStop",
    "PreCompact",
    "PostCompact",
    "SessionEnd",
)

#: id -> what the pet needs to know about that agent.
#:
#: `comm` is what `/proc/<pid>/comm` reads for the process that runs a turn.
#: `cmdline` is an extra string that must appear in its command line, needed
#: only where `comm` is too generic to mean anything -- a Node-hosted CLI is
#: just `node` to the kernel.
#:
#: `events` is the *complete* mapping from our name to theirs: an event absent
#: from it is one that agent does not have. Listing only the renames would be
#: shorter and wrong -- it would write `SubagentStop` into Gemini's settings,
#: which has no such event, because nothing renamed it.
AGENTS: dict[str, dict[str, Any]] = {
    "claude": {
        "label": "Claude Code",
        "command": "claude",
        "comm": "claude",
        "cmdline": "",
        "settings": "~/.claude/settings.json",
        "events": {name: name for name in CANONICAL_EVENTS},
    },
    "codex": {
        "label": "Codex",
        "command": "codex",
        # The npm entry point is a Node wrapper that execs a native binary, and
        # the native one is what a turn actually runs under.
        "comm": "codex",
        "cmdline": "",
        "settings": "~/.codex/config.toml",
        # Claude's names verbatim. This list is what Codex actually accepted:
        # offered all ten and asked it back through `hooks/list`, these eight
        # came back registered. `Notification` was dropped without complaint
        # and there is no `SessionEnd` at all -- which is why a Codex session
        # ending is noticed by its process going away instead.
        "events": {
            name: name
            for name in (
                "SessionStart",
                "UserPromptSubmit",
                "PreToolUse",
                "PostToolUse",
                "Stop",
                "SubagentStop",
                "PreCompact",
                "PostCompact",
            )
        },
    },
    "gemini": {
        "label": "Gemini CLI",
        "command": "gemini",
        # Node all the way down, so the command line is the only thing that
        # distinguishes it from any other Node process.
        "comm": "node",
        "cmdline": "gemini",
        "settings": "~/.gemini/settings.json",
        "events": {
            "SessionStart": "SessionStart",
            "SessionEnd": "SessionEnd",
            "Notification": "Notification",
            # "the agent loop starts/completes" is this agent's way of saying
            # a turn began and ended.
            "UserPromptSubmit": "BeforeAgent",
            "Stop": "AfterAgent",
            "PreToolUse": "BeforeTool",
            "PostToolUse": "AfterTool",
            "PreCompact": "PreCompress",
        },
    },
}

#: What the pet is, so it never mistakes itself for an agent.
DEFAULT_AGENT = "claude"

# OpenCode was looked at and left out, for a reason worth writing down rather
# than rediscovering. Its extension point is a JavaScript plugin exporting
# callbacks -- `tool.execute.before`, `tool.execute.after`, `permission.ask` --
# not a command a hook can run, so the bridge would have to become a plugin
# too. Worse, its event bus carries `message.updated` and `permission.asked`
# but nothing that plainly marks a turn beginning or ending, so `running` and
# `done` would have to be inferred from message traffic. That inference is
# guesswork, and guesswork here is what produces a pet sitting on the wrong
# state. Adding it means finding a real turn boundary first.


def known() -> list[str]:
    return list(AGENTS)


def label(agent_id: str) -> str:
    return str(AGENTS.get(agent_id, {}).get("label") or agent_id)


def settings_path(agent_id: str) -> Path:
    return Path(os.path.expanduser(str(AGENTS[agent_id]["settings"])))


def installed(agent_id: str) -> bool:
    # `shutil` is imported here rather than at the top: the hook reaches this
    # module on every tool call, and only this function needs it.
    import shutil

    entry = AGENTS.get(agent_id)
    if entry is None:
        return False
    return shutil.which(str(entry["command"])) is not None


def detect() -> list[str]:
    """Which of them this machine actually has."""
    return [agent_id for agent_id in AGENTS if installed(agent_id)]


# ------------------------------------------------------------------- events


def to_agent_event(agent_id: str, canonical: str) -> str | None:
    """This agent's name for one of our events, or None if it has no such thing.

    Renames are explicit; anything a renaming agent does not list is assumed to
    be spelled the same, which is how all three ended up agreeing on
    `SessionStart` and `Notification`.
    """
    entry = AGENTS.get(agent_id)
    if entry is None:
        return None
    name = entry["events"].get(canonical)
    return str(name) if name else None


def to_canonical(name: str) -> str:
    """Our name for whatever an agent just called an event.

    The bridge is one program serving several agents, so it is told the event
    in the caller's own vocabulary and has to translate on the way in. Names
    are unambiguous across the three, so which agent sent it does not matter.
    """
    for entry in AGENTS.values():
        for canonical, theirs in entry["events"].items():
            if theirs == name:
                return canonical
    return name


# ---------------------------------------------------------------- processes


#: Wraps the block written into Codex's TOML, so it can be found and removed
#: again. Its settings file is not JSON, so it cannot simply be re-serialised
#: the way the others are -- comments and key order are the user's, and
#: rewriting the file would lose both.
TOML_BEGIN = "# >>> claude-pet hooks >>>"
TOML_END = "# <<< claude-pet hooks <<<"


def toml_hook_block(agent_id: str, command: str) -> str:
    """The `[[hooks.Event]]` tables to append to a TOML settings file."""
    lines = [TOML_BEGIN]
    for canonical in CANONICAL_EVENTS:
        name = to_agent_event(agent_id, canonical)
        if name is None:
            continue
        lines.append(f"[[hooks.{name}]]")
        lines.append(f"[[hooks.{name}.hooks]]")
        lines.append('type = "command"')
        lines.append(f'command = "{command}"')
    lines.append(TOML_END)
    return "\n".join(lines) + "\n"


def any_running() -> bool:
    """Whether any agent this pet follows is running at all.

    A /proc sweep, and it belongs here rather than in `state` because
    recognising an agent is not uniform: Claude and Codex are processes named
    after themselves, Gemini is a process called `node` that has to be told
    apart by its command line. Sweeping for comm alone would let any Node
    program on the machine pass for a Gemini session.

    Errs towards True on any failure: never conclude everything has gone away
    because /proc could not be read.
    """
    import os

    try:
        entries = [entry.name for entry in os.scandir("/proc") if entry.name.isdigit()]
    except OSError:
        return True

    wanted = [(name, spec) for name, spec in AGENTS.items() if spec.get("comm")]
    for pid in entries:
        comm = _comm_of(pid)
        if not comm:
            continue
        for name, spec in wanted:
            if comm != spec["comm"]:
                continue
            if not spec.get("cmdline"):
                return True
            if is_process(name, int(pid)):
                return True
    return False


def codex_hook_status(timeout: float = 8.0) -> tuple[int, int] | None:
    """How many hooks Codex has of ours, and how many it will actually run.

    Codex holds any hook that can run a command untrusted until the user
    approves it, and an unapproved hook is silently inert -- installed,
    listed, and never fired. Without asking, `doctor` could only ever say
    "installed", which is the half of the truth that does not explain why
    nothing happens.

    Asked over its app-server, which answers without starting a session or
    spending anything. Returns None when it cannot be asked at all.
    """
    import json
    import subprocess

    try:
        process = subprocess.Popen(  # noqa: S603 - fixed argv
            ["codex", "app-server"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    def send(payload: dict[str, Any]) -> None:
        process.stdin.write(json.dumps(payload) + "\n")
        process.stdin.flush()

    def reply(wanted: int) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = process.stdout.readline()
            if not line:
                return None
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if message.get("id") == wanted:
                return message
        return None

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"clientInfo": {"name": "claude-pet", "version": "0"}}})
        if reply(1) is None:
            return None
        send({"jsonrpc": "2.0", "id": 2, "method": "hooks/list", "params": {}})
        answer = reply(2)
    except (OSError, ValueError):
        return None
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()

    if not answer:
        return None
    groups = (answer.get("result") or {}).get("data") or [{}]
    hooks = [
        hook
        for group in groups
        for hook in (group.get("hooks") or [])
        if "claude-pet" in str(hook.get("command") or "")
    ]
    trusted = [hook for hook in hooks if hook.get("trustStatus") == "trusted"]
    return len(hooks), len(trusted)


def _comm_of(pid: int | str) -> str:
    try:
        with open(f"/proc/{pid}/comm", encoding="utf-8") as stream:
            return stream.read().strip()
    except OSError:
        return ""


def _cmdline_of(pid: int | str) -> str:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as stream:
            return stream.read().replace(b"\0", b" ").decode("utf-8", "replace")
    except OSError:
        return ""


def is_process(agent_id: str, pid: int) -> bool:
    """Whether `pid` is still a running turn of this agent.

    The one question a session's liveness rests on. `comm` decides it where
    that is meaningful and the command line is consulted where it is not, so
    an unrelated Node program cannot pass for a Gemini session and keep a
    dead entry alive.
    """
    entry = AGENTS.get(agent_id)
    if entry is None:
        return False
    if _comm_of(pid) != entry["comm"]:
        return False
    hint = str(entry["cmdline"])
    return not hint or hint in _cmdline_of(pid)


def identify(pid: int) -> str | None:
    """Which agent `pid` is, if any."""
    for agent_id in AGENTS:
        if is_process(agent_id, pid):
            return agent_id
    return None
