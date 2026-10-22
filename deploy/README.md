# Building server installation

This deployment installs **SBMS + The Things Stack Open Source + PostgreSQL +
Redis** on one Linux server. ThingsBoard is optional and disabled by default.
SBMS stores latest readings, telemetry history, and alarms in SQLite, and keeps
email alerts active with ThingsBoard disabled. Set `ALERT_EMAIL_ENABLED=true`
and configure SMTP to send alerts.

Use Docker Engine with Docker Compose v2.24 or newer, Python 3.10+ for the
configuration generator, and a static LAN IPv4 address or DNS name reachable by
browsers and gateways. Keep a checkout of this repository on the server: Compose
builds SBMS from its Dockerfile and pulls The Things Stack, PostgreSQL, and Redis
as upstream images. No published SBMS image or registry login is required.
The default configuration uses HTTP and unencrypted gateway connections on the
building's trusted LAN. Publish these ports only to that network; use a reverse
proxy and TLS configuration before exposing the service beyond it.

## 1. Prepare configuration

Run from the repository root on the server:

```bash
python3 tools/prepare_building.py \
  --host 192.168.10.20 \
  --admin-email admin@building.example
```

Replace the address and email with the building's values. This creates
`.env.building` with independent random passwords/keys and
`deploy/stack/ttn-lw-stack.yml` with the stack's LAN URLs. It leaves the existing
development `.env` untouched. Both generated files are gitignored. The generator
refuses to replace existing files so a rerun cannot rotate a live installation's
credentials. The stack configuration contains no secrets; `.env.building` is
created with owner-only permissions on Linux.

The generated SBMS administrator password is stored as `ADMIN_PASSWORD` in
`.env.building`. Save it in your password manager. The Things Stack has a
separate administrator account, created in the next step.

Build SBMS from the files in this checkout and pull the supporting services:

```bash
docker compose --env-file .env.building -f docker-compose.building.yml build sbms-app
docker compose --env-file .env.building -f docker-compose.building.yml pull stack postgres redis
```

The Dockerfile copies the app, templates, static assets, and supporting files
into the SBMS image. Source changes take effect after rebuilding. The services
communicate over the Compose network and store their data in named volumes.
On a network with a managed CA, save the certificate as `.docker-local-ca.cer`
and set `SBMS_LOCAL_CA_FILE=.docker-local-ca.cer` in `.env.building` before
building. Otherwise the committed empty certificate placeholder is used.

## 2. Initialize The Things Stack

For a **new installation only**:

```bash
bash linux/initialize-building.sh admin@building.example
```

This starts PostgreSQL and Redis, migrates the Identity Server database,
prompts for the Things Stack administrator password, creates the Console OAuth
client with the matching generated secret, and starts the stack. It does not
change any existing deployment's databases. Run it once, then use ordinary
Compose commands for restarts. If initialization stops midway, resume the
failed step rather than rerunning account/client creation; the individual
commands are in `linux/initialize-building.sh`. For unattended installation,
`TTS_BOOTSTRAP_PASSWORD` can be supplied through the script's environment.

Open `http://192.168.10.20:1885/console` and sign in as `admin`.

Create an application with ID matching `TTN_APP_ID` in `.env.building`
(`smart-building-lora-2` by default). Create an application API key with the
rights needed to read/write end devices, read/write their keys, read traffic,
and configure integrations. Store it as `TTN_API_KEY` in `.env.building`.

In that application's **Integrations → Webhooks**, add a custom JSON webhook:

| Setting | Value |
| --- | --- |
| Base URL | `http://sbms-app:8000` |
| Uplink message path | `/ttn-webhook` |
| Header name | `X-Webhook-Secret` |
| Header value | The generated `TTN_WEBHOOK_SECRET` from `.env.building` |

`stack` and `sbms-app` resolve through the Compose network. Do not use
`localhost` or `host.docker.internal` for the webhook. The SBMS-to-stack API URL
is `http://stack:1885`. `TTN_CLUSTER_ADDRESS=localhost` reflects the monolithic
stack's internal server identity; it is distinct from its API transport URL.

Register the LoRaWAN gateway in the Things Stack Console. Match its gateway
EUI and frequency plan to the physical gateway and sensor firmware. This
Compose file exposes these gateway transports:

| Transport | Gateway destination |
| --- | --- |
| Semtech UDP packet forwarder | `192.168.10.20`, UDP port `1700` |
| LoRa Basics Station | `ws://192.168.10.20:1887` with a gateway API key |

For Basics Station, create a key with gateway link rights and configure it in
the gateway. Unauthenticated Basics Station connections stay disabled.
Gateway MQTT and TLS ports are not published by this configuration. Expose
additional transports only after configuring their listeners and credentials.

The default LoRaWAN AppKey is newly generated for this building. For existing
sensors, copy their actual shared `LORAWAN_APP_KEY` and `JOIN_EUI` into
`.env.building` before registration, or flash firmware with the new values.
An existing TTN application or device registry is not migrated automatically.

## 3. Start SBMS and verify

```bash
docker compose --env-file .env.building -f docker-compose.building.yml up -d --build --wait
curl --fail http://192.168.10.20:8000/healthz
```

Open `http://192.168.10.20:8000/admin` and sign in using `ADMIN_EMAIL` and
`ADMIN_PASSWORD`. Verify a real gateway is online, trigger a sensor uplink,
check that it enters the discovery inbox, place it on a floor, and verify
readings, history, and an alarm. A green container health check proves the
service/database are reachable; it does not prove a gateway, webhook, or email
configuration works.

The explicit `-f docker-compose.building.yml` prevents Compose from loading
`docker-compose.override.yml`. Keep using it for all production commands.
Port `8000` is configurable through `SBMS_HTTP_PORT`; `SBMS_BIND_ADDRESS` can
restrict listeners to the server's LAN interface. PostgreSQL and Redis expose
no host ports. HTTP mode uses `COOKIE_SECURE=false`; set it to `true` when
serving SBMS over HTTPS.

## Persistent data and existing installations

Named volumes store SBMS's database and uploads, the stack's PostgreSQL and
Redis state, and stack blobs. New SBMS volumes inherit UID 1000 ownership from
the image, avoiding Linux upload bind-mount permissions. The Compose project
name defaults to `pulse-building`; keep it stable across upgrades. Changing
the project name selects different volumes. `docker compose down` preserves
volumes; `down -v` removes the building's data.

A fresh volume starts a fresh SBMS database. To transfer an existing SBMS
installation, stop its writers, copy a consistent SQLite backup and its uploads
into the new `sbms-data` and `sbms-uploads` volumes, and set their ownership to
UID/GID `1000:1000` before starting SBMS. Preserve the corresponding TTN
application/device registrations and sensor credentials. Do not copy only the
SQLite main file while WAL-mode writes are active.

## Backups and upgrades

Back up **all five volumes**, `.env.building`, and
`deploy/stack/ttn-lw-stack.yml`. Redis contains live LoRaWAN session state and
must be backed up alongside PostgreSQL. The following is an offline Linux
backup for the default `pulse-building` project name; it briefly stops service:

```bash
umask 077
backup_dir="$PWD/backups/building-$(date -u +%Y%m%d-%H%M%S)"
mkdir -p "$backup_dir"
docker compose --env-file .env.building -f docker-compose.building.yml stop
docker compose --env-file .env.building -f docker-compose.building.yml run \
  --rm --no-deps --user 0 --entrypoint tar \
  -v pulse-building_tts-postgres:/backup/tts-postgres:ro \
  -v pulse-building_tts-redis:/backup/tts-redis:ro \
  -v pulse-building_tts-blob:/backup/tts-blob:ro \
  -v "$backup_dir:/backup-output" \
  sbms-app -czf /backup-output/volumes.tgz \
  -C /app data uploads -C /backup tts-postgres tts-redis tts-blob
cp .env.building "$backup_dir/"
cp deploy/stack/ttn-lw-stack.yml "$backup_dir/"
docker compose --env-file .env.building -f docker-compose.building.yml up -d --wait
```

Check every command succeeds, restart services if a backup command fails,
and copy the backup to a separate machine. For restoration, stop the destination
services, restore into empty corresponding volumes, preserve numeric ownership,
restore the two configuration files, and use the same database/image versions
before starting services. Test restoration on an isolated server before relying
on the backup. Never restore over a running database.

Before an upgrade, take a backup and record the currently deployed Git commit
and supporting image references. Check out the tested application revision,
preserve `.env.building` and the generated stack configuration, and run
`docker compose --env-file .env.building -f docker-compose.building.yml up -d --build --wait`.
SBMS runs its database migrations on startup. If changing
the Things Stack version, follow its migration instructions and run
`docker compose --env-file .env.building -f docker-compose.building.yml run --rm stack is-db migrate`
while the stack is stopped. Do not change the PostgreSQL major version by
changing its image tag alone. PostgreSQL/Redis tags are pinned to a release
series; pin immutable digests as part of the server's release process.

## Optional ThingsBoard

To connect a separately installed ThingsBoard server, set
`THINGSBOARD_ENABLED=true`, `THINGSBOARD_URL` to an address reachable from the
SBMS container (for example `http://192.168.10.30:8080`), and its `TB_USERNAME`
and `TB_PASSWORD` in `.env.building`. Recreate SBMS with `up -d --wait`.
The integration remains in the application; no ThingsBoard container is
installed. Core telemetry history, dashboard readings, floor views, alarms,
and emails work with `THINGSBOARD_ENABLED=false`.

The Things Stack configuration and initialization follow the official
[Docker installation guide](https://www.thethingsindustries.com/docs/enterprise/docker/)
and [initialization procedure](https://www.thethingsindustries.com/docs/enterprise/docker/running-the-stack/).
