# Security and sharing scope

Reviewed 2026-09-29. This is a local source review and bounded test result,
not a claim that the application is vulnerability-free.

## Supported environment

The exercised host is Linux, one trusted desktop user, GTK3/X11 or XWayland.
Windows support code uses Qt, psutil, msvcrt locks and owner-only Win32 ACLs.
Windows CI checks the Qt frontend, current-user-only file ACLs, packaged
executables, and installation/removal. Real recipient desktop integration is
a separate validation boundary.
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
overflows or its reducer fails, affected metadata becomes explicitly unknown.
From 0.2.7, fresh connections can resume observation in the same adapter while
old connections remain untrusted. Loss diagnostics contain counts and sizes,
not payloads. Filesystem write failures are isolated and publication can recover.
History, session summaries and connection parsers have explicit retention caps.
The worker holds queued raw messages only in bounded memory and never writes
their request/answer contents to disk.

Tests cover rejected proxy credentials, private file modes and cleanup,
compressed payload bounds, byte-preserving HTTP/SSE fallback, unknown framing,
session correlation and absence of synthetic prompt/output data in snapshots.
A recipient must enable response observation in their own Codex installation
before its real activation is verified. Isolated transport tests do not establish
that deployment boundary.

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

## Windows runtime maintenance

Version 0.2.4 bundled Python 3.12.10. The Windows runtime in 0.2.5 is pinned to
Python 3.13.15 in `windows/python-version.txt`; builds and bundle verification
check that exact version. Python's source-only security releases after 3.12.10
are not supplied by merely requesting the latest 3.12 Windows installer.
See [Python 3.13.15](https://www.python.org/downloads/release/python-31315/) and
[the 3.12 release schedule](https://peps.python.org/pep-0693/).

This is runtime maintenance, not a finding of demonstrated exploitation of
Pawline. Linux uses the recipient's distribution-managed Python/GTK runtime;
security backports must be assessed using distribution packages, not the Python
version string alone.

The public 0.2.4 source and portable archives were checked on 2026-09-29 for
private account/state/log paths and private-key PEM text; no matches were found.
OSV queries for the 12 checked Python dependency/build packages returned no
matching advisories. This query does not cover every bundled native library or
prove the absence of vulnerabilities.
