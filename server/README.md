# RF Platform - Server

Central relay server for live RF spectrum streaming. Agents push FFT frames over WebSocket; browser viewers subscribe and receive them in real time. SQLite stores users, agents, and tokens.

## Quick start

```bash
cd server
pip install -e ".[dev]"

# Create the first user
python -m server.app.bootstrap --email admin@example.com --password secret

# Start the server
uvicorn "server.app.api:create_app" --factory --reload
```

The server listens on `http://0.0.0.0:8000` by default. Interactive API docs are at `http://localhost:8000/docs`.

## Configuration

All settings are read from environment variables. Defaults work for local development.

| Variable | Default | Description |
|---|---|---|
| `RF_DB_PATH` | `rf_platform.db` | SQLite file path |
| `RF_HOST` | `0.0.0.0` | Bind address (used when running via the module entry point) |
| `RF_PORT` | `8000` | Port (used when running via the module entry point) |
| `RF_SESSION_SECRET` | `dev-secret-change-in-production` | HMAC key for session cookies - change in production |
| `RF_SESSION_COOKIE_NAME` | `session` | Cookie name |
| `RF_SESSION_COOKIE_SECURE` | `` (false) | Set to `1` or `true` behind HTTPS - also switches cookie to `SameSite=None` for cross-origin use |
| `RF_CORS_ORIGINS` | `` (disabled) | Comma-separated allowed origins, e.g. `https://app.example.com,https://preview.vercel.app`. Leave unset for same-origin deployments. |

## Bootstrap

The bootstrap command creates a user account in the database. Run it once before starting the server for the first time.

```bash
python -m server.app.bootstrap --email admin@example.com --password secret

# Custom DB path
python -m server.app.bootstrap --email admin@example.com --password secret --db-path /data/rf.db
```

Exits with status 1 if the email already exists.

## API surface

See `../docs/server_api_contract.md` for frozen JSON shapes.

| Endpoint | Auth | Description |
|---|---|---|
| `POST /auth/signup` | - | Create account, sets session cookie |
| `POST /auth/login` | - | Log in, sets session cookie |
| `POST /auth/logout` | cookie | Clear session cookie |
| `GET /me` | cookie | Current user |
| `DELETE /me` | cookie | Delete current user account and owned resources |
| `GET /agents` | cookie | List own agents |
| `POST /agents` | cookie | Create agent |
| `GET /agents/{id}` | cookie | Get agent |
| `GET /agents/{id}/status` | cookie | Live session status |
| `POST /agents/{id}/tokens` | cookie | Mint agent token |
| `GET /agents/{id}/tokens` | cookie | List tokens |
| `POST /agents/{id}/tokens/{tid}/revoke` | cookie | Revoke token |
| `WS /ws/agent` | Bearer token | Agent streaming connection |
| `WS /ws/viewer` | cookie | Browser viewer connection |

## Tests

```bash
cd server
pytest
```

117 tests across storage, auth, HTTP routes, agent WebSocket, viewer WebSocket, and an end-to-end vertical slice.

```bash
# Unit tests only (no external deps)
pytest tests/unit/

# Single file
pytest tests/unit/test_ws_agent.py
```

## Logging

The server logs session lifecycle events at `INFO` and unexpected errors at `ERROR` via standard Python `logging`. To see them during development:

```bash
uvicorn "server.app.api:create_app" --factory --reload --log-level info
```

Key log messages:

```text
INFO  server.app.ws_agent  agent connecting agent_id=... node_id=...
INFO  server.app.ws_agent  agent session started session_id=ses_... agent_id=...
INFO  server.app.ws_agent  agent session ended session_id=ses_... agent_id=...
INFO  server.app.ws_viewer viewer subscribed subscription_id=sub_... user_id=... agent_id=...
INFO  server.app.ws_viewer viewer unsubscribed subscription_id=sub_...
```
