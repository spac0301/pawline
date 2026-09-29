# Linux에서 시작하기

[Linux ZIP 다운로드](https://github.com/spac0301/pawline/releases/download/v0.2.9/pawline-0.2.9-source.zip)

Linux 버전은 Python과 GTK3로 실행합니다. Python 3.10 이상, venv, PyGObject, GTK3, cairo가 필요합니다.
아래 안내는 GTK3/X11 또는 XWayland 환경을 기준으로 합니다.

## 설치

1. ZIP을 풀어 생성된 `pawline` 폴더를 `~/.local/share/fluff-monitor`에 둡니다. 기존 설치가 있다면 먼저 복사해 보관해 주세요.
2. 해당 폴더에서 실행 환경을 준비합니다.

   ```sh
   cd ~/.local/share/fluff-monitor
   chmod u+x launch-pet.sh launch-capture.sh
   python3 -m venv --system-site-packages .venv
   .venv/bin/python -m pip install -r requirements.lock
   .venv/bin/python install_desktop.py
   ```

3. 작업 상태를 읽는 수집기를 사용자 서비스로 등록합니다.

   ```sh
   systemctl --user link "$HOME/.local/share/fluff-monitor/fluff-monitor-activity.service"
   systemctl --user enable --now fluff-monitor-activity.service
   ```

## 펫 준비와 실행

[Fluff 원본 페이지](https://petdex.dev/pets/fluff)에서 펫을 받고, `pet.json`과 `spritesheet.webp`를 `~/.codex/pets/fluff`에 둡니다.
설치 폴더에서 실행합니다.

```sh
./launch-pet.sh
```

다른 펫 폴더를 사용할 때는 경로를 지정할 수 있습니다.

```sh
./launch-pet.sh --pet-dir /path/to/fluff
```

## 응답 모델 관측 연결

기본 수집기는 로컬 작업 기록과 사용량을 읽습니다. 실제 응답 모델 관측을 연결하려면 [보안과 통신 경로](SECURITY.md)를 먼저 확인해 주세요.

이 컴퓨터에 설치된 원래 Codex CLI의 위치를 등록합니다.

```sh
./launch-capture.sh --fluff-configure-cli /path/to/the/apps/bundled/codex
```

이후 Codex 앱을 시작하는 환경에 다음 두 값을 지정합니다.

- `FLUFF_DESKTOP_CAPTURE=1`
- `CODEX_CLI_PATH`: 이 설치의 `launch-capture.sh` 전체 경로

진행 중인 작업을 마친 뒤 Codex 앱을 정상적으로 재시작하면 적용됩니다. 원래 앱 실행 방법도 보관해 두면 위 설정을 제거해 관측 연결을 해제할 수 있습니다.

다른 사람의 계정 설정이나 CLI 경로는 복사하지 않습니다. 기존 기업 프록시를 연결하는 기능은 지원하지 않습니다.

## 수집기만 새로고침하기

0.2.8부터 응답 수집기를 별도 프로세스로 실행합니다. 아래 명령은 Codex 작업과 통신 연결을 유지합니다.

```sh
./launch-capture.sh --fluff-reload-observer
```

새 수집 코드는 공식 릴리스의 `pawline-VERSION-observer.zip`을 받아 적용합니다.

```sh
./launch-capture.sh --fluff-update-observer /path/to/pawline-VERSION-observer.zip
```

이 파일은 실행할 코드입니다. 신뢰하는 릴리스의 파일만 사용하세요. 설치 프로그램을 다시 실행하거나 Codex를 종료할 필요가 없습니다.
0.2.7 이하에서 최초로 전환할 때와 통신 중계부·Python 런타임 자체를 교체할 때는 다음 정상적인 Codex 실행부터 적용됩니다.

앱 목록과 시스템 감시에서는 **Pawline**으로 표시됩니다. Pawline을 종료하면 펫과 정보창만 닫힙니다.
