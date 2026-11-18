# Changelog

All notable changes to `iowap-client-mcp` are documented here
(Keep a Changelog format, SemVer versions). Releases before 0.1.5
predate this file — see `git log` for their history.

## [0.1.6] — 2026-11-18

### Changed (T-274 — MCP error transparency)

- `task_submit`, `task_result` and `artifact_download` convert
  `NodeCliError` into a structured result
  `{'error': {'http_status': int | None, 'detail': ..., 'raw': ...}}`
  instead of raising, so MCP hosts render the relay detail directly
  instead of a bare "Error executing tool". `detail` carries the relay
  message when the runner's embedded stderr tail parses to an
  `HTTP <code>: <body>` line (`parse_relay_error`); with no parseable
  status — transport errors such as a missing node-cli binary, or a
  bare `HTTP 500:` — `detail = raw` = the stderr tail.
- `task_result` keeps its pre-existing `not found` handling
  (`status: "not_found"` + hint) verbatim, now matched on the stderr
  tail so the runner's own `node-cli not found on this host` transport
  preamble cannot masquerade as a missing task. Validation errors
  (payload shape, node ID, capability allowlist, `FileExistsError`)
  still raise.
- First release with a `CHANGELOG.md`.
