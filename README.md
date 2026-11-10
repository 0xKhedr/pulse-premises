# Smart Building Management System

FastAPI-based smart-building platform for LoRaWAN provisioning, telemetry,
floor maps, alarms, gateways, client access and firmware tooling.

Start with [setup.md](setup.md) for the quick setup steps and default logins.

## Requirements

- Python **3.10+**
- Docker Engine / Docker Desktop with Docker Compose **2.24+**

## Install and run

The default setup runs SBMS, The Things Stack, PostgreSQL, and Redis together.
Set it up from PowerShell or a Linux terminal:

```text
python tools/setup.py
```

The host defaults to `localhost` and the SBMS administrator email defaults to
`admin@{host}`. Use `--host <LAN-IP-or-DNS-name>` for remote browsers or gateways,
and `--admin-email` to override the email. Each run overwrites `.env` and the
stack configuration using these arguments and defaults, regenerating random keys.

This generates configuration, builds the images, and starts all
services. The TTN entrypoint initializes its database, administrator, and
Console OAuth client before starting the server. Completed database initialization
steps remain recorded in the blob volume. See [the setup guide](deploy/NEW_MACHINE_SETUP.md).

Open `http://localhost:8000/admin` for SBMS or
`http://localhost:1885/console` for TTN. Compose automatically loads `.env`
and `docker-compose.yml`. See [the deployment guide](deploy/README.md) for
TTN initialization, credentials, gateway connections, backups, and upgrades.

### Run the Python app directly

For source development with Python and pip, the existing launchers run SBMS
on the host. The TTN stack must already be running.

On Windows:

```powershell
.\windows\install.ps1
.\windows\run.bat
```

On Linux:

```bash
bash linux/install.sh
bash linux/run.sh
```

Direct entry point:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Configuration

### Dashboard preview

Run `python tools/preview_dashboard.py` (or `windows\preview.bat`) and open
`http://localhost:8001/admin`. Sign in with `admin@preview.local` and
`Preview123!`. The two client accounts are `client1@preview.local` and
`client2@preview.local`, with the same preview password.

The launcher creates `build/dashboard-preview.db`, separate from the configured
database. It includes two clients, sites, buildings, floors, rooms, devices,
gateways, sensor profiles, sensors, firmware modules, client access records,
maps, alarm recipients, alarms and audit events, plus sample telemetry history.
One device and gateway are online and the other pair is offline. Preview mode
simulates gateway health and refreshes local readings; TTN and ThingsBoard
connections and alert emails are disabled. Authentication and client scope
checks still apply. Re-running preserves changes made in the preview workspace.
Use `--seed-only` to prepare the sample database without starting a server,
or `--port 8002` to choose another local port.

Generate `.env` with `python tools/prepare_building.py`, then edit it for your
environment. `.env.example` documents settings and contains sample credentials.

The active names include `DB_FILE`, TTN settings, `TB_USERNAME`, `TB_PASSWORD`,
`ALERT_EMAIL_FROM`, `ALERT_EMAIL_TO`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`,
`COOKIE_SECURE`, and `LOCAL_TEST_MODE`.

Connection and access settings:

- **TTN is bundled.** Host-side tools use `TTN_BASE_URL`, generated from the
  selected host (default `http://localhost:1885`). Docker Compose routes SBMS
  to `http://stack:1885` on its internal network.
- **`TTN_WEBHOOK_SECRET` is required.** `/ttn-webhook` returns 503 to every
  request while it is empty. Set the same value in the TTN webhook's
  `X-Webhook-Secret` header.
- **`CORS_ORIGINS`** is a comma-separated list of front-end origins. Empty
  means same-origin only; there is no wildcard.

Set `COOKIE_SECURE=true` for any deployment served over HTTPS.

## Portals

- Admin: `/admin`
- Client: `/client-portal`
- API docs: `/docs`
- Readiness check: `/healthz`

Provisioning and provisioning-option endpoints require an authenticated admin
session because provisioning responses contain LoRaWAN root credentials. TTN
uplinks use the separate `/ttn-webhook` endpoint and authenticate with
`X-Webhook-Secret`.

Device placement is floor-based: every device must be assigned to a valid
floor (and may optionally be assigned to a room). Site-only and building-only
device placement is rejected.

To change the sensor profile of an existing device, open **Devices**
(`/device-capabilities-manager`), choose its **Sensor profile**, and click
**Change profile**. Select a profile matching the device's firmware; for the
ESP32-CAM meter-reader node use `ESPCAM_METER_V1`. This updates the device's
profile versions, capabilities, and TTN decoder while preserving its identity,
credentials, placement, and flashed firmware. Only enabled, active profiles
are offered. If TTN synchronization fails, the assignment remains saved and
**Retry decoder sync** completes it once the network server is available.

## LoRaWAN discovery and commissioning

Every board uses the configured `JOIN_EUI` and `LORAWAN_APP_KEY`. Its unique
DevEUI is derived deterministically from the TTN application ID and ESP32 MAC
using the same transformation in
`firmware/lilygo_cayenne_lpp_node/lilygo_cayenne_lpp_node.ino`. The
application namespace prevents the same board from colliding with an older TTN
application registration.

Generate the shared firmware credential header after configuring `.env`:

```powershell
python tools/generate_lorawan_credentials.py
```

For portal-controlled admission, `AUTO_ENROLLMENT_ENABLED` defaults to `true`. Configure
`AUTO_ENROLLMENT_GATEWAY_API_KEY` with gateway traffic access. In Device
Management or Sensor Capabilities, select **Scan for new devices**, power on
the node, and save its nickname. A valid join request adds one **Pending
approval** entry; it does not register the node in TTN. Review the DevEUI,
receiving gateway, signal and last-seen time, then select **Accept** or
**Reject**. Pending entries and decisions survive scan closure and restarts.
The per-window quota limits new discoveries; repeated requests update the
existing entry.

Accept registers the node in TTN. The portal shows **Registering**, then
**Awaiting join**, and **Online** once its first uplink arrives (including the
nickname uplink on port 10). LMIC keeps retrying OTAA while approval is pending.
Registration failures remain visible with **Retry registration**; radio traffic
does not trigger registration retries. Reject is persistent and does not
recreate a pending entry on another scan. This admission decision applies to
new candidates; it does not revoke devices previously registered outside this
flow. Building placement remains a separate step after the first uplink.

`AUTO_ENROLLMENT_MAX_PER_WINDOW` caps new candidates per scan. Scanning closes
on a restart, but saved pending devices can still be reviewed without another
scan. A changed root key requires a fresh verified join request before a saved
candidate can be accepted. Restart the backend after updating so migration
`012_device_enrollment_approvals` creates the approval queue automatically.

### Send a Class A downlink

Device Management and Device Capabilities include **Send downlink**. Select
a joined device, FPort (1–223), payload format (UTF-8 text, hex, or Base64),
and payload, then **Queue downlink**. The limit is 51 decoded bytes. Messages
append to the TTN queue; existing queued messages are preserved. Unconfirmed
is the default; optional confirmed messages request an ACK in a later uplink.

The backend uses `POST /devices/{device_id}/downlinks` and the TTN Application
Server's `/api/v3/as/applications/{application_id}/devices/{device_id}/down/push`
API. `TTN_API_KEY` needs application downlink-write permission
(`RIGHT_APPLICATION_TRAFFIC_DOWN_WRITE`). Only administrators can send, and
preview mode cannot queue downlinks. Requests validate FPort, encoding, and
decoded byte length before contacting TTN. The audit log records the sender,
target, port, length, and correlation ID, without recording the message bytes.

**Queued is not received.** Class A delivery waits for RX1/RX2 after an uplink.
A timeout leaves the queue outcome unknown, so the portal asks the operator
to check TTN before resending; it never automatically repeats a queue push.
The environment node's OLED displays the last received application downlink
in paged TEXT/HEX views after joining. See
[`firmware/lilygo_environment_lpp_node`](firmware/lilygo_environment_lpp_node/README.md)
for a simple `Hello` test.

### Configure a device's send rate

Open **Devices**, then **Configuration** on the device's card. Enter a send
interval from **30 to 86,400 seconds** and choose **Queue send-rate change**.
This requires the updated `lilygo_environment_lpp_node` firmware to be built
and flashed once. The device saves the interval in flash and restores it on
restart. Other sketches need support for the same command before they can
apply this setting.

The administrator-only endpoint is
`POST /devices/{device_id}/configuration/send-rate`, with JSON
`{"send_interval_seconds": 300}` for five minutes. It uses the existing TTN
queue, on FPort **42**, with a versioned six-byte binary command. Preview mode
and unapproved devices cannot queue changes. The page remembers the last
queued request and distinguishes it from the unknown active device value.
Delivery waits for an uplink at the old rate; no application acknowledgement
is reported to the website. See the environment firmware README for the wire
format and Serial verification instructions.

For the u-blox NEO-6M GPS node, see
[`firmware/lilygo_gps_lpp_node`](firmware/lilygo_gps_lpp_node/README.md)
for breadboard wiring, Arduino IDE setup and GPS telemetry details. It uses
the same naming and OTAA commissioning procedure as the temperature node.

For the AJ-SR04M ultrasonic distance node, see
[`firmware/lilygo_sr04_lpp_node`](firmware/lilygo_sr04_lpp_node/README.md)
for LilyGO wiring, the ECHO voltage divider and Cayenne LPP distance telemetry.

Register a batch in TTN before field deployment without creating backend rows:

```powershell
python tools/register_ttn_devices.py --csv device_inventory.csv
```

The CSV requires `chip_mac` and may include `device_id`. Use `--dry-run` to
inspect the derived identities. The first accepted TTN webhook then creates the
backend device automatically with `is_placed = 0`. Telemetry and radio metadata
are stored, but alarms remain suppressed until an administrator drags the node
onto a floor and saves its position. Room assignment is optional.

Run the field acceptance check with one command:

```powershell
python tools/hardware_acceptance.py --device-id node-aabbccddeeff --timeout 300
```

Add `--require-commissioned` when the test must also verify floor placement.

For a controlled migration of an older database, first run reconciliation and
TTN synchronization in dry-run mode, then apply each operation:

```powershell
python tools/reconcile_legacy_database.py --source backups/retired-databases-20260912/devices.db --target smarthome.db
python tools/reconcile_legacy_database.py --source backups/retired-databases-20260912/devices.db --target smarthome.db --apply
python tools/sync_ttn_credentials.py
python tools/sync_ttn_credentials.py --apply
```

Both apply commands create timestamped database backups. TTN synchronization
never prints the shared AppKey and restores the previous TTN identity if a
replacement registration fails.

Device connection states are `never_connected`, `online`, `stale`, and
`offline`. `DEVICE_STALE_AFTER_SECONDS` and `DEVICE_OFFLINE_AFTER_SECONDS`
control the boundaries.

## Database

SQLite is the default. Startup bootstraps the current compatibility schema and
then runs tracked non-destructive migrations in `app/db/migrations/`.

The project is migrating incrementally to the canonical hierarchy:

`Client -> Site -> Building -> Floor -> Room -> Device`

Legacy location columns/tables are intentionally retained until all routes use
relational joins and migration tests pass against a copy of real data.

## Deployment (Docker)

`docker-compose.yml` installs SBMS, The Things Stack Open Source, PostgreSQL,
and Redis with five persistent named volumes. SBMS uses `Dockerfile`; the
stack uses `deploy/Dockerfile.stack` to add automatic initialization to the
upstream image. PostgreSQL and Redis use upstream images. ThingsBoard is optional and
is disabled by default. Local readings, telemetry history, alarms, and email
alerts work without it.

For a fresh installation, run `python tools/setup.py`. To prepare configuration
without starting Docker, use `python tools/setup.py --prepare-only`.
Start and update configured services using:

```bash
docker compose up -d --build --wait
```

The generated `.env` uses PostgreSQL password `root`, SBMS admin password
`admin`, Console secret `console`, and enables auto-enrollment. Cookie, cluster,
webhook, and device keys are randomly generated.
Preserve it and
`deploy/stack/ttn-lw-stack.yml` across upgrades. Keep `COMPOSE_PROJECT_NAME`
stable (default `smart-building`) to reuse the existing volumes. SBMS stores
its SQLite database in `sbms-data` and uploaded files in `sbms-uploads`. Both
volumes inherit ownership from the image's unprivileged user (UID 1000).

Configure TTN's webhook with base URL `http://sbms-app:8000`, uplink path
`/ttn-webhook`, and an `X-Webhook-Secret` header matching `TTN_WEBHOOK_SECRET`
in `.env`. SBMS reaches TTN at `http://stack:1885` inside Compose. Set the
application API key created in the bundled stack as `TTN_API_KEY` in `.env`.

### Editing templates and static files

Templates and static files are baked into the image. Rebuild after changes
with `docker compose up -d --build`. To mount these directories read-only
for development, explicitly add the optional development file:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

Template and static edits then appear on the next request. Python source
changes still need a rebuild. Ordinary Compose commands use the self-contained
image without development mounts.

### Networks that intercept TLS

Export the network's root certificate in DER format to `.docker-local-ca.cer`
and add this to `.env`:

```text
SBMS_LOCAL_CA_FILE=.docker-local-ca.cer
```

The build installs the certificate into the image trust store. On an ordinary
network, the committed empty `.docker-local-ca.cer.example` placeholder is
used automatically.

## Verification

`pytest` and `ruff` are dev-only tools and are not in `requirements.txt` (the
production image only installs runtime dependencies). Install them first:

```bash
pip install -r requirements-dev.txt
```

```bash
python -m compileall -q app tests tools
pytest -q
ruff check app tests tools/register_ttn_devices.py tools/hardware_acceptance.py tools/generate_lorawan_credentials.py tools/configure_local_ttn.py tools/reconcile_legacy_database.py tools/sync_ttn_credentials.py
docker compose config --quiet
```

These checks and a Docker image build also run in `.github/workflows/ci.yml`.

On a managed network that intercepts TLS, export the organization root
certificate to `.docker-local-ca.cer` and build without weakening verification:

```powershell
docker build --secret id=sbms_local_ca,src=.docker-local-ca.cer -t smart-building-management-system:test .
```
