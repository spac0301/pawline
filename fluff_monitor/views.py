"""Current-task projections shared by the passive UI."""
from __future__ import annotations
import time
from pathlib import Path
from .storage import read_snapshot, state_dir

def desktop_view(directory=None, now=None, *, thread_id=None, catalog=None, activity=None, include_history=False, capture=None):
    """Only actual desktop observations; never substitute a standalone probe."""
    now = now or time.time()
    value = capture if capture is not None else read_snapshot(Path(directory or state_dir()) / "desktop.json")
    has_capture = bool(value)
    sessions = [dict(entry) for entry in (value.get("sessions") or [])]
    observations = list(sessions)
    metrics = activity if activity is not None else read_snapshot(Path(directory or state_dir()) / "activity.json")
    metrics_fresh = bool(metrics) and 0 <= now - metrics.get("updated_at", 0) <= 10 and metrics.get("catalog_available", True)
    states = (metrics.get("threads") or {}) if metrics_fresh else {}
    common = {key: value.get(key) for key in ("active", "updated_at", "publisher_pid", "session_protocol", "observation_disabled")}
    titles = {}
    if catalog is not None:
        ids = [entry["thread_id"] for entry in sessions if entry.get("thread_id")]
        ids.extend(states)
        if thread_id:
            ids.append(thread_id)
        titles = catalog.resolve(ids)
        # Transport IDs are not an app task list. Keep raw capture evidence intact,
        # but offer/follow only saved, unarchived tasks. A saved unnamed task is valid.
        sessions = [dict(entry, title=titles[entry["thread_id"]]) for entry in sessions
                    if entry.get("thread_id") in titles]
        # A saved subagent is not a separate user task. Never classify by title.
        sessions = [s for s in sessions if catalog.metadata.get(s["thread_id"], {}).get("kind", "task") == "task"]
        observed_ids = {entry["thread_id"] for entry in sessions}
        # Saved native tasks exist independently of response-model observation.
        # A native entry never supplies a server request/response or match verdict.
        native_ids = sorted(states, key=lambda tid: max(
            states[tid].get("last_model_activity_at") or 0,
            states[tid].get("state_at") or 0), reverse=True)
        for tid in native_ids:
            if (tid not in observed_ids and tid in titles
                    and catalog.metadata.get(tid, {}).get("kind", "task") == "task"
                    and states[tid].get("available", True)):
                sessions.append(dict(thread_id=tid, title=titles[tid], native_only=True,
                                     requested=None, served=None, connected=False,
                                     request_source="unknown", verdict="NO_DATA"))
    recent, history = list(sessions), []
    if metrics_fresh:
        for entry in sessions:
            meta = states.get(entry["thread_id"]) or {}
            entry["activity"] = meta.get("state", "unknown")
            entry["usage"] = meta.get("usage")
            entry["last_model_activity_at"] = meta.get("last_model_activity_at")
            entry["configured_model"] = meta.get("model")
            entry["configured_effort"] = meta.get("effort")
            entry["children"] = child_activity(entry["thread_id"], states, now)
        # Response completion is not task completion. A saved selection must not
        # keep an ended/stale task in the current-task list indefinitely.
        recent, history = [], []
        for entry in sessions:
            meta = states.get(entry["thread_id"]) or {}
            at = meta.get("state_at") or entry.get("observed_at") or 0
            retained = (meta.get("state") == "running"
                        or entry["children"]["running"] > 0 or now - at <= 300
                        or now - (entry.get("request_started_at") or 0) <= 300)
            (recent if retained else history).append(entry)
        sessions = recent + history if include_history else recent
    if thread_id or catalog is not None:
        selected = (next((entry for entry in sessions if entry.get("thread_id") == thread_id), None)
                    if thread_id else next(iter(sessions), None))
        if selected is None:
            unavailable = bool(thread_id and catalog is not None and catalog.available and thread_id not in titles)
            detail = ("선택한 작업이 앱의 현재 작업 목록에 없습니다. 메뉴에서 다른 작업을 선택할 수 있습니다."
                      if unavailable else "선택한 작업의 새 요청을 기다립니다. 다른 작업의 결과로 대신하지 않습니다."
                      if thread_id else "앱에 등록된 작업의 요청을 기다립니다.")
            value = dict(common, thread_id=thread_id, title=titles.get(thread_id),
                         selection_missing=bool(thread_id), selection_unavailable=unavailable,
                         verdict="NO_DATA", connected=False,
                         detail=detail)
        else:
            value = dict(selected, **common)
    value = dict(value, source="desktop", label="앱 실시간", sessions=sessions,
                 observations=observations, selected_thread=thread_id,
                 history_sessions=history, history_count=len(history),
                 selection_expired=bool(thread_id and metrics_fresh and
                     (any(s.get("thread_id") == thread_id for s in history)
                      or (catalog is not None and catalog.available and thread_id not in titles))),
                 metrics_available=metrics_fresh)
    value["activity_parent_map"] = {}
    if catalog is not None:
        for tid, meta in catalog.metadata.items():
            cursor, visited = meta.get("parent_thread_id"), {tid}
            while cursor and cursor not in visited:
                parent = catalog.metadata.get(cursor) or {}
                if parent.get("kind") == "task":
                    value["activity_parent_map"][tid] = cursor
                    break
                visited.add(cursor)
                cursor = parent.get("parent_thread_id")
    if not has_capture:
        value.update(label="앱 감시 연결 전", verdict="NO_DATA", active=False, connected=False,
                     detail="Codex 앱 응답 수집기가 아직 연결되지 않았습니다. 별도 검사 결과는 표시하지 않습니다.")
    elif not value.get("active") or now - value.get("updated_at", 0) > 7:
        value.update(label="앱 감시 끊김", verdict="UNKNOWN", connected=False, active=False)
    elif value.get("capture_lost") or value.get("observation_disabled"):
        value.update(label="앱 응답 읽기 중단", verdict="UNKNOWN", served=None,
                     detail="응답 모델 관측이 중단됐습니다. 작업 목록과 사용량은 로컬 기록에서 계속 읽습니다.")
    elif not value.get("connected") and value.get("transport") == "http" and value.get("error_code"):
        value["label"] = "HTTP 응답 오류"
    elif not value.get("connected") and value.get("transport")=="http" and value.get("response_id") and value.get("status") in ("completed","failed","incomplete","cancelled"):
        value["label"]="HTTP 응답 기록"
    elif not value.get("connected") and (value.get("usage") or {}).get("observed_at",0)>(value.get("observed_at") or 0):
        value.update(label="모델 관측 대기",verdict="OBSERVATION_GAP",served=None,response_id=None,
                     detail="로컬 작업 기록은 갱신됐지만 새 응답 모델명이 감시기에 도착하지 않았습니다.")
    elif not value.get("connected"):
        value.update(label="작업 확인 필요" if value.get("selection_unavailable") else
                     "선택한 작업 대기" if value.get("selection_missing") else "앱 연결 대기", verdict="NO_DATA")
    elif not value.get("response_id"):
        value.update(label="앱 응답 대기", verdict=value.get("verdict") or "NO_DATA")
    return value


def child_activity(parent, states, now):
    """Roll up descendants without confusing their model result with the parent's."""
    result = {"running": 0, "recent_completed": 0, "total": 0, "details": []}
    for tid, meta in states.items():
        cursor, visited = meta.get("parent_thread_id"), {tid}
        while cursor and cursor not in visited:
            if cursor == parent:
                result["total"] += 1
                result["details"].append({"name": meta.get("title") or meta.get("agent_nickname") or "하위 활동",
                                           "state": meta.get("state", "unknown")})
                if meta.get("state") == "running":
                    result["running"] += 1
                elif meta.get("state") in ("completed", "interrupted") and now - meta.get("state_at", 0) <= 60:
                    result["recent_completed"] += 1
                break
            visited.add(cursor)
            cursor = (states.get(cursor) or {}).get("parent_thread_id")
    return result


def claude_view(activity, selected=None, now=None, include_history=False):
    now = time.time() if now is None else now
    if not activity or now - activity.get("updated_at", 0) > 10:
        return {"label": "감시 연결 대기", "state": "unknown", "sessions": [], "selected_session": selected}
    raw = activity.get("claude_sessions") or {}
    all_sessions, represented = [], set()
    for role_id, role in (activity.get("claude_roles") or {}).items():
        tid = role.get("session_id")
        represented.update(v for v in (tid, role.get("previous_session_id")) if v)
        live = raw.get(tid) or {}
        entry = dict(live, session_id=tid, selection_key="role:" + role_id,
                     title=role.get("title") or "구현 담당", role_id=role_id)
        if not live or not live.get("process_alive"):
            entry.update(state="handoff", process_alive=False, usage=None, served=None,
                         requested_model=None, requested_effort=None, state_at=None)
        all_sessions.append(entry)
    all_sessions.extend(dict(s, selection_key=s["session_id"]) for s in raw.values()
                        if s.get("session_id") not in represented)
    all_sessions.sort(key=lambda s: (bool(s.get("process_alive")), s.get("state_at") or 0), reverse=True)
    # This is a live CLI picker. Historical logs remain in the shared collector,
    # but another provider's history toggle can never expose them here.
    visible = [s for s in all_sessions if s.get("role_id") or s.get("process_alive")]
    current = (next((s for s in visible if s.get("selection_key") == selected), None) if selected
               else next((s for s in all_sessions if s.get("role_id")), None)
               or next((s for s in all_sessions if s.get("process_alive")), None))
    if current is None:
        return {"label": "선택한 CLI 대기" if selected else "실행 중인 CLI 없음", "state": "idle",
                "sessions": visible, "selected_session": selected, "observed_at": None,
                "selection_expired": bool(selected)}
    labels = {"running": "작업 중", "completed": "응답 완료", "unknown": "활동 확인 필요", "offline": "CLI 종료", "handoff": "담당 연결 대기"}
    return dict(current, label=labels.get(current.get("state"), "기록 대기"),
                observed_at=current.get("state_at"), sessions=visible, selected_session=selected)
