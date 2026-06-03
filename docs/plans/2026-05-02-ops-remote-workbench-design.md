# OPS Remote Workbench Design

> Status: Reference Design
> Date: 2026-05-03

## Purpose

This file is retained as the approved architecture reference for the server
workbench workstream.

It should be used for:

- scope boundaries
- terminal/SFTP/admin-only design intent
- multi-hop remote access expectations

It should not be used as the current implementation status tracker.

## Original Workstream Goal

Upgrade the existing server detail and file-management features into a single
admin-only server workbench with:

- web terminal access
- one-shot SSH command execution
- SFTP-based file management
- multi-hop jump-host proxy support
- key-based authentication support
- consistent audit coverage

## Design Summary

Recommended architecture:

1. `app/services/remote_access.py`
2. `app/services/terminal_sessions.py`
3. `app/api/servers.py`
4. tabbed frontend workbench under `/servers/:name`

Expected tabs:

- Overview
- Commands
- Terminal
- Files

Key design rules:

- browser never talks SSH directly
- terminal via backend WebSocket proxying
- file operations via backend REST
- all workbench capabilities are admin-only
- audit must include hop and auth metadata

## Multi-Hop Connection Model

```text
operator session
  -> hop[0]
    -> hop[1]
      -> ...
        -> hop[n]
          -> target server
```

Each hop resolves:

- host
- port
- username
- auth mode
- key source or password source

## Status Note

For current code completion and verification status, use:

- `docs/plans/README.md`
- `docs/plans/2026-05-01-runtime-source-of-truth.md`
- `docs/plans/2026-05-03-ops-stabilization-and-iteration-plan.md`
