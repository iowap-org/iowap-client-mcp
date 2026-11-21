# iowap-client-mcp

MCP toolkit for IOWAP: run a curated IOWAP toolset from any MCP-speaking agent —
without teaching every agent the node CLI's pitfalls.

## The problem

IOWAP is a distributed capability mesh: the [relay](https://github.com/iowap-org/iowap-server)
routes tasks, [nodes](https://github.com/iowap-org/iowap-node) execute them.
Today, an AI agent that wants to use IOWAP shells out to `iowap-cli --json`.
That works, but every agent has to relearn the same pitfalls itself: response
envelopes that must be unwrapped, node IDs vs. names, the global flag position,
which commands are safe and which mutate the node. Knowledge ends up in one
agent's skill file instead of in code.

## The answer

`iowap-mcp` is an MCP stdio server that wraps `iowap-cli` behind a small,
curated toolset:

| Tool | Purpose |
|------|---------|
| `relay_status` | Relay health, queue depth, task counters |
| `node_list` | Fleet overview (IDs, status, capabilities, load) |
| `capabilities_list` | What the relay can do — including input schemas **and** the node IDs that own each capability |
| `task_list` | Filtered task query (submitted_by, owner_node_id, status csv, created window) — compact rows, newest-first, first `limit` (default 50) |
| `task_submit` | Submit a task against a capability (optional short wait) |
| `task_result` | Poll a task result |
| `artifact_download` | Fetch a task artifact to a local path |
| `docs` | Read the live relay documentation |

The node CLI remains the source of truth for authentication and execution —
the server adds zero credential handling of its own. Destructive commands
(register, update, daemon control, capability publishing) are deliberately not
exposed.

**Trust model:** whoever can launch `iowap-mcp` on the host acts with the
host's node identity — an MCP client spawning this server is a trusted
executor. Environment filtering of the stdio subprocess is host behavior;
the package relies on the node's on-disk identity and enforces none of it
itself.

**Narrowable execution:** operators can restrict which capabilities
`task_submit` may fire with `IOWAP_ALLOW_CAPS=cap.one,cap.two` (csv env,
unset means all) — with `hermes mcp add` that is an `--env` line away.
`artifact_download` writes a file: an existing path is refused unless
`overwrite: true` states the replacement explicitly.

Scope is one host: the server binds the local node to local MCP clients.
POSIX host (Linux/macOS) — no Windows support.

## HTTP transport (optional)

For remote-topology setups (e.g. the node runs in a container on Docker
Desktop and the agent lives on the same host), `iowap-mcp --http` serves the
same toolset via the MCP Streamable HTTP transport at `/mcp`:

```bash
export IOWAP_MCP_BEARER='<long random token>'   # required — no token, no start
iowap-mcp --http [--port 7781]                  # binds 127.0.0.1 by default
```

- **Bearer auth:** every request must carry `Authorization: Bearer <token>`;
anything else gets `401`. The token comes from the `IOWAP_MCP_BEARER` env
— the process **fails closed**: without it (or with a blank value) the
HTTP transport exits with an error instead of starting unauthenticated.
- **stdio stays the default:** without `--http` the server behaves exactly
as before (spawned by the MCP client, no env, no port). Existing client
configs are unaffected.
- **Same toolset, same guards:** HTTP serves the identical curated 8 tools —
`IOWAP_ALLOW_CAPS` and the artifact-overwrite refusal apply unchanged.
Point your MCP client at `http://127.0.0.1:7781/mcp` with the Bearer token.

## Install

```bash
pip install "iowap-client-mcp[mcp]"
```

…or let the local node install it: `iowap-cli mcp install` (own venv,
client-neutral, PyPI registry). Requires a node installation of IOWAP on
the same host (see [iowap-node](https://github.com/iowap-org/iowap-node)) —
the server shells out to the installed `iowap-cli`. Both wheels are regular
PyPI packages: `iowap-cli update check/apply` keeps node and toolkit current
together (toolkit upgrades need no service restart — the server process
reloads on the next client spawn).

## Wire it into an MCP client

Example for [Hermes Agent](https://github.com/NousResearch/hermes-agent) (`~/.hermes/config.yaml`):

```yaml
mcp_servers:
  iowap:
    command: "iowap-mcp"
```

Or: `hermes mcp add iowap --command iowap-mcp`

Any other MCP client uses the equivalent stdio spawn (command = `iowap-mcp`).
Tested with Hermes Agent (`hermes mcp add` + `/reload-mcp`, live against a
running relay) and Codex (OpenAI) — same stdio command, zero per-client code.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e ".[mcp,dev]"
.venv/bin/pytest
```

## License

MIT — see [LICENSE](LICENSE).