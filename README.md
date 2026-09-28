<div align="center">

# Pawline

**작업은 계속, 상태 확인은 가볍게.**

A small desktop companion for Codex and Claude Code.

[Windows 다운로드](https://github.com/spac0301/pawline/releases/tag/v0.2.4) · [시작하기](#시작하기) · [변경 기록](CHANGELOG.md) · [문제 신고](https://github.com/spac0301/pawline/issues)

<img src="assets/screenshots/panel-dark.png" width="320" alt="GPT와 Claude의 작업, 요청 모델, 응답 모델, 입력 캐시를 표시하는 Pawline 정보창">

<sub>실제 화면을 예시 데이터로 렌더링했습니다. 펫 이미지는 별도 다운로드합니다.</sub>

</div>

## 무엇을 보여주나요?

- **지금 하는 작업** — Codex와 Claude Code의 현재 작업과 상태를 한곳에 표시합니다.
- **요청 모델과 응답 모델** — 설정한 모델과 실제 응답에서 관측한 이름을 구분합니다. 관측하지 못한 값은 추측하지 않습니다.
- **입력 캐시 사용량** — 최근 입력 중 캐시로 재사용한 비율과 기록 시각을 보여줍니다.

정보창은 펫 옆에 작게 붙고, 긴 제목에 마우스를 올리면 제목만 펼쳐집니다.
상태를 확인하기 위해 대화창에 메시지를 추가하거나 모델을 호출하지 않습니다.

## 시작하기

| 환경 | 받기 · 실행 | 현재 확인 범위 |
| --- | --- | --- |
| Windows 10/11 · x64 | [포터블 ZIP](https://github.com/spac0301/pawline/releases/tag/v0.2.4) → 압축 해제 → `pawline/pawline.exe` | Windows CI에서 빌드·기본 실행 검사 완료. 실제 데스크톱 연동은 사용자 환경에서 확인 |
| Linux · GTK3 | [소스 다운로드](https://github.com/spac0301/pawline/archive/refs/heads/main.zip) → [설치 안내](INSTALL.md) | GTK/X11 및 XWayland 환경에서 확인 |

**펫 준비:** 현재 사용하는 흰 고양이 **Fluff · Sejal R.**는 [Petdex 원본 페이지](https://petdex.dev/pets/fluff)에서 받습니다.
`pet.json`과 `spritesheet.webp`가 있는 폴더를 사용자 홈의 `.codex/pets/fluff`에 두면 기본 경로로 실행할 수 있습니다.
다른 폴더를 사용하려면 [Windows 안내](WINDOWS.md) 또는 [Linux 안내](INSTALL.md)를 따르세요.

Windows ZIP은 **설치 프로그램이 아닌 포터블 실행 묶음**입니다. Python을 따로 설치할 필요는 없지만, EXE 옆의 `_internal` 폴더는 함께 두어야 합니다.
응답 모델을 직접 관측하는 `pawline-capture`는 선택 기능이며, 연결 방법과 동작 범위는 [Windows 안내](WINDOWS.md#응답-모델-관측-연결)에 있습니다.

## 캐시 숫자는 무슨 뜻인가요?

`입력 캐시 98.9%`는 **최근 사용량 기록에서 전체 입력 토큰 중 캐시를 읽어 재사용한 비율**입니다.
출력 캐시, 요청 성공률, 비용 절감률 또는 서버 캐시가 남아 있는 시간을 뜻하지 않습니다.
옆의 시간은 기록이 얼마나 오래됐는지 나타냅니다. 값이 없으면 0%로 채우지 않습니다.

## 내 자료는 어디에 남나요?

작업 정보와 사용량은 로컬에 저장합니다. Pawline은 프롬프트·답변 본문·계정 키를 기록하거나 배포 파일에 넣지 않습니다.
기본 수집기는 로컬 세션 기록을 읽습니다. 선택적으로 켜는 응답 관측기는 로컬 통신 경로에 들어가므로 [보안 설명](SECURITY.md)을 먼저 확인하세요.

## 더 알아보기

[Linux 설치](INSTALL.md) · [Windows 사용](WINDOWS.md) · [보안과 자료 경로](SECURITY.md) · [구조와 개발](docs/architecture.md) · [검증 범위](VERIFICATION.md)

## 라이선스와 출처

Pawline 코드는 [MIT](LICENSE)입니다. 렌더러, 폰트, 제공사 로고, Windows 런타임은 각자의 조건을 따릅니다.
[전체 출처와 고지](THIRD_PARTY_NOTICES.md)에서 확인할 수 있습니다.

Fluff 그림의 재배포 허가를 확인하지 못해 **펫 그림은 저장소와 ZIP에 포함하지 않습니다.**
[원본 다운로드와 작가 안내](PET_ASSET_NOTICE.md)를 제공합니다.
Pawline은 OpenAI 또는 Anthropic의 공식 제품이 아닙니다.
