# data-proc-sessions


## Changelog

### 2026-03-30 — Domain migration prep
- Replaced hardcoded `thync.online` domain references with environment variables
- `ALLOWED_ORIGINS` env var (Cloud Run) now controls CORS allowed origins — set as comma-separated list, e.g. `https://service.newdomain.com,https://service.newdomain.com`
- Default falls back to `http://localhost:5173` for local development
- `FRONTEND_URL` env var: set to the Cloudflare Pages URL for this service
- See `domain-migration-register.md` at the root of /Users/charles/Projects for the complete list of Cloud Run env vars to set per service
