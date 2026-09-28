# Windows 전달 안내 — 수신자 실기 검증

Windows용 코드와 실행·검사 경로를 제공한다. 이 배포자는 실제 Windows에서
실행하거나 `.exe`를 빌드하지 않았다. 공통 코드와 Qt 화면은 Linux에서 검사했다.
Windows의 실제 실행 검증은 수신자가 한다는 요청에 따른 전달 상태다.

## 준비와 실행

Python 3.12 x64가 설치된 Windows에서 압축을 일반 사용자 폴더에 푼다.
관리자 실행, 시스템 인증서 설치, 보안 프로그램 해제는 필요하지 않다.
프로젝트 폴더의 터미널에서:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-windows.lock
.\windows\verify.cmd
.\launch-pet.cmd --pet-dir "C:\path\to\trusted-pet-pack"
```

개인 펫 이미지, 사용자 계정 설정과 로그는 이 소스에 넣지 않았다. 자신의
호환 sprite pack 디렉터리를 지정한다. 정상 실행은 `pythonw`를 사용하므로
Claude 담당 교체마다 터미널 창을 추가로 만들지 않는다. 종료 작업은 현재
목록에서 제거하고 공통 수집기가 읽는 기록은 보존한다.

문제 확인 시 콘솔로 한 번 실행할 수 있다:

```powershell
.\.venv\Scripts\python.exe -B run.py pet --pet-dir "C:\path\to\trusted-pet-pack" --test-seconds 10
```

Windows UI는 Qt/PySide6, Linux 기본 UI는 GTK다. 화면 공통 계약은
288×201 논리 픽셀 카드, 94px 값 열, 제목 좌우 8px 여백과 제목만 펼쳐지는
동작이다. 입력 캐시 계산, 종료 작업 필터, 공유 상태와 메뉴 위치 정책을
재사용한다. Windows DPI 배율에 따라 실제 화면 픽셀 수는 달라진다.

## 데이터와 명시적 식별

기본 상태 경로는 `%LOCALAPPDATA%\FluffMonitor\state`다. `FLUFF_STATE_DIR`로
다른 경로를 지정할 수 있다. 기본 Codex 로그는 사용자 홈의 `.codex`를 읽으며
다른 설치는 `CODEX_HOME`으로 지정한다. 계정 인증 파일을 공유하지 않는다.

Claude 프로세스는 명령행의 `--session-id` 또는 `--resume` UUID로 로그와
결속한다. UUID가 없는 새 CLI를 시간이나 최근 파일로 추측 연결하지 않는다.
관리형 담당 역할은 자신의 `role-sources.json`에 명시한 registry로 연결한다.
남의 역할 설정, UUID 또는 절대 경로를 복사하지 않는다.

## 응답 모델명 관측을 선택적으로 연결하기

사용량 읽기와 TLS 관측은 별도다. TLS 관측을 사용하기 전에 `SECURITY.md`를
읽는다. 실행파일을 만들려면 Windows PowerShell에서 `.\windows\build.ps1`을
실행한다. 스크립트는 현재 프로젝트의 `build`/`dist`에만 산출물을 만든다.
실행 정책을 낮추거나 관리자 권한을 요구하는 코드는 없다.

그 컴퓨터의 실제 Codex 앱 시작 환경에서:

- 먼저 `pawline-capture.exe --fluff-configure-cli "원래 Codex 실행파일의 전체 경로"`로
  이 설치의 실행파일 위치를 등록한다. 이 설정에는 인증정보를 넣지 않는다.
- `FLUFF_DESKTOP_CAPTURE`: `1` (앱 실행 환경에만 지정)
- `CODEX_CLI_PATH`: 빌드한 `dist\pawline-capture\pawline-capture.exe`

로 설정한다. `CODEX_ROUTING_REAL_CLI`로 저장한 위치를 명시적으로 덮어쓸 수도 있다.
브라우저·설정 조회용 보조 실행은 원래 CLI로 전달되며 관측기를 새로 만들지 않는다.
`.cmd`를 앱의 네이티브 CLI 실행파일인 것처럼 지정하지 않는다.
기존 프록시가 있으면 이 버전은 우회하지 않고 관측 시작을 거부한다.
기업 프록시 연결과 특정 Codex 앱 버전의 adapter 설정은 별도 확인이 필요하다.
실행 중인 작업을 정리한 뒤 정상적으로 앱을 재시작한다.

## 수신자가 확인할 항목

1. `windows\verify.cmd`: 공통 동작, TLS 로컬 시험, Windows 파일 잠금과
   사용자 ACL 검사, Qt 화면 검사를 통과하는지 확인한다. 모델 호출은 없다.
2. 실제 화면의 100%/150%/200% 배율, 두 모니터, 드래그, 메뉴의 Escape/바깥
   클릭, 펫과 팝업 겹침, 제목 펼치기를 확인한다.
3. 정상 사용자 요청에서 `desktop.json`의 `implementation`이
   `fluff-monitor/0.2.4`이고 자기 작업의 응답 모델명이 관측되는지 확인한다.
   설정 모델명을 실제 응답으로 채워 넣지 않았는지 확인한다.
4. 종료한 Claude/하위 활동이 현재 작업 목록에 남지 않는지 확인한다.
5. 앱 종료 후 임시 CA가 정리되는지, 공개 ZIP에 개인 로그·키·설정이 없는지
   확인한다. 확인 전 Windows에서 검증 완료 또는 무결함으로 배포하지 않는다.

위 검사 스크립트를 받는 Windows 컴퓨터에서 실행할 수 있다. 공개 저장소의
`Source checks`는 Linux와 Windows에서 공통 검사 및 합성 Qt 화면 검사를 실행하도록
구성했다. `Experimental Windows build`는 Actions에서 수동 실행하는 빌드이며,
통과하면 EXE 폴더를 artifact로 받을 수 있다. 아직 실제 실행 결과가 없는 설정을
Windows 검증 완료로 해석하지 않는다. 빌드 성공 후에도 위 실제 화면·앱 연동
확인은 별도로 필요하다.
