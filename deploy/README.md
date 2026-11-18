# Building server installation

This deployment installs **SBMS + The Things Stack Open Source + PostgreSQL +
Redis** on one machine with Docker. ThingsBoard is optional and disabled by default.
SBMS stores latest readings, telemetry history, and alarms in SQLite, and keeps
email alerts active with ThingsBoard disabled. Set `ALERT_EMAIL_ENABLED=true`
and configure SMTP to send alerts.

Use Docker Engine with Docker Compose v2.24 or newer, Python 3.10+ for the
configuration generator, and a static LAN IPv4 address or DNS name reachable by
browsers and gateways. Keep a checkout of this repository on the server: Compose
builds SBMS from its Dockerfile, extends the upstream Things Stack image with
an initialization entrypoint, and pulls PostgreSQL and Redis as upstream images. No published SBMS image or registry login is required.
The default configuration uses HTTP and unencrypted gateway connections on the
building's trusted LAN. Publish these ports only to that network; use a reverse
proxy and TLS configuration before exposing the service beyond it.

## 1. Prepare configuration

The simplest setup on Windows or Linux is `python tools/setup.py`. It prepares
configuration and starts all services; TTN initializes inside its entrypoint.
See [the setup guide](NEW_MACHINE_SETUP.md) for the complete flow.

For separate configuration preparation, run from the repository root:

For local use, run `python3 tools/prepare_building.py` with no arguments. The
host defaults to `localhost` and the administrator email defaults to
`admin@{host}` (`admin@localhost` by default). Override either with `--host` or
`--admin-email`. For access from other computers or physical gateways, use the
server's LAN address:

```bash
python3 tools/prepare_building.py \
  --host 192.168.10.20 \
  --admin-email admin@building.example
```

Replace the address and email with the building's values. This creates
`.env` with PostgreSQL password `root`, SBMS admin password `admin`,
auto-enrollment enabled, and independently generated cryptographic keys, plus
`deploy/stack/ttn-lw-stack.yml` with the stack's URLs. Both generated files are
gitignored. Every generator/setup run overwrites both files and regenerates
cookie, cluster, webhook, and device keys. PostgreSQL password `root`, Console
secret `console`, SBMS password `admin`, and auto-enrollment `true` are fixed
generation defaults. Use ordinary Compose commands for restarts or upgrades
that should keep the current configuration. The stack configuration contains no secrets; `.env` is
created with owner-only permissions on Linux.

The SBMS administrator password defaults to `admin`, stored as `ADMIN_PASSWORD`
in `.env`. The Things Stack has a
separate administrator account, created in the next step.

Build SBMS and the TTN wrapper from this checkout and pull the supporting services:

```bash
docker compose build
docker compose pull postgres redis
```

The Dockerfile copies the app, templates, static assets, and supporting files
into the SBMS image. Source changes take effect after rebuilding. The services
communicate over the Compose network and store their data in named volumes.
On a network with a managed CA, save the certificate as `.docker-local-ca.cer`
and set `SBMS_LOCAL_CA_FILE=.docker-local-ca.cer` in `.env` before
building. Otherwise the committed empty certificate placeholder is used.

## 2. Start The Things Stack

Initialization now runs automatically when the stack container starts. No
host Bash script or interactive password prompt is required:

```bash
docker compose up -d --build --wait
```

The entrypoint migrates the Identity Server database, creates the `admin`
account and Console OAuth client, then executes the server. TTN's bootstrap
email defaults to `admin@127.0.0.1` and its password defaults to `admin`; set
`TTS_BOOTSTRAP_EMAIL` and `TTS_BOOTSTRAP_PASSWORD` in `.env` before the first
start to override them. SBMS has its own `ADMIN_EMAIL` / `ADMIN_PASSWORD`.

Creation checkpoints live in the blob volume. Subsequent starts preserve
accounts and the Console client; migrations still run before startup. If a
step fails, resolve the error in `docker compose logs stack` and start again.
The legacy `linux/initialize-building.sh [email]` is a compatibility shortcut
for starting TTN and its dependencies.

Open `http://192.168.10.20:1885/console` and sign in as `admin`.

Set one user/organization backend key as `TTN_GATEWAY_MANAGEMENT_API_KEY`.
For the default user owner, grant `RIGHT_USER_APPLICATIONS_CREATE` and
`RIGHT_APPLICATION_ALL`, as well as the gateway rights documented below.
Creating an admin account does not grant every right to each API key.
An organization owner needs the corresponding organization creation rights.

On SBMS startup, automatic setup creates `TTN_APP_ID` (`smart-building-lora-2`
by default) if absent and configures its JSON uplink webhook, including the
`X-Webhook-Secret` header. Existing applications are preserved. An existing
webhook pointing to this SBMS uplink endpoint is reused to avoid duplicates.
Setup retries every 30 seconds after failures; the Gateways page shows its
status. `TTN_API_KEY` is optional and overrides application access if supplied.

Compose supplies `TTN_WEBHOOK_BASE_URL=http://sbms-app:8000`; uplinks go to
`/ttn-webhook`. Override that URL for an external TTN deployment. With no URL
outside Compose, automatic application creation still runs, but webhook setup
is skipped. `TTN_AUTO_SETUP_ENABLED=false` disables automatic setup entirely.

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

Direct Ethernet connections are supported on Windows and Linux. For gateways
using `169.254.x.x`, configure the Ethernet return route using
[Direct Ethernet gateway connection](NEW_MACHINE_SETUP.md#direct-ethernet-gateway-connection).
Point the packet forwarder at the PC's Ethernet IPv4 address on UDP 1700.

### Gateway connection approval

The **Gateways** page includes a connection-request inbox. The bundled stack
continues to require registered gateways; discovery does not admit traffic.
Compose shares a bounded JSON failure journal with SBMS through the
`tts-gateway-journal` volume, read-only in SBMS. No Docker socket is exposed.
Rebuild both images after updating (`docker compose up -d --build --wait`);
do not rerun the configuration generator on an existing installation.

For acceptance, set `TTN_GATEWAY_MANAGEMENT_API_KEY` in `.env` to a **user or
organization API key** permitted to create gateways under
`TTN_GATEWAY_OWNER_ID` (default `admin`, owner type `users`). Include gateway
information, settings and API-key management rights, and the rights it will
grant to new gateway keys (`RIGHT_GATEWAY_LINK`). This is separate from the
application-scoped override `TTN_API_KEY`. Include `RIGHT_GATEWAY_DELETE`,
`RIGHT_GATEWAY_STATUS_READ` and `RIGHT_GATEWAY_TRAFFIC_READ` for deletion,
monitoring and device discovery. The shared backend key is used for all
managed gateways; `AUTO_ENROLLMENT_GATEWAY_API_KEY` is only a legacy fallback
when the shared key is unset. UDP gateways need no hardware API key. Basics
Station gateways still need their own restricted link credentials; never
copy the backend management key onto gateway hardware.

Accept registers the EUI and chosen frequency plan in TTN and adds the gateway
to SBMS, optionally assigning it to a site. A registration with an uncertain
result can be retried with its original ID and radio settings. Definitively
refused settings can be corrected before retrying. The approval marker
in TTN lets SBMS reconcile a create that succeeded before a connection timed
out. Existing unrelated TTN or SBMS registrations are never overwritten.
For Basics Station, use **Create connection key** after acceptance, copy the
one-time key into the gateway's LNS configuration, and reconnect. The browser
does not persist this key; manage/revoke keys in the TTN Console. A key-creation
timeout may have created a key: inspect TTN before creating another.

Deleting a gateway from Asset Management first deletes its TTN registration,
then removes its SBMS record and resets its approval entry for rediscovery.
If TTN refuses deletion or cannot confirm it, SBMS retains the gateway so the
operation can be retried. A TTN registration that is already absent is treated
as success. TTN deletion releases the EUI but can reserve the old gateway ID;
use a different gateway ID when registering again if TTN reports a conflict.

Reject records a decision and leaves the unregistered gateway blocked by TTN.
Repeated failures update its rejected record. It does not create a firewall
rule or revoke an already registered gateway. UDP EUIs are self-reported and
can be impersonated; use authenticated Basics Station for gateway identity.
Verify hardware before accepting any request. The page shows collector
availability separately from an empty inbox. Attempts only appear when TTN
logs a gateway registration/authentication failure with an identifiable EUI
or gateway ID; packet drops before that point are not visible. TTN may throttle
failure logging, so counts represent observed failures, not every packet.
The journal retains roughly 2 MiB, so prolonged SBMS downtime can lose older
attempts. The inbox retains up to 500 distinct identities by default
(`GATEWAY_APPROVAL_MAX_CANDIDATES`); it reports when this cap is reached.

External/public TTN installations do not supply this journal automatically.
Without access to server failure logs, the page reports that monitoring is
not configured. For a host-run SBMS process reading a journal from a local TTN
server, set `TTN_GATEWAY_ATTEMPT_LOG_DIR` to the journal directory.

The default LoRaWAN AppKey is newly generated for this building. For existing
sensors, copy their actual shared `LORAWAN_APP_KEY` and `JOIN_EUI` into
`.env` before registration, or flash firmware with the new values.
An existing TTN application or device registry is not migrated automatically.

## 3. Start SBMS and verify

```bash
docker compose up -d --build --wait
curl --fail http://192.168.10.20:8000/healthz
```

Open `http://192.168.10.20:8000/admin` and sign in using `ADMIN_EMAIL` and
`ADMIN_PASSWORD`. Verify a real gateway is online, trigger a sensor uplink,
check that it enters the discovery inbox, place it on a floor, and verify
readings, history, and an alarm. A green container health check proves the
service/database are reachable; it does not prove a gateway, webhook, or email
configuration works.

Compose automatically loads `docker-compose.yml` and `.env` from this checkout.
Optional development mounts require `-f docker-compose.yml -f docker-compose.dev.yml`.
Port `8000` is configurable through `SBMS_HTTP_PORT`; `SBMS_BIND_ADDRESS` can
restrict listeners to the server's LAN interface. PostgreSQL and Redis expose
no host ports. HTTP mode uses `COOKIE_SECURE=false`; set it to `true` when
serving SBMS over HTTPS.

## Persistent data and existing installations

Named volumes store SBMS's database and uploads, the stack's PostgreSQL and
Redis state, and stack blobs. New SBMS volumes inherit UID 1000 ownership from
the image, avoiding Linux upload bind-mount permissions. The Compose project
name defaults to `smart-building`; keep it stable across upgrades. Changing
the project name selects different volumes. `docker compose down` preserves
volumes; `down -v` removes the building's data.

A fresh volume starts a fresh SBMS database. To transfer an existing SBMS
installation, stop its writers, copy a consistent SQLite backup and its uploads
into the new `sbms-data` and `sbms-uploads` volumes, and set their ownership to
UID/GID `1000:1000` before starting SBMS. Preserve the corresponding TTN
application/device registrations and sensor credentials. Do not copy only the
SQLite main file while WAL-mode writes are active.

## Backups and upgrades

Back up **all five volumes**, `.env`, and
`deploy/stack/ttn-lw-stack.yml`. Redis contains live LoRaWAN session state, and the blob volume contains
initialization checkpoints. Back up these volumes alongside PostgreSQL. The following is an offline Linux
backup for the default `smart-building` project name; it briefly stops service:

```bash
umask 077
backup_dir="$PWD/backups/building-$(date -u +%Y%m%d-%H%M%S)"
mkdir -p "$backup_dir"
docker compose stop
docker compose run \
  --rm --no-deps --user 0 --entrypoint tar \
  -v smart-building_tts-postgres:/backup/tts-postgres:ro \
  -v smart-building_tts-redis:/backup/tts-redis:ro \
  -v smart-building_tts-blob:/backup/tts-blob:ro \
  -v "$backup_dir:/backup-output" \
  sbms-app -czf /backup-output/volumes.tgz \
  -C /app data uploads -C /backup tts-postgres tts-redis tts-blob
cp .env "$backup_dir/"
cp deploy/stack/ttn-lw-stack.yml "$backup_dir/"
docker compose up -d --wait
```

Check every command succeeds, restart services if a backup command fails,
and copy the backup to a separate machine. For restoration, stop the destination
services, restore into empty corresponding volumes, preserve numeric ownership,
restore the two configuration files, and use the same database/image versions
before starting services. Test restoration on an isolated server before relying
on the backup. Never restore over a running database.

Before an upgrade, take a backup and record the currently deployed Git commit
and supporting image references. Check out the tested application revision,
preserve `.env` and the generated stack configuration, and run
`docker compose up -d --build --wait`.
SBMS runs its database migrations on startup. If changing
the Things Stack version, follow its migration instructions and run
`docker compose run --rm stack is-db migrate`
while the stack is stopped. Do not change the PostgreSQL major version by
changing its image tag alone. PostgreSQL/Redis tags are pinned to a release
series; pin immutable digests as part of the server's release process.

## Optional ThingsBoard

To connect a separately installed ThingsBoard server, set
`THINGSBOARD_ENABLED=true`, `THINGSBOARD_URL` to an address reachable from the
SBMS container (for example `http://192.168.10.30:8080`), and its `TB_USERNAME`
and `TB_PASSWORD` in `.env`. Recreate SBMS with `up -d --wait`.
The integration remains in the application; no ThingsBoard container is
installed. Core telemetry history, dashboard readings, floor views, alarms,
and emails work with `THINGSBOARD_ENABLED=false`.

The Things Stack configuration and initialization follow the official
[Docker installation guide](https://www.thethingsindustries.com/docs/enterprise/docker/)
and [initialization procedure](https://www.thethingsindustries.com/docs/enterprise/docker/running-the-stack/).
