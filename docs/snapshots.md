# Local snapshots

Pawline 0.2.6 separates snapshot contents from collector liveness. A collector
replaces `activity.json` or `desktop.json` only when its contents change.
An unchanged snapshot is kept alive by a small `activity.heartbeat.json` or
`desktop.heartbeat.json` file, replaced every two seconds. All files are private
to the current OS user and replaced atomically in the same directory.

The data snapshot retains the existing fields and adds:

```json
{"version": 2, "snapshot_id": "opaque-unique-id", "updated_at": 100.0}
```

Its heartbeat contains only:

```json
{"version": 2, "snapshot_id": "opaque-unique-id", "updated_at": 112.0}
```

`updated_at` in the data file means publication time. A reader may use the
heartbeat time for collector freshness only when both versions are 2, both
nonempty snapshot IDs match, and the heartbeat timestamp is a finite number
greater than or equal to the data timestamp. The heartbeat never updates a
request's `observed_at`, token counts, model, or response identity.

Read the data first, then its heartbeat. Every content replacement and publisher
restart gets a new snapshot ID. Missing, malformed, older or mismatched
heartbeats cannot extend a snapshot's freshness. Fall back to the data's own
publication time in those cases, including the short interval between the two
atomic replacements. A missing data file remains unavailable even if a heartbeat
exists. After the collector stops, existing freshness deadlines still expire.

Version 1 snapshots have no heartbeat requirement and keep their original
`updated_at` meaning. Readers must support both versions before enabling a
version 2 publisher. Pawline's cached `SnapshotReader` and uncached
`read_snapshot` implement the same merge rules; the former rereads only files
whose inode, modification time or size changed.

Consumers outside Pawline, including the meeting board, should implement these
same checks or use the reader. Do not use a sidecar timestamp alone: a delayed
heartbeat from another process or an older snapshot must not make old data look
current. A board upgrade must precede activation of this publisher.
