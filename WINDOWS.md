# Windows에서 시작하기

[Windows x64 포터블 ZIP 받기](https://github.com/spac0301/pawline/releases/tag/v0.2.4)

## EXE로 실행

1. ZIP을 사용자 폴더에 압축 해제합니다. 설치 프로그램은 없으며 Python을 따로 설치할 필요도 없습니다.
2. [Fluff 원본](https://petdex.dev/pets/fluff)에서 펫을 받습니다. `pet.json`과 `spritesheet.webp`가 들어 있는 폴더를 `%USERPROFILE%\.codex\pets\fluff`로 둡니다.
3. `pawline` 폴더 안의 `pawline.exe`를 실행합니다. `_internal` 폴더를 분리하지 마세요.

다른 펫 폴더를 쓰려면 압축을 푼 폴더에서 PowerShell로 실행합니다.

```powershell
.\pawline\pawline.exe --pet-dir "C:\path\to\fluff"
```

실행파일은 아직 코드 서명이 없는 초기 Windows 배포본입니다. 운영체제가 실행을 차단한다면 보안 설정을 낮추지 말고 소스 실행 경로를 사용하거나 검토를 요청하세요.

## 표시되는 정보

기본 수집기는 로컬 Codex·Claude 세션 기록을 읽습니다. 모델 호출, 캐시 유지를 위한 요청, 대화창 메시지는 만들지 않습니다.
Windows 상태 파일은 `%LOCALAPPDATA%\FluffMonitor\state`에 저장합니다.
다른 자료 위치를 쓸 때만 `FLUFF_STATE_DIR`, `CODEX_HOME`을 지정하세요.

Claude 작업은 명령행의 `--session-id` 또는 `--resume` UUID, 혹은 사용자가 명시한 담당 역할과 연결합니다.
이름이나 최근 기록 시간만 보고 다른 세션을 추측 연결하지 않습니다.

## 응답 모델 관측 연결

입력 사용량 읽기와 실제 응답 모델 관측은 별도 기능입니다.
`pawline-capture`를 연결하기 전 [보안 설명](SECURITY.md)을 확인하세요.

1. 이 컴퓨터에 설치된 원래 Codex CLI의 위치를 등록합니다.

   ```powershell
   .\pawline-capture\pawline-capture.exe --fluff-configure-cli "C:\path\to\native\codex.exe"
   ```

2. **Codex 앱 시작 환경에만** `FLUFF_DESKTOP_CAPTURE=1`과 `CODEX_CLI_PATH=압축을 푼 폴더\pawline-capture\pawline-capture.exe`를 지정합니다.
3. 진행 중인 작업을 마친 뒤 Codex 앱을 정상적으로 재시작합니다.

`.cmd` 파일을 네이티브 CLI 경로로 지정하지 않습니다. 다른 사람의 계정 설정·키·세션 UUID를 복사하지 않습니다.
브라우저·설정 조회 같은 보조 호출은 원래 CLI로 전달합니다. 기존 기업 프록시를 연결하는 기능은 아직 지원하지 않습니다.

## 소스로 실행하거나 빌드

Python 3.12 x64가 있는 Windows에서 프로젝트 폴더를 엽니다.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-windows.lock
.\launch-pet.cmd --pet-dir "C:\path\to\fluff"
```

직접 EXE를 만들려면 위의 **새 가상환경**에서 `windows/build.ps1`을 실행합니다.
빌드에는 사용하는 Qt Essentials만 포함하며, PDF·가상 키보드용 Addons는 필요하지 않습니다.
배포 전 `tests/verify_windows_bundle.py dist verification/bundle.json`을 같은 Python으로 실행합니다.
런타임 라이선스는 `licenses` 폴더에, 해당 버전의 Qt/PySide 소스 위치는 `licenses/Qt-SOURCES.json`에 포함됩니다.

Qt/PySide는 LGPLv3 조건으로 사용합니다. DLL은 별도 파일로 제공하며, 호환되는 수정본으로 교체하거나 라이브러리 수정 사항을 디버깅할 권리를 제한하지 않습니다.
Pawline의 공개 소스와 빌드 스크립트로 다시 빌드할 수 있습니다. [해당 버전 소스와 고지](assets/licenses/Qt-SOURCES.json)를 참조하세요.

## 확인된 범위

Windows CI에서 공통 검사, 합성 Qt 화면 검사, EXE 시작·종료, 네이티브 CLI의 입력·출력·오류 출력·종료 코드 전달을 검사합니다.
실제 사용자 Windows의 Codex 연동, DPI 배율, 여러 모니터에서의 사용까지 대신 검증한 것은 아닙니다.
검사 기록과 범위는 [VERIFICATION.md](VERIFICATION.md)에 정리합니다.
