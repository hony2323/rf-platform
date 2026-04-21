# RF Platform Web

## Environment

The frontend requires `VITE_API_BASE_URL` so it can talk to the separately deployed backend without relying on the Vite dev proxy.

Local development:

```bash
cp .env.example .env.local
```

`VITE_API_BASE_URL=http://localhost:8000`

Production example:

`VITE_API_BASE_URL=https://api.example.com`

WebSocket URLs are derived automatically from the same value, so `https://api.example.com` becomes `wss://api.example.com/...`.
