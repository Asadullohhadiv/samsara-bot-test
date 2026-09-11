# samsara-bot-test# My Truck Safety — production hardening pass

Telegram Mini App + bot for fleet fuel planning, PTI, driver points, and Samsara fleet data.

## Stack

- Python / FastAPI
- python-telegram-bot
- Supabase PostgreSQL
- Render
- Samsara API
- OpenRouteService HGV routing
- Apify fuel-price discovery

## What changed in this build

### Route-aware fuel recommendations
The fuel engine now:

1. Gets an HGV route from the truck's current Samsara location to the destination.
2. Samples multiple points along long routes to discover more stations.
3. Merges external fuel-price results with the local truck-stop database.
4. Deduplicates stations.
5. Projects each station onto the actual route.
6. Calculates miles ahead, distance to route, and estimated detour.
7. Considers fuel level, tank capacity, average MPG, and a configurable reserve.
8. Ranks stations using a transparent score.

The top recommendations are therefore not simply the cheapest or closest stations.

### Security hardening
- Removed credentials from `config.py`; use environment variables.
- Correct Telegram Mini App `initData` verification.
- Driver credentials are stored as PBKDF2-SHA256 hashes. Existing plaintext credentials are upgraded after a successful login.
- Admin credentials are no longer embedded in `index.html`. Admin login is server-side and returns a short-lived signed token.
- Admin API endpoints require the signed admin token.
- Fuel, PTI, and points operations require a verified Telegram Mini App session.

### Health check
`GET /health` returns a simple Render health response.

## Required Render environment variables

See `.env.example`. At minimum set:

- `TELEGRAM_TOKEN`
- `DATABASE_URL`
- `SAMSARA_API_TOKEN`
- `OPENROUTESERVICE_API_KEY`
- `APIFY_API_TOKEN`
- `ADMIN_USERNAME`
- `ADMIN_PASSWORD`
- `ADMIN_API_SECRET`
- group IDs and `WEB_APP_URL`

## Important before deployment

The original project archive contained API credentials. Rotate those credentials before deploying this repository. Do not reuse the exposed tokens.

## Remaining production work

1. Move Mini App PTI sessions from in-memory `PTI_SESSIONS` to Supabase so sessions survive Render restarts and multiple instances.
2. Add versioned SQL migrations instead of relying only on startup `CREATE TABLE`.
3. For the top 3 fuel candidates, optionally calculate exact HGV road detour using a routing/matrix request rather than the current perpendicular-distance detour estimate.
4. Add Samsara sync deduplication/idempotency so repeated polling cannot create duplicate safety/fault/maintenance records.
5. Add rate limiting and audit logging to sensitive admin endpoints.
