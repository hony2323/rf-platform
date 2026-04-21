# RF Platform Web

## Environment

The frontend can use the Vite proxy in local development and an explicit backend origin in production.

Local development:

```bash
cp .env.example .env.local
```

Leave `VITE_API_BASE_URL` unset to keep requests same-origin, so `/auth`, `/me`, `/agents`, and `/ws/...` continue to flow through the Vite proxy.

Production example:

`VITE_API_BASE_URL=https://api.example.com`

If `VITE_API_BASE_URL` is absolute, HTTP and WebSocket URLs are built from it, so `https://api.example.com` becomes `wss://api.example.com/...`.
