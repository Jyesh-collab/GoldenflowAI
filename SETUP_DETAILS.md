# Local Bagisto Backend — Docker Setup Details

Real Bagisto v2.4.8 store, running locally via Docker, used as the backend for the
`opensource-ecommerce-mobile-app` Flutter project during Android APK builds/testing.

## Location

- Compose file: `bagisto-backend/docker-compose.yml` (relative to this repo's root)
- App source (mounted into the app container): `bagisto-backend/bagisto/`
- MySQL data: Docker named volume `bagisto-backend_mysql-data`
- The compose file pins `name: bagisto-backend` at the top level so the project name
  (and therefore the volume name) stays stable no matter where this folder lives on
  disk — this let the whole setup be moved into the repo without losing seeded data.
- `bagisto-backend/bagisto/` and its `.env` are git-ignored (see repo `.gitignore`) —
  it's ~680MB of vendor code/dependencies and disposable, not meant to be committed.

## Containers

| Container       | Image                     | Purpose                          | Host Port | Container Port |
|-----------------|---------------------------|-----------------------------------|-----------|-----------------|
| `bagisto-mysql` | `mysql:8.0`                | Database                          | 3307      | 3306            |
| `bagisto-app`   | `webdevops/php-apache:8.3` | PHP 8.3 + Apache, serves Bagisto   | 8080      | 80              |

`webdevops/php-apache:8.3` was chosen over the bare `composer:2` image because it ships
PHP 8.3 with the extensions Bagisto requires out of the box (`calendar`, `intl`, `pdo_mysql`).

## Database credentials

- Host (from `bagisto-app` container): `mysql`
- Host (from your Mac): `localhost:3307`
- Database: `bagisto`
- Username: `bagisto`
- Password: `bagisto`
- Root password: `root`

## Application

- Installed with: `php artisan bagisto:install --no-interaction --demo-samples`
- Demo products seeded: **143**
- Admin URL: `http://localhost:8080/admin` (from Mac) / `http://10.0.2.2:8080/admin` (from Android emulator)
- Admin login: `admin@example.com` / `admin123`

## Storefront GraphQL API

Installed via the `bagisto/bagisto-api` Composer package + `php artisan bagisto-api-platform:install`.

- GraphQL endpoint: `http://localhost:8080/api/graphql` (Mac) / `http://10.0.2.2:8080/api/graphql` (Android emulator)
- GraphQL Playground: `http://localhost:8080/api/graphiql`
- REST Storefront Swagger: `http://localhost:8080/api/shop`
- REST Admin Swagger: `http://localhost:8080/api/admin`
- Auth header: `X-STOREFRONT-KEY: pk_storefront_RwgpPpEpBNrKywjBExAY0QL26pUfuLtq`

Relevant `.env` entries (`bagisto-backend/bagisto/.env`):

```
STOREFRONT_DEFAULT_RATE_LIMIT=100
STOREFRONT_CACHE_TTL=60
STOREFRONT_KEY_PREFIX=storefront_key_
STOREFRONT_PLAYGROUND_KEY=pk_storefront_RwgpPpEpBNrKywjBExAY0QL26pUfuLtq
API_PLAYGROUND_AUTO_INJECT_STOREFRONT_KEY=false
APP_URL=http://10.0.2.2:8080
```

## Flutter app wiring

`lib/core/constants/api_constants.dart` in the mobile app repo points at this backend:

```dart
const String bagistoEndpoint = 'http://10.0.2.2:8080/api/graphql';
const String storefrontKey = 'pk_storefront_RwgpPpEpBNrKywjBExAY0QL26pUfuLtq';
```

`10.0.2.2` is the Android emulator's alias for the host machine's `localhost` — it will
**not** work from a physical device or a different network. See below for exposing it
via ngrok instead.

## Exposing the backend remotely (ngrok)

To reach this local backend from a physical device, a different network, or anywhere
else — not just this Mac's Android emulator — tunnel port 8080 with ngrok.

**One-time setup:**

1. Create a free account at <https://dashboard.ngrok.com/signup> and grab your authtoken
   from <https://dashboard.ngrok.com/get-started/your-authtoken>
2. Set it (either works):

   ```bash
   ngrok config add-authtoken YOUR_TOKEN
   ```

   or edit the placeholder directly in `~/Library/Application Support/ngrok/ngrok.yml`
   (currently contains `authtoken: YOUR_NGROK_AUTHTOKEN_HERE`).

**Each time you want a public URL:**

```bash
cd bagisto-backend
./start-ngrok.sh
```

This refuses to start with a clear error while the authtoken is still the placeholder.
Once running, it prints a forwarding URL like `https://abcd1234.ngrok-free.app`. Copy it
into `lib/core/constants/api_constants.dart`:

```dart
const String bagistoEndpoint = 'https://abcd1234.ngrok-free.app/api/graphql';
```

Then rebuild the APK. Caveats:

- **Free ngrok URLs are random and change every time you restart the tunnel** — you'll
  need to update `api_constants.dart` and rebuild after each restart. A paid ngrok plan
  can reserve a static domain to avoid this.
- The tunnel only stays up while `start-ngrok.sh` / `ngrok` is running in a terminal,
  the Docker backend is up, and this Mac is on. It is not a persistent public backend.
- Free ngrok tunnels also have a request-rate limit — fine for demoing, not for load.

## Useful commands

Start / stop the stack:

```bash
cd bagisto-backend   # relative to this repo's root
docker compose up -d      # start mysql + app
docker compose stop       # stop containers (keeps data)
docker compose down       # stop and remove containers (keeps volumes/data)
docker compose down -v    # stop and DELETE all data (irreversible)
```

Run artisan/composer commands inside the app container:

```bash
docker exec -w /app bagisto-app php artisan <command>
docker exec -w /app bagisto-app composer <command>
```

Inspect the database directly:

```bash
docker exec bagisto-mysql mysql -ubagisto -pbagisto bagisto -e "SHOW TABLES;"
```

## Caveats

- This is a local dev setup, not production-hardened: default/weak DB credentials,
  `PHP_DISPLAY_ERRORS=1`, no HTTPS, no queue worker running (emails/notifications that
  rely on queued jobs won't fire unless one is started).
- Data persists across container restarts (named volume) but is lost if you run
  `docker compose down -v`.
- Stack must be running (`docker compose up -d` in `bagisto-backend/`) and Docker
  Desktop must be open for the Flutter app to reach the backend.
- `bagisto-backend/bagisto/` (the actual Bagisto app + vendor code) is git-ignored.
  If you clone this repo elsewhere, that folder won't exist — re-run the setup
  (`composer create-project bagisto/bagisto`, `bagisto:install --demo-samples`,
  `composer require bagisto/bagisto-api`, `bagisto-api-platform:install`) to recreate it.
