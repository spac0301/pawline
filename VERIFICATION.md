# 검증 범위 · 0.2.7

## Windows 배포본

[빌드·실행 검사 기록](https://github.com/spac0301/pawline/actions/runs/36503467976)

- 소스: `47b934bd7be933ba87519ee5fa50a82ddd028d31`
- Windows Server 2022 · Python 3.13.15 · x64에서 EXE 빌드 성공
- 공통 검사 111개: 104개 통과, 다른 플랫폼 전용 7개 제외
- GTK·Qt의 `claude-opus-5.5` 표시와 잘린 활동 상태의 펼치기·닫기·열 위치 유지 확인
- Windows Qt 화면과 패키징된 EXE의 시작·종료 확인
- 두 실행 파일에 Python 3.13.15와 `python313.dll` 포함 확인
- 보조 CLI의 표준 입력·출력·오류 출력·종료 코드 전달 확인
- 런타임 라이선스 포함, 사용하지 않는 Qt PDF·Virtual Keyboard 미포함 확인

검사에는 임시 자료와 합성 펫 이미지를 사용했습니다. 모델 호출이나 사용자 계정 연결은 없었습니다.
실제 Windows 데스크톱의 Codex 연동·배율·다중 모니터 사용은 별도 확인이 필요합니다.

첫 Windows 실행파일 검사는 네이티브 CLI 전달 결함으로 실패했습니다.
[실패 기록](https://github.com/spac0301/pawline/actions/runs/36483840040)을 보존하고,
자식 프로세스의 입출력과 종료 코드를 기다려 전달하도록 수정했습니다.

## Linux와 공통 동작

Linux에서 공통 검사를 실행했고, 같은 소스의 Windows 검사도 위 빌드에서 통과했습니다. GTK 격리 화면과 Qt offscreen 화면에서 관측 중단 시 작업·설정 모델·사용량 표시를 확인했습니다.

- 로컬 공통 검사 111개: 110개 통과, Windows ACL 검사 1개 제외
- GTK 격리 화면과 Qt offscreen 화면의 열 정렬, 제목 여백·펼치기, 메뉴 위치, 종료된 선택 해제 확인
- 로컬 TLS 서버에서 WebSocket·HTTP/SSE 전달, 요청·응답 연결, 인증, 관측 실패 시 통신 유지 확인
- 기록 상한, 큐 초과, 저장 실패·지연, 닫힌 연결 정리 확인
- 큐 초과·집계 실패 뒤 새 연결에서 수집 재개, 이전 연결의 늦은 응답 거부 확인
- 같은 TLS 연결의 다음 HTTP 요청에서 프록시 재시작 없이 수집 복구 확인
- 5 MiB 입력과 비ASCII 문자가 메모리 한도 안에서 처리되는지 확인
- 브라우저·설정 조회용 호출이 원래 CLI에 전달되고 관측기를 추가 실행하지 않는지 확인

## 저장과 생존 확인

- 30초의 가상 시간 동안 내용 파일의 바이트·mtime·inode를 유지하고 heartbeat만 갱신하는지 확인
- GTK·Qt 공통 reader가 바뀌지 않은 본문을 다시 해석하지 않는지 확인
- 중첩 사용량 변경, 수집기 재시작, 다른 snapshot ID, 누락·잘못된 heartbeat, 본문·heartbeat 저장 실패와 복구 확인
- 수집기 중단 후 GPT·Claude의 기존 연결 만료와 version 1 호환 확인
- 실제 usage.observed_at과 캐시 사용량은 heartbeat로 바뀌지 않음

외부 파일 소비자는 [규약](docs/snapshots.md)을 지원한 뒤 새 수집기를 사용해야 합니다.

## 배포 범위

사용자 대화·계정·키·사용량·개인 펫 이미지는 포함하지 않습니다.
관측 누락은 설정 모델명이나 0%로 채우지 않습니다.
기존 기록은 호환 경로에 보존하며 옛 detector 실행 코드가 없어도 동작하도록 구성했습니다.

[Windows 설치](WINDOWS.md) · [Linux 설치](INSTALL.md) · [보안](SECURITY.md)

## 설치 파일

설치 EXE는 위 빌드의 실행 파일을 가져와 Inno Setup으로 묶습니다.
별도 [설치 작업](https://github.com/spac0301/pawline/actions/workflows/windows-installer.yml)에서 임시 경로에 설치한 모든 파일의 해시를 원본과 비교하고 제거를 확인합니다.
실제 통과 기록은 릴리스에 연결합니다.
