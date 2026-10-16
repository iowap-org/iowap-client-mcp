# Security Policy

## Supported versions

Only the latest release is supported.

## Reporting a vulnerability

Please do **not** open a public issue for security problems.
Report privately via GitHub Security Advisories on this repository
(**Security → Advisories → New draft security advisory**), or contact a
maintainer through the IOWAP org.

Include: affected version/commit, reproduction steps, and expected vs. actual
behavior. You will get a response within a reasonable time frame.

## Scope notes

- The `iowap-mcp` server intentionally exposes no destructive surface
  (registration, updates, daemon control, capability publishing).
- The server does not manage credentials; it relies on the installed node CLI
  and its token lifecycle.