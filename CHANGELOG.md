# 0.2.4 — preserve native CLI access for browser/config helpers

The desktop adapter path was inherited by browser tools, while its required
`CODEX_ROUTING_REAL_CLI` variable was removed from their filtered environment.
The adapter exited before the native policy/config request could run; Browser
Use reported this generically as an unavailable admin policy check.

An installation can now save its native executable path with
`--fluff-configure-cli PATH`. Desktop observation requires an explicit
`FLUFF_DESKTOP_CAPTURE=1` flag, consumed before starting the native process.
Other helper invocations go to the real CLI without a new proxy or snapshot
publisher. No browser security policy or approval requirement is modified.

# 0.2.3 — separate information-window and pet menus

Public source packaging adds the project MIT license, third-party notices,
Fluff's original asset link, source checks and a manual experimental Windows
build. Fluff artwork remains excluded. Test fixtures that emulate Linux /proc
are labelled Linux-only; Windows permissions use the existing native DACL test
instead of a POSIX mode assertion. These packaging changes do not alter the
running observer or panel behavior.

The information window's options contain theme and pin controls. The pet's
temporary context menu contains petting, walking and quit. Pinning applies only
to the information window; choosing or dismissing a pet action does not change
it. Both GTK and Qt use the same action definitions, close the previous menu
when another opens, and keep pet controls clear of the pet and information
window. Qt also hides retired entries before their deferred deletion, preventing
old and new menu items from overlapping during a menu change.

Only the pet needs to be reloaded for this UI change. The collector, capture
adapter and model sessions can keep running. Native Windows interaction still
requires the recipient's check; Qt widget tests were run offscreen on Linux.

# 0.2.2 — recognize resumed Claude CLI sessions

On Linux, a CLI resumed interactively can keep argv as just `claude`. Discovery
now reads Claude's native `sessions/<pid>.json`, validates PID, process start
ticks, host/PID namespace and the registration timestamp, and follows that
current session UUID. Stale/exited registrations are rejected. An interactive
resume takes precedence over the process's original session argument; the old
session's launch options are not reused as the new one's settings.

Both platforms share parsing for split and equals-form CLI options and native
or npm CLI identification. Windows retains explicit-argument binding until its
native registration/process-start representation is verified on Windows.
Response model, effort and token counts still come from that session's own
native log. An unknown requested model is never filled from the response model.

The Linux activity collector can be reloaded independently. No Codex capture
or Claude CLI restart is needed for this fix. The capture transport code is
unchanged from 0.2.1.

# 0.2.1 — observation isolation and bounded retention

Independent review found that a WebSocket observer callback could close the
real relay, and that aggregation/history retention grew with long sessions.
The callback failure was independently reproduced before editing.

This release isolates callback errors, moves reducer/snapshot work to a bounded
worker, limits retained metadata and discards closed connection state. Snapshot
lookup uses incremental mismatch counters. It also handles non-JSON HTTP error
responses, removes repeated SSE suffix copying and refuses ambiguous WebSocket
request/response associations. Existing protocol, privacy, lifecycle and UI
regression checks remain applicable; native Windows verification is delegated
to the recipient.

This is the first corrective slice, covering review items 1–3 and the related
HTTP/SSE and association boundary. It does not claim to resolve every UI or
optimization suggestion. Activity snapshot write frequency, GTK/Qt policy
duplication, panel drag/anchor behavior, elapsed-time display and lower-impact
rendering optimizations remain follow-up work. Random ambient pet animation is
intentional; the fixed panel carries task status.

Several review statements were corrected against the source: activity.json
already has `schema: codex_local_activity_v1`; both frontends persist mismatch
alert state; Fluff overrides quit without calling the vendor PID-file cleanup;
status labels accompany colored dots. Broader schema validation and shared
frontend policies can still be improved.

Switching a running desktop to the new capture requires a normal full app
restart and verification of its own fresh responses. Local tests do not prove
that activation. Existing conversations and native histories are preserved.
