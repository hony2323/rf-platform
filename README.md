# RF Platform

Monorepo for remote live RF spectrum viewing and multi-sensor aggregation.

## Projects

- `protocol/` — Shared contract source-of-truth
- `agent/` — SDR access, FFT generation, buffering, outbound connection
- `server/` — Auth validation, session management, frame validation, fanout
- `web/` — Remote live viewing UI
- `docs/` — Architecture, protocol, and product documentation

## Development

Each project has its own `pyproject.toml` / `package.json`. Tests live inside each project.
