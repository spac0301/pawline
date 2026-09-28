# Pawline

조용히 곁에서 살펴보는 GPT·Claude 데스크톱 펫.


GPT·Claude의 작업 상태, 관측된 응답 모델명, 입력 캐시 사용량을 작은 데스크톱
펫과 정보창에서 확인하는 도구입니다. 상태 확인을 위해 대화창에 메시지를
추가하거나 모델을 호출하지 않습니다.

- **Linux:** GTK/X11 환경에서 실행과 화면 동작을 확인했습니다.
- **Windows:** Qt 소스와 EXE 빌드 스크립트를 제공합니다. 네이티브 Windows
  실행 검증과 완성된 EXE 배포는 아직 완료하지 않았습니다.
- **캐시 표시:** 마지막 사용량 기록의 입력 토큰 재사용률이며, 서버 캐시의
  남은 유지 시간이나 자동 캐시 유지 기능이 아닙니다.

## 다운로드와 설치

이 저장소의 소스 ZIP 또는 clone으로 시작합니다. Python과 OS별 의존성 설치는
[Linux 안내](INSTALL.md), [Windows 안내](WINDOWS.md)를 따릅니다.
프로그램 소스와 사용자 계정의 설정·로그는 별개입니다.

현재 화면에 사용한 흰 고양이는 **Fluff — Sejal R.**입니다.
[Petdex 원본 소개·다운로드](https://petdex.dev/pets/fluff)에서 펫을 받은 뒤
`pet.json`과 `spritesheet.webp`가 들어 있는 폴더를 지정합니다.

```sh
# Linux: 런타임 준비 후
./launch-pet.sh --pet-dir /path/to/fluff
```

```powershell
# Windows: 런타임 준비 후
.\launch-pet.cmd --pet-dir "C:\path\to\fluff"
```

Fluff 이미지의 재배포 허가를 확인하지 못했으므로 이 저장소와 소스 ZIP에는
이미지를 넣지 않았습니다. 원본과 현재 사용 파일의 일치 확인 및 출처는
[펫 출처 안내](PET_ASSET_NOTICE.md)에 기록했습니다.

프로그램 코드는 [MIT](LICENSE)로 공개하며, 재사용 코드·폰트·로고의 고지와
별도 조건은 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)에 모았습니다.

## 구현 및 검증 범위

Local desktop companion for actual Codex request/response metadata and passive
Codex/Claude usage. This directory is the complete application; it does not
import or execute the old `codex-routing-detector` checkout or its virtualenv.
Native conversation history is preserved; the old executable checkout is not
required.

공개 이름은 Pawline입니다. 기존 설치와의 호환을 위해 내부 Python 모듈,
`FLUFF_*` 환경변수와 기존 데이터 경로는 유지합니다.

## Components

- `activity.py`: one background collector reads native session usage and task
  lifecycle, and follows the current Claude owner through explicit role bindings.
  It never calls a model or maintains cache warmth.
- `catalog.py`, `storage.py`, `views.py`: read-only task identity and shared
  metadata projections. The pet consumes the collector's catalog instead of
  querying SQLite again. Unchanged snapshots are decoded once.
- `capture.py`, `proxy.py`, `http_stream.py`, `live.py`, `records.py`: local
  desktop adapter and metadata interpretation. WebSocket and HTTP/SSE responses
  are correlated with their own request and explicit thread ID. The proxy is
  loopback-only, verifies upstream TLS and preserves original wire bytes.
- `pet.py`, `menu.py`, `pet_state.py`: the Linux panel, shared GPT/Claude
  selectors, placement and animations. A provider's title, model and usage share
  the same value column. On hover, only a long title's single-line strip
  extends in the same colors/type; card size and all other rows stay fixed.
- `vendor/claude-pet`: MIT-licensed rendering/movement code, with the existing
  local config-directory override documented in `LOCAL_CHANGES.md`. Its
  automatic updates, hooks, model calls, terminal launch and session UI are not
  activated by Fluff.

`qt_pet.py` provides the Windows frontend. `platform_support.py` isolates file
locks, private Windows ACLs and explicit process identity. `presentation.py` and
`layout.py` share cache wording and placement with GTK.

The standalone probe CLI, detector GUI, update checker and terminal launcher
are not part of the active application.

**Platforms:** Linux GTK/X11 (including XWayland) and a Windows Qt frontend.
The Windows code is supplied for recipient verification, not as a verified
Windows binary. See [WINDOWS.md](WINDOWS.md). Linux remains GTK by default.

## Entry points

`launch-pet.sh` starts the pet and the shared user collector. `run.py activity`
is the service command. `launch-capture.sh` is the Codex desktop's CLI adapter.
Register the native CLI with `--fluff-configure-cli PATH` and set
`FLUFF_DESKTOP_CAPTURE=1` only on the desktop launch, as described in `INSTALL.md`.
Browser/config helpers delegate to the native CLI without starting an observer.
The native policy checks run normally; no policy setting is weakened or replaced.

Only the pet and collector can be reloaded independently. Replacing the
desktop adapter requires the **next normal Codex app launch**. Never terminate
its app-server while work is running just to refresh this display.

On another Linux machine, use its own Python 3.10+, GTK3/PyGObject/cairo and
virtualenv, install `requirements.lock`, and supply a trusted local sprite pack
via `launch-pet.sh --pet-dir PATH`. Personal sprite packs, installed runtimes,
native logs, credentials, role bindings and machine-specific app launchers are
not distributed. Integrating capture also requires that machine's bundled
Codex CLI path; do not copy another person's complete home-directory config.

The white cat shown in the local installation is **Fluff**, submitted to Petdex
by **Sejal R.** Its artwork is not covered by the renderer's MIT license.
The upstream asset package and public pages did not establish a redistribution
license, so this archive includes source attribution and the original download
link instead of the sprite. See [PET_ASSET_NOTICE.md](PET_ASSET_NOTICE.md).

## Data compatibility

The existing private directories are deliberately retained:

- `~/.local/state/codex-routing-detector/{desktop,activity,role-sources}.json`
- `~/.config/codex-routing-detector/pet/panel.json`

These are compatibility data paths, not executable dependencies. The meeting
board reads `activity.json` using its existing authenticated, exact-session
integration. One service and `activity.lock` prevent duplicate collectors.
UI refreshes never add messages to conversations.

Linux Claude discovery also recognizes an existing CLI resumed from within the
terminal, even if its original command has no `--session-id`. Native PID/start
registration is verified before reading the exact session log. A newer native
session identity takes precedence over stale launch arguments. Requested model
settings and observed response models remain separate.

The selector contains current tasks, recent completed GPT tasks for five
minutes, and live Claude processes/current logical roles. Historical Claude
logs, title-generation work and subagents do not become separate menu rows.
An ended fixed selection expires instead of permanently pinning a dead row.
The files that contain history are not deleted.

## What the numbers mean

`입력 캐시 98.9%` means cached input tokens divided by total input tokens in the
last usage record. It is not a percent of requests, a cost saving, cached output,
or proof that the server still holds the cache. The adjacent age is the age of
the local usage record, not a countdown until eviction.

For Claude, total input includes uncached input + cached reads + new cache
writes. Only cached reads count as hits. New output is shown separately in the
details tooltip. Missing usage is unknown, never zero.

Returned model fields are server metadata, not proof of a hidden model engine.
Configured/requested models never substitute for unobserved response models.

## HTTP fallback

The old monitor missed responses when Codex fell back from WebSocket to HTTP.
The new observer handles Content-Length JSON requests (identity, gzip, deflate,
zstd), HTTP/1 response chunking/content lengths and JSON/SSE bodies, including
split UTF-8 and streaming updates. Other request framing (`Expect`, chunked or
pipelined requests) is transparently relayed without invented observations.
Unsupported response framing is relayed blindly and marked unobserved.
An observer parse failure does not terminate the underlying conversation.
Connection failure itself and display observability are separate problems.

The desktop adapter requires a random per-launch proxy credential before any
outbound connection. Existing proxy configuration is rejected instead of
silently bypassed. This version does not implement enterprise proxy chaining.

Headers, prompts and generated text are never saved by this monitor. Only the
selected metadata is published. Temporary local CA material is private and
removed on normal adapter shutdown; no system certificates are installed.
See [SECURITY.md](SECURITY.md) for the trust boundary and distribution limits.

## Verification

```
PYTHONNOUSERSITE=1 .venv/bin/python -B -m unittest discover -s tests -p 'test_*.py' -v
```

`tests/verify_activity_ui.py OUTPUT PRIVATE_XVFB` exercises our widgets on an
isolated display, including shared menu geometry, long titles, both themes,
screen edges, expired selections and actual main-loop rendering. It does not
capture or interact with the user's desktop.

The HTTP tests use local TLS stub servers only: lost WebSocket, compressed
HTTP/SSE on pooled connections, byte-for-byte passthrough, incremental updates,
unknown framing, metadata privacy, and explicit session correlation. No real
provider calls are required.

### 0.2.1 observation boundary

Socket callbacks enqueue into a bounded inbox (512 events and at most 16 MiB
of conservatively estimated text). One worker aggregates metadata and publishes
at one-second intervals. Filesystem IO does not run in a relay callback.
Request metadata is enqueued before the corresponding bytes are sent, preserving
request/response ordering; server bytes are forwarded before observation.
Callback exceptions never close a relay socket. A full inbox or a reducer fault
disables observation for that adapter instance and publishes unknown status.
It never blocks real traffic to preserve telemetry or invents missing metadata.

The reducer retains at most 256 history rows, 128 session summaries and 128
connection states, with 30-minute idle cleanup. Closed connection parsers and
indexes are discarded. Current-request records have a separate bounded index;
cumulative mismatch counts are maintained per retained session, rather than
rescanning the full history for each snapshot. These are local diagnostic
retention limits, unrelated to the provider's prompt-cache TTL.

Two unacknowledged requests, or a new request while another response remains
unfinished on one WebSocket, are marked unobserved. FIFO ordering alone is not
treated as proof of response identity. HTTP exchanges retain their individual
identity. HTML/text HTTP failures report a bounded status code; error-page
contents are not published. SSE line parsing scans each decoded chunk once.

See `CHANGELOG.md` for scope and remaining review items. Use the latest source
archive for sharing; older archives only document their original releases.

The information window's `⋯` menu holds theme and pin controls. The pet's
right-click menu holds petting, walking and quit. The latter is temporary and
does not have its own pin state. These action definitions are shared by GTK and
Qt; only the pet needs reloading when applying this menu change.

The private venv uses the installed system GTK/cairo and the exact binary
dependencies in `requirements.lock`. No global Python packages are modified.

## References and attribution

- [claude-pet](https://github.com/HaneulOscarLee/claude-pet): MIT upstream pet
  rendering and movement. License retained beside the vendor copy.
- [codex-routing-detector](https://github.com/darkdarkcocoa/codex-routing-detector):
  MIT upstream wire transport and response interpretation, adapted here as a
  standalone library. `UPSTREAM-DETECTOR-LICENSE` is retained.
- [GTK Grid](https://docs.gtk.org/gtk3/class.Grid.html) and
  [SizeGroup](https://docs.gtk.org/gtk3/class.SizeGroup.html): shared column and
  control-size rules. The pet is GTK, not a React application.
- [OpenAI Apps SDK UI](https://github.com/openai/apps-sdk-ui): official OpenAI
  vector mark, with MIT attribution in `assets/providers`.
- [Anthropic press kit](https://www.anthropic.com/press-kit): official Claude
  Spark geometry and clay color. Marks identify providers, not endorsement.
- [Pretendard](https://github.com/orioncactus/pretendard): bundled OFL font,
  registered only inside this process.
- [Python HTTPResponse](https://docs.python.org/3/library/http.client.html):
  standard HTTP framing and streaming reader.
- [OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)
  and [Claude prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching):
  input-token and retention semantics. API policy is not direct evidence of
  native app cache residency.
