# Inspection Profile Workflow Design

Goal: make recurring server inspection easier for many groups and 58+ servers while preserving MCP/tool-token guardrails.

## Design

Use a lightweight "inspection profile" layer above the existing batch inspection service. Profiles are stored in `config_kv` for the first version, so no migration is required. A profile defines target filters, categories, concurrency, timeout, report mode, and confirmation policy. The backend exposes list, preview, and run operations through HTTP and MCP.

The preview operation resolves the actual target servers before execution. It returns eligible/skipped servers, estimated settings, and a deterministic confirmation phrase. The run operation resolves the same profile again, validates the supplied phrase, executes the existing batch inspection, and automatically generates one combined report for all successful run ids.

The confirmation phrase is optimized for frequent use but still context-bound:

```text
RUN INSPECTION <profile-id> <target-count> <fingerprint>
```

The fingerprint is derived from the profile id, target identities, categories, and execution knobs. This keeps the phrase short enough to copy while preventing a stale confirmation from being reused for a different target set.

## Default Profiles

- `daily-lite`: all online servers, lightweight checks: disk, memory, service status, backup.
- `weekly-security`: all online servers, security checks: login, account, command history, process/ports, firewall.
- `monthly-full`: all online servers, all built-in server categories, stricter concurrency, full report.
- `crypto-test-daily`: crypto group plus test-name keyword, lightweight checks for routine repeated use.

## API / MCP

HTTP:

- `GET /api/v2/inspection/profiles`
- `POST /api/v2/inspection/profiles/preview`
- `POST /api/v2/inspection/profiles/run`

MCP/HTTP tools:

- `ops.inspection.profile.list`
- `ops.inspection.profile.preview`
- `ops.inspection.profile.run`

## Error Handling

- Missing profile: 404.
- Empty resolved target set: 400 with skipped target details.
- Mismatched confirmation phrase: 428 with `expected_confirm_text`.
- Batch execution errors are returned per server; successful runs still generate the combined report.

## Testing

Add contract tests for profile defaults, target preview filtering, confirmation phrase stability/change detection, run execution wiring, MCP schema exposure, and report generation handoff.
