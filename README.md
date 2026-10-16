# iowap-client-mcp

MCP toolkit for IOWAP: run a curated IOWAP toolset from any MCP-speaking agent —
without teaching every agent the node CLI's pitfalls.

## The problem

IOWAP is a distributed capability mesh: the [relay](https://github.com/iowap-org/iowap-server)
routes tasks, [nodes](https://github.com/iowap-org/iowap-node) execute them.
Today, an AI agent that wants to use IOWAP shells out to `node-cli --json`.
That works, but every agent has to relearn the same pitfalls itself: response
envelopes that must be unwrapped, node IDs vs. names, the global flag position,
which commands are safe and which mutate the node. Knowledge ends up in one
agent's skill file instead of in code.

## The answer

`iowap-mcp` is an MCP stdio server that wraps `node-cli` behind a small,
curated toolset:

| Tool | Purpose |
|------|---------|
| `relay_status` | Relay health, queue depth, task counters |
| `node_list` | Fleet overview (IDs, status, capabilities, load) |
| `capabilities_list` | What the relay can do — including input schemas **and** the node IDs that own each capability |
| `task_submit` | Submit a task against a capability (optional short wait) |
| `task_result` | Poll a task result |
| `artifact_download` | Fetch a task artifact to a local path |
| `docs` | Read the live relay documentation |

The node CLI remains the source of truth for authentication and execution —
the server adds zero credential handling of its own. Destructive commands
(register, update, daemon control, capability publishing) are deliberately not
exposed.

Future: a Streamable HTTP variant for remote agents, running under a dedicated
service node identity.

## Install

```bash
pip install "iowap-client-mcp[mcp]"
```

Requires a node installation of IOWAP on the same host (see
[iowap-node](https://github.com/iowap-org/iowap-node)) — the server shells out
to the installed `node-cli`.

## Wire it into an MCP client

Example for [Hermes Agent](https://github.com/NousResearch/hermes-agent) (`~/.hermes/config.yaml`):

```yaml
mcp_servers:
  iowap:
    command: "iowap-mcp"
```

Or: `hermes mcp add iowap --command iowap-mcp`

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e ".[mcp,dev]"
.venv/bin/pytest
```

## License

MIT — see [LICENSE](LICENSE).