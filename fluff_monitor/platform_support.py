"""Small OS boundary: locks, private files and explicit process identities."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import uuid


def cli_argument(args, *flags):
    """Read both --flag value and --flag=value without parsing prompt text."""
    for index, value in enumerate(args):
        if value == "--":
            break
        for flag in flags:
            if value == flag and index + 1 < len(args):
                return args[index + 1]
            if value.startswith(flag + "="):
                return value[len(flag) + 1:]
    return None


def is_claude_cli(name, args):
    name = name.lower()
    return (name in {"claude", "claude.exe"} or
            (name in {"node", "node.exe"} and any(
                "@anthropic-ai/claude-code/" in str(value).replace("\\", "/")
                for value in args[1:3])))


def native_claude_session(record, pid, process_start, pid_domain=None, process_created_at=None):
    """Native registration is valid only for this exact live process instance."""
    if not isinstance(record, dict) or type(record.get("pid")) is not int:
        return None
    if record["pid"] != pid or str(record.get("procStart", "")) != str(process_start):
        return None
    if record.get("pidDomain") and record["pidDomain"] != pid_domain:
        return None
    if process_created_at is not None:
        started = record.get("startedAt")
        if type(started) not in (int, float) or started / 1000 + 2 < process_created_at:
            return None
    if record.get("entrypoint") not in (None, "cli") or record.get("kind") == "subagent":
        return None
    try:
        return str(uuid.UUID(record.get("sessionId")))
    except (ValueError, TypeError, AttributeError):
        return None


def lock_exclusive(file):
    """Nonblocking process lock; closing the file releases it on both platforms."""
    try:
        if os.name == "nt":
            import msvcrt
            file.seek(0, os.SEEK_END)
            if file.tell() == 0:
                file.write(b"\0")
                file.flush()
            file.seek(0)
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except (BlockingIOError, OSError):
        return False


def protect_owned_path(path, directory=False):
    """Protect only a newly created application-owned object, never a user's tree."""
    if os.name != "nt":
        Path(path).chmod(0o700 if directory else 0o600)
        return
    # Use the maintained Win32 bindings; POSIX chmod is not an ACL on Windows.
    import ntsecuritycon
    import win32api
    import win32security
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
    try:
        sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    finally:
        token.Close()
    acl = win32security.ACL()
    flags = (win32security.OBJECT_INHERIT_ACE | win32security.CONTAINER_INHERIT_ACE) if directory else 0
    acl.AddAccessAllowedAceEx(win32security.ACL_REVISION, flags, ntsecuritycon.FILE_ALL_ACCESS, sid)
    win32security.SetNamedSecurityInfo(str(path), win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        None, None, acl, None)


def windows_claude_processes(processes=None):
    """Use an explicit CLI session UUID; do not guess identities from timestamps."""
    if processes is None:
        import psutil
        processes = psutil.process_iter(["pid", "name", "cmdline", "create_time"])
    result = {}
    for process in processes:
        try:
            info = process.info
            args = info.get("cmdline") or []
            name = (info.get("name") or "").lower()
            if not is_claude_cli(name, args):
                continue
            tid = str(uuid.UUID(cli_argument(args, "--session-id", "--resume")))
            result[tid] = dict(pid=info["pid"], process_created_at=info["create_time"],
                requested_model=cli_argument(args, "--model"), requested_effort=cli_argument(args, "--effort"),
                requested_source="cli_start_arguments")
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            continue
    return result
