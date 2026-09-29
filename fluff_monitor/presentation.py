"""Shared display text. Rendering and OS-specific widgets do not decide metrics."""
import re
import time

CARD_WIDTH = 320
CARD_PADDING = 12
ROW_HEIGHT = 20
CONTROL_HEIGHT = 28
CONTENT_INSET = 8
FIELD_WIDTH = 48
FIELD_GAP = 8
STATUS_WIDTH = 88
PROVIDER_HEIGHT = ROW_HEIGHT + CONTROL_HEIGHT * 3 + 12
FONT_SIZES = dict(body=13, secondary=12, heading=14, metric=18)
THEMES = {
    "dark": dict(background="#23252a", edge="#393c44", foreground="#eceef2", secondary="#cbd0d9", muted="#a6aebb",
                 hover="#343841", inset="#2c3038", selected="#3c424d", accent="#a9c2ed",
                 accent_start="#849ecb", accent_end="#b3c7e9",
                 warning="#e4c18b", danger="#f09b9f", positive="#86c9a5"),
    "light": dict(background="#fafbfc", edge="#dce0e6", foreground="#252a33", secondary="#475260", muted="#646e7c",
                  hover="#e9edf2", inset="#eff1f5", selected="#e0e6ee", accent="#486798",
                  accent_start="#486798", accent_end="#7597c2",
                  warning="#886020", danger="#b44653", positive="#21734e"),
}


def selection_text(selected):
    return "직접 선택" if selected else "자동 추적"


def progress_status(provider, value):
    """Native work activity and response-model evidence are independent."""
    state = value.get("activity") if provider == "gpt" else value.get("state")
    if provider == "gpt" and value.get("metrics_available") is False:
        state = None
    if state == "running":
        return "작업 중", "blue"
    if value.get("metrics_available") is not False and (value.get("children") or {}).get("running"):
        return "하위 작업 중", "blue"
    states = {"completed": ("응답 완료", "neutral"), "interrupted": ("중단됨", "waiting"),
              "handoff": ("연결 대기", "waiting"), "offline": ("연결 종료", "neutral"),
              "idle": ("작업 없음", "neutral")}
    if state in states:
        return states[state]
    if (provider == "gpt" and value.get("active") and value.get("connected")
            and not value.get("capture_lost") and not value.get("observation_disabled")):
        if value.get("status") == "in_progress":
            return "응답 중", "blue"
        if value.get("status") == "completed":
            return "최근 응답", "neutral"
    if not value.get("thread_id") and not value.get("session_id"):
        return "작업 없음", "neutral"
    return "상태 미확인", "neutral"


def observation_status(provider, value):
    """Describe only evidence actually supplied by this provider's projection."""
    served = value.get("served")
    if provider == "claude":
        setting = model_text(value.get("requested_model")) or "미확인"
        if value.get("requested_effort"):
            setting += " · "+value["requested_effort"]
        if served:
            return "기록 확인", "neutral", "시작 설정  "+setting+"\n실제 응답  "+model_text(served)
        prefix = "시작 설정  "+setting+"\n" if value.get("requested_model") else ""
        return "기록 대기", "neutral", prefix+"새 응답 로그의 모델명을 기다리고 있어요."
    if value.get("observation_disabled") or value.get("capture_lost"):
        return ("관측 중단", "waiting", value.get("detail") or
                "응답 모델 관측이 중단됐습니다. 작업 상태와 사용량 기록은 별도로 확인합니다.")
    verdict = value.get("verdict")
    if verdict == "REROUTED" and served and value.get("requested"):
        return ("모델 다름", "bad", "요청 모델: "+model_text(value.get("requested"))
                +"\n실제 응답: "+model_text(served)+"\n\n이 응답에서 관측한 모델명이 요청과 다릅니다.")
    if verdict == "ERROR":
        return "응답 오류", "waiting", value.get("detail") or "응답 처리 중 오류가 기록됐습니다."
    if verdict == "UNSUPPORTED":
        return "미지원", "waiting", value.get("detail") or "이 응답 형식에서는 모델명을 확인할 수 없습니다."
    if served:
        text = "모델 일치" if verdict in ("ok", "OK") and value.get("requested") else "관측됨"
        caption, requested, effort, _ = gpt_request(value)
        setting = model_text(requested) or "미확인"
        if effort:
            setting += " · "+effort
        detail = caption+"  "+setting+"\n응답  "+model_text(served)
        if value.get("observed_at"):
            detail += "\n"+age_text(value["observed_at"])+" 관측"
        if value.get("active") is False:
            detail += "\n\n현재 관측 연결은 끊겨 있어 마지막 기록을 표시합니다."
            return "이전 기록", "neutral", detail
        return text, "neutral", detail
    if value.get("active") is False:
        return "연결 대기", "waiting", value.get("detail") or "모델 관측 연결을 기다리고 있습니다."
    caption, requested, effort, _ = gpt_request(value)
    prefix = caption+"  "+model_text(requested)+(" · "+effort if effort else "")+"\n" if requested else ""
    return ("모델 대기", "neutral", prefix+(value.get("detail") or
            "새 응답의 모델명을 기다리고 있어요."))


def cache_breakdown(usage, available=True):
    """Partition the last input into cache reads and all remaining input.

    Cache writes are part of remaining input; output tokens never enter this bar.
    Missing/inconsistent counts are unknown, never a fabricated zero percent.
    """
    values = (usage or {}).get("last") or {}
    total, cached = values.get("input_tokens"), values.get("cached_input_tokens")
    if (not isinstance(total, int) or isinstance(total, bool) or total <= 0
            or not isinstance(cached, int) or isinstance(cached, bool) or not 0 <= cached <= total):
        return {"known": False, "message": "입력 기록을 기다리고 있어요." if available else "입력 기록에 연결되지 않았어요."}
    return {"known": True, "fraction": cached/total, "percent": f"{cached/total:.1%}",
            "cached": cached, "other": total-cached, "total": total,
            "age": age_text(usage.get("observed_at"))}


def model_text(value):
    """Display Claude version separators as decimals; leave source IDs intact."""
    return re.sub(r"^(claude-(?:opus|sonnet|haiku)-\d+)-(\d{1,2})(?=-|$)",
                  r"\1.\2", value or "")


def toolbar_icon(name, color, active=False):
    """Shared vector controls, independent of platform icon fonts/themes."""
    if name == "pin":
        rotation = "" if active else ' transform="rotate(35 12 12)"'
        fill = color if active else "none"
        shape = f'<g{rotation}><path d="M9 3h6l-1 7 3 3v2H7v-2l3-3-1-7z" fill="{fill}"/><path d="M12 15v6"/></g>'
    elif name == "sun":
        shape = '<circle cx="12" cy="12" r="3.6"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>'
    elif name == "moon":
        shape = '<path d="M20.5 14.1A8.5 8.5 0 0 1 9.9 3.5a8.5 8.5 0 1 0 10.6 10.6z"/>'
    elif name == "chevron-down":
        shape = '<path d="m5 9 7 7 7-7"/>'
    elif name == "chevron-right":
        shape = '<path d="m9 5 7 7-7 7"/>'
    else:
        raise ValueError("unknown toolbar icon")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
            f'stroke="{color}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
            +shape+'</svg>').encode()


def menu_actions(surface, *, walking=False):
    """Only pet actions use a menu; window controls live in the toolbar."""
    if surface == "pet":
        return (("pet", "쓰다듬기"),
                ("walk", "산책 끄기" if walking else "산책 켜기"),
                ("quit", "펫 종료"))
    raise ValueError("unknown menu surface")


def route_status(value):
    if value.get("observation_disabled") or value.get("capture_lost"):
        return "관측 중단", "waiting"
    states = {"ok": ("일치", "neutral"), "OK": ("일치", "neutral"),
              "REROUTED": ("불일치", "bad"), "ERROR": ("실패", "waiting"),
              "UNSUPPORTED": ("미지원", "waiting"), "PENDING": ("응답 대기", "blue"),
              "UNKNOWN": ("미확인", "neutral"), "NO_DATA": ("미연결", "neutral"),
              "OBSERVATION_GAP": ("관측 대기", "waiting")}
    text, tone = states.get(value.get("verdict"), ("확인 못함", "neutral"))
    if value.get("verdict") == "PENDING" and value.get("status") == "in_progress":
        text = "응답 중"
    return text, tone


def gpt_request(value):
    """Keep a captured request distinct from the native session's setting."""
    if value.get("requested"):
        return "요청", value["requested"], value.get("effort"), "선택한 작업의 요청에서 관측한 모델·추론 설정입니다."
    return ("설정", value.get("configured_model"), value.get("configured_effort"),
            "선택한 Codex 작업의 로컬 기록에 남은 모델·추론 설정입니다. 응답 모델명을 확인한 값은 아닙니다.")


def age_text(at, now=None):
    if not at:
        return "기록 없음"
    age = max(0, int((now or time.time()) - at))
    if age < 60:
        return f"{age}초 전"
    if age < 3600:
        return f"{age // 60}분 전"
    if age < 86400:
        return f"{age // 3600}시간 전"
    return f"{age // 86400}일 전"


def usage_text(usage, available=True):
    if not usage:
        return ("입력 기록 대기" if available else "입력 미확인",
                "새 모델 호출 없이 로컬 사용량 기록을 기다립니다. 미수집은 0%가 아닙니다.")
    values = usage.get("last") or {}
    fraction = values.get("cached_fraction")
    text = f"마지막 입력 캐시 {fraction:.1%}" if isinstance(fraction, (int, float)) else "입력 미확인"
    text += " · " + age_text(usage.get("observed_at"))
    def number(key):
        value = values.get(key)
        return f"{value:,}" if isinstance(value, int) else "—"
    details = (f"마지막 사용량 기록\n"
               f"캐시 재사용 {number('cached_input_tokens')} / 전체 입력 {number('input_tokens')} 토큰\n"
               f"새 출력 {number('output_tokens')} 토큰")
    if values.get("cache_write_input_tokens") is not None:
        details += f"\n새로 캐시에 저장 {number('cache_write_input_tokens')} 토큰 (히트에서 제외)"
    details += ("\n기록: " + age_text(usage.get("observed_at"))
                + "\n\n입력 토큰 중 캐시에서 재사용한 비율입니다."
                + "\n비용 절감률이나 요청 건수의 히트율과는 다릅니다."
                + "\n현재 캐시 유지 여부는 확인할 수 없습니다.")
    return text, details


def usage_parts(usage, available=True):
    """A compact row can emphasize the metric without changing its meaning."""
    _, details = usage_text(usage, available)
    if not usage:
        return "입력 캐시", "기록 대기" if available else "미확인", "", details
    fraction = (usage.get("last") or {}).get("cached_fraction")
    metric = f"{fraction:.1%}" if isinstance(fraction, (int, float)) else "미확인"
    return "입력 캐시", metric, age_text(usage.get("observed_at")), details
