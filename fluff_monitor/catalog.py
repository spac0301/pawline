"""Read-only saved-task identities; no request or model generation."""
from __future__ import annotations
from contextlib import closing
import json,os,sqlite3,time
from pathlib import Path


class SnapshotCatalog:
    """UI projection of the single collector's catalog; no second SQLite reader."""
    def __init__(self):self.metadata={};self.available=False
    def update(self,snapshot,now=None):
        now=time.time() if now is None else now
        self.available=bool(snapshot.get('catalog_available') and 0<=now-snapshot.get('updated_at',0)<=10)
        self.metadata=(snapshot.get('threads') or {}) if self.available else {}
    def resolve(self,thread_ids):
        return {tid:self.metadata[tid].get('title') for tid in thread_ids if tid in self.metadata}

def task_identity(source):
    """Use explicit native origin metadata; never infer a role from a task name."""
    try:
        obj = json.loads(source) if isinstance(source, str) and source.startswith("{") else source
    except ValueError:
        return {"kind": "unclassified", "parent_thread_id": None}
    if isinstance(obj, dict) and "subagent" in obj:
        sub = obj["subagent"]
        spawn = sub.get("thread_spawn") if isinstance(sub, dict) else None
        if isinstance(spawn, dict):
            return {"kind": "subagent", "parent_thread_id": spawn.get("parent_thread_id"),
                    "agent_path": spawn.get("agent_path"), "agent_nickname": spawn.get("agent_nickname")}
        return {"kind": "internal", "parent_thread_id": None}
    return {"kind": "task", "parent_thread_id": None}


def display_title(value):
    """A task title is one line of plain text, even when its source contains separators."""
    if not isinstance(value, str):
        return None
    return " ".join(value.split()) or None


class ThreadCatalog:
    """Resolve saved, unarchived tasks; None means an existing task without a title.

    Only requested IDs are read. Database model settings never supply routing evidence.
    """
    def __init__(self, database=None):
        self.database = Path(database) if database else Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "state_5.sqlite"
        self.cached = {}
        self.metadata = {}
        self.ids = ()
        self.checked = 0.0
        self.available = False

    def resolve(self, thread_ids):
        ids = tuple(sorted(set(thread_ids)))
        if ids == self.ids and time.monotonic() - self.checked < 5:
            return {tid: self.cached[tid] for tid in ids if tid in self.cached}
        self.ids, self.checked = ids, time.monotonic()
        if not ids or not self.database.is_file():
            self.available = False
            return {tid: self.cached[tid] for tid in ids if tid in self.cached}
        try:
            with closing(sqlite3.connect(self.database.resolve().as_uri() + "?mode=ro", uri=True, timeout=.1)) as db:
                columns = {row[1] for row in db.execute("PRAGMA table_info(threads)")}
                name_column = "name" if "name" in columns else "NULL"
                archive_filter = " AND COALESCE(archived,0)=0" if "archived" in columns else ""
                extra = [col if col in columns else "NULL" for col in ("source", "rollout_path")]
                select = "SELECT id," + name_column + ",title," + ",".join(extra) + " FROM threads WHERE id IN ("
                rows = db.execute(select + ",".join("?" for _ in ids) + ")" + archive_filter, ids).fetchall()
                # Parent metadata may be needed even if no parent request was
                # observed by this capture process. Bound recursion and cycles.
                seen = set(ids)
                for _ in range(16):
                    parents = {task_identity(row[3]).get("parent_thread_id") for row in rows} - seen - {None}
                    if not parents:
                        break
                    seen.update(parents)
                    rows.extend(db.execute(select + ",".join("?" for _ in parents) + ")" + archive_filter,
                                           tuple(parents)).fetchall())
                self.cached = {}
                self.metadata = {}
                self.available = True
                for tid, name, legacy_title, source, rollout_path in rows:
                    title = display_title(name)
                    # Legacy title can contain the complete first prompt; do not use a
                    # multi-paragraph prompt as a name when the display name is absent.
                    if not title and isinstance(legacy_title, str) and len(legacy_title.splitlines()) <= 1:
                        title = display_title(legacy_title)
                    self.cached[tid] = title
                    self.metadata[tid] = dict(task_identity(source), thread_id=tid, title=title,
                                              rollout_path=rollout_path)
        except sqlite3.Error:
            self.available = False
        return {tid: self.cached[tid] for tid in ids if tid in self.cached}
