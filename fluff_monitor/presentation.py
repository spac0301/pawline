"""Shared display text. Rendering and OS-specific widgets do not decide metrics."""
import re
import time


def model_text(value):
    """Display Claude version separators as decimals; leave source IDs intact."""
    return re.sub(r"^(claude-(?:opus|sonnet|haiku)-\d+)-(\d{1,2})(?=-|$)",
                  r"\1.\2", value or "")


def menu_actions(surface, *, theme="dark", pinned=False, walking=False):
    """Keep information-window controls separate from pet controls on both UIs."""
    if surface == "panel":
        return (("theme", "어두운 화면" if theme == "light" else "밝은 화면"),
                ("pin", "정보창 고정 해제" if pinned else "정보창 고정"))
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
    text = f"입력 캐시 {fraction:.1%}" if isinstance(fraction, (int, float)) else "입력 미확인"
    text += " · " + age_text(usage.get("observed_at"))
    def number(key):
        value = values.get(key)
        return f"{value:,}" if isinstance(value, int) else "—"
    details = (f"입력 캐시 히트 · 마지막 사용량 기록\n"
               f"캐시에서 재사용 {number('cached_input_tokens')} / 전체 입력 {number('input_tokens')} 토큰\n"
               f"새 출력 {number('output_tokens')} 토큰")
    if values.get("cache_write_input_tokens") is not None:
        details += f"\n새로 캐시에 저장 {number('cache_write_input_tokens')} 토큰 (히트에서 제외)"
    details += ("\n기록: " + age_text(usage.get("observed_at"))
                + "\n비용 절감률이나 요청 건수의 히트율이 아닌 입력 토큰 재사용률입니다."
                + "\n현재 서버 캐시 유지 여부는 미확인입니다.")
    return text, details
