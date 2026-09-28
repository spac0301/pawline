"""Private metadata snapshots; legacy data path retained for board compatibility."""
from __future__ import annotations
import json,os,tempfile,time,sys
import math
import uuid
from pathlib import Path
from .platform_support import protect_owned_path


HEARTBEAT_INTERVAL = 2.0


def heartbeat_path(path: Path) -> Path:
    return path.with_suffix(".heartbeat.json")


def with_heartbeat(data: dict, heartbeat: dict) -> dict:
    """Only a pulse for this exact snapshot may extend its freshness."""
    if (data.get("version") != 2 or not data.get("snapshot_id")
            or heartbeat.get("version") != 2
            or heartbeat.get("snapshot_id") != data["snapshot_id"]):
        return data
    updated = heartbeat.get("updated_at")
    published = data.get("updated_at")
    if (type(updated) not in (int, float) or not math.isfinite(updated)
            or type(published) not in (int, float) or not math.isfinite(published)
            or updated < published):
        return data
    return dict(data, updated_at=updated)


def read_snapshot(path: Path) -> dict:
    data = read_json(path)
    if data.get("version") == 2:
        return with_heartbeat(data, read_json(heartbeat_path(path)))
    return data


class SnapshotReader:
    """Avoid decoding unchanged snapshots at the faster animation/UI refresh rate."""
    def __init__(self):self.cached={}
    def _read(self,path):
        path=Path(path)
        try:
            stat=path.stat();stamp=(stat.st_ino,stat.st_mtime_ns,stat.st_size)
        except OSError:
            self.cached.pop(path,None);return {}
        old=self.cached.get(path)
        if old is None or old[0]!=stamp:self.cached[path]=(stamp,read_json(path))
        return self.cached[path][1]

    def read(self, path):
        path = Path(path)
        data = self._read(path)
        if data.get("version") == 2:
            return with_heartbeat(data, self._read(heartbeat_path(path)))
        return data

def state_dir() -> Path:
    configured = os.environ.get("FLUFF_STATE_DIR") or os.environ.get("CODEX_ROUTING_STATE_DIR")
    if configured:
        return Path(configured)
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home()/"AppData/Local")/"FluffMonitor/state"
    return Path.home()/".local/state/codex-routing-detector"


def read_json(path: Path) -> dict:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except (OSError, ValueError):
        return {}


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            protect_owned_path(temp)
            json.dump(data, f, ensure_ascii=False)
            f.write("\n")
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)

class Publisher:
    """Replace changed data atomically; renew liveness with a small sidecar."""
    def __init__(self,directory=None):
        self.directory=Path(directory or state_dir())
        self.previous={};self.written_at={}
    def publish(self,name,data,heartbeat=False):
        now, elapsed = time.time(), time.monotonic()
        path = self.directory / (name + '.json')
        payload = dict(data)
        payload.pop('updated_at', None)
        # Freeze values, not references to mutable collector dictionaries.
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        previous = self.previous.get(name)
        changed = (previous is None or previous[0] != encoded or not path.is_file()
                   or previous[1]['version'] != (2 if heartbeat else 1))
        if changed:
            value = dict(payload, version=2 if heartbeat else 1,
                         publisher_pid=os.getpid(), updated_at=now)
            if heartbeat:
                value['snapshot_id'] = uuid.uuid4().hex
            atomic_json(path, value)
            self.previous[name] = (encoded, value)
        if heartbeat and (changed or elapsed - self.written_at.get(name, float('-inf')) >= HEARTBEAT_INTERVAL):
            value = self.previous[name][1]
            atomic_json(heartbeat_path(path), dict(version=2, snapshot_id=value['snapshot_id'], updated_at=now))
            self.written_at[name] = elapsed
