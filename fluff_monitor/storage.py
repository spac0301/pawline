"""Private metadata snapshots; legacy data path retained for board compatibility."""
from __future__ import annotations
import json,os,tempfile,time,sys
from pathlib import Path
from .platform_support import protect_owned_path


class SnapshotReader:
    """Avoid decoding unchanged snapshots at the faster animation/UI refresh rate."""
    def __init__(self):self.cached={}
    def read(self,path):
        path=Path(path)
        try:
            stat=path.stat();stamp=(stat.st_ino,stat.st_mtime_ns,stat.st_size)
        except OSError:
            self.cached.pop(path,None);return {}
        old=self.cached.get(path)
        if old is None or old[0]!=stamp:self.cached[path]=(stamp,read_json(path))
        return self.cached[path][1]

def state_dir() -> Path:
    default = (Path(os.environ.get("LOCALAPPDATA") or Path.home()/"AppData/Local")/"FluffMonitor/state"
               if sys.platform == "win32" else Path.home()/".local/state/codex-routing-detector")
    return Path(os.environ.get("FLUFF_STATE_DIR") or os.environ.get("CODEX_ROUTING_STATE_DIR") or default)


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
    def __init__(self,directory=None):
        self.directory=Path(directory or state_dir())
        self.previous={};self.written_at={}
    def publish(self,name,data,heartbeat=False):
        now=time.time()
        if data==self.previous.get(name) and (not heartbeat or now-self.written_at.get(name,0)<2):return
        atomic_json(self.directory/(name+'.json'),dict(data,version=1,publisher_pid=os.getpid(),updated_at=now))
        self.previous[name]=dict(data);self.written_at[name]=now
