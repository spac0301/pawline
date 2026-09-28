# Security and sharing scope

Reviewed 2026-09-29. This is a local source review and bounded test result,
not a claim that the application is vulnerability-free.

## Supported environment

The exercised host is Linux, one trusted desktop user, GTK3/X11 or XWayland.
Windows support code uses Qt, psutil, msvcrt locks and owner-only Win32 ACLs.
Its Qt frontend was exercised offscreen on Linux, while native Windows ACLs,
process access, executables and real desktop behavior are for recipient verification.
The Windows-only ACL test is skipped on Linux rather than presented as passed.
Both collectors depend on native app log formats and explicit session identity.

## Network and local trust

The usage collector performs no model or network calls. Its user service only
permits AF_UNIX sockets. The optional response observer is different: it is a
local TLS-intercepting proxy that necessarily sees request/response bytes in
memory. Use it only if this is acceptable on the host and network.

The observer binds `127.0.0.1` on an ephemeral port. The desktop adapter enables
a random per-launch Basic proxy credential; an unauthenticated client is
rejected before an upstream socket is opened. The credential exists in the
child environment, never in published snapshots. This does not isolate the
monitor from malicious code running as the same OS user or from root.

Upstream certificate and hostname verification remain enabled. Only the
configured Codex host is decrypted; unrelated HTTPS traffic preserves the
original TLS tunnel. The default is `chatgpt.com`. This is a trust boundary,
not proof of the identity of a hidden model behind a returned model field.

An existing upstream proxy configuration is rejected, not overwritten. Proxy
chaining, enterprise deployment and organization-managed certificates require
separate compatibility review. No root CA is added to the system trust store.

## Data and files

Only task identity, model fields, status and token counts are published. Raw
prompts, generated content, headers, cookies and provider credentials are not
written to monitor snapshots. Metadata itself can still be sensitive: task
titles and usage reveal work activity. Do not share `activity.json`,
`desktop.json`, private app configs, logs or debug output indiscriminately.

On Linux, snapshot files are created atomically with mode 0600; temporary CA
directories are 0700 and key/certificate files 0600. On Windows, newly created
snapshot files and CA directories get an explicit current-user-only DACL before
private contents are written. POSIX chmod is not presented as a Windows ACL. Normal exit removes the
temporary CA. Forced termination or a host crash can leave temporary material;
operating-system/user cleanup remains necessary in that case.

The scope is application-owned files. This review does not assert that all
native Codex/Claude logs elsewhere on the machine have private permissions.
No broad chmod of the user's home directory is performed.

## Parsing and execution

Incoming payloads are parsed as JSON/SSE/WebSocket data, not evaluated as code.
GTK text is plain text; the limited markup path escapes user-controlled labels.
Children launch with fixed argv lists, not a shell assembled from task titles.
The observer has 64 MiB message limits, including post-decompression limits.
Observation failure leaves the actual wire stream intact and marks metadata
unknown. Unknown/uncorrelated model fields are never substituted from settings.

Version 0.2.1 addresses the independently reproduced 0.2.0 WebSocket callback
failure, which could interrupt the real relay. Relay callbacks now enqueue into
a bounded worker; snapshot IO never runs on the socket pump. If observation
overflows or its reducer fails, that capture instance becomes explicitly
unobserved. Filesystem write failures are isolated and publication can recover.
History, session summaries and connection parsers have explicit retention caps.
The worker holds queued raw messages only in bounded memory and never writes
their request/answer contents to disk.

Tests cover rejected proxy credentials, private file modes and cleanup,
compressed payload bounds, byte-preserving HTTP/SSE fallback, unknown framing,
session correlation and absence of synthetic prompt/output data in snapshots.
The native bundled Codex binary still needs its next normal-launch observation
before that activation is called verified. Local mock tests are not that proof.

## Distribution

The sharing archive is built from an explicit source/asset/license allowlist.
It excludes `.venv`, user profiles, caches, logs, captured traffic, CA/key
material, role-source bindings and local deployment/review artifacts. Personal
sprite packs are not bundled. Original licenses, commit provenance and local
adapter changes are retained. No automatic downloading installer or updater is
enabled by the application.

The 13 pinned Linux/Windows runtime dependency versions were queried against
OSV on 2026-09-28; no matching entries were returned. This is a database
snapshot, not proof that there are no vulnerabilities. The optional PyInstaller
build tool, its transitive dependencies, system GTK/Python libraries and future
advisories are outside that result. Recipients must maintain OS packages and
review dependency updates. The vendor's MIT source and font/logo attribution
are kept in the archive; branding is not an endorsement.
