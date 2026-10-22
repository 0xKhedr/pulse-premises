# Set up SBMS and The Things Stack on a new machine

This guide creates a fresh installation with four containers in one Docker
Compose stack:

| Service | Purpose |
| --- | --- |
| SBMS | Built from this repository; stores app data in SQLite |
| The Things Stack (TTN) | LoRaWAN network server and Console |
| PostgreSQL | TTN's database |
| Redis | TTN's cache and live session state |

For a move of an existing installation, read [Moving an existing installation](#moving-an-existing-installation)
before generating new credentials.

## 1. Prepare the machine

Install:

- Python 3.10 or newer.
- Git, or copy the complete repository to the machine.
- Docker with Docker Compose v2.24 or newer.
- On Windows, Docker Desktop configured for Linux containers and Git Bash for
  the initialization script.

Start Docker and check it is available:

```text
docker version
docker compose version
```

Use the latest repository files, including the configuration-generator fixes.
Run the commands below from the repository root. SBMS is built locally; no
published SBMS image is needed. PostgreSQL, Redis, and TTN are supplied by
their Docker images, so they do not need separate host installations.

## 2. Choose the server address and ports

Assign the machine a stable LAN IPv4 address or DNS name. This guide uses
`192.168.10.20` as an example. Replace it with your actual server address in
commands and browser URLs. Replace `admin@building.example` with your admin
email address.

Allow the following ports through the host firewall for the building network:

| Port | Purpose |
| --- | --- |
| TCP 8000 | SBMS web app |
| TCP 1885 | TTN Console and API |
| UDP 1700 | Semtech gateway packet forwarder, if used |
| TCP 1887 | LoRa Basics Station, if used |

Ensure these ports are available. PostgreSQL and Redis have no published host
ports. This configuration uses HTTP on a trusted LAN; configure TLS before
exposing the services beyond that network.

## 3. Generate the configuration

On Windows, run in PowerShell:

```powershell
python tools/prepare_building.py --host 192.168.10.20 --admin-email admin@building.example
```

On Linux:

```bash
python3 tools/prepare_building.py --host 192.168.10.20 --admin-email admin@building.example
```

The generator creates both required files:

- `.env.building`: application settings and freshly generated passwords/keys.
- `deploy/stack/ttn-lw-stack.yml`: TTN's configuration with the server's public
  LAN URLs.

Run the generator instead of copying `.env.building.example`. The example
contains sample credentials. The `.yml` path must be a file, not a directory.
The generator refuses to overwrite existing deployment files, preserving their
keys and passwords.

Review `.env.building`:

| Setting | What to check |
| --- | --- |
| `TTS_HOST` | Actual server IP/DNS; must match the URLs in the generated TTN configuration |
| `SBMS_BIND_ADDRESS` | `0.0.0.0` listens on all host interfaces; a specific IP must belong to this machine |
| `SBMS_HTTP_PORT` | Default `8000`; choose a free host port |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | SBMS login; the password is generated |
| `TTN_APP_ID` | Default `smart-building-lora-2`; keep it consistent with existing firmware identities |
| `JOIN_EUI`, `LORAWAN_APP_KEY` | Must match the sensor firmware and TTN registrations |
| `TTN_CLUSTER_ADDRESS` | Keep `localhost` for the bundled stack's internal identity |
| `THINGSBOARD_ENABLED` | Leave `false` unless connecting a separate ThingsBoard server |
| `ALERT_EMAIL_*`, `SMTP_*` | Replace sample mail settings before enabling email alerts |
| `CORS_ORIGINS`, `COOKIE_SECURE` | Match the browser origins and HTTPS setup; defaults suit same-origin LAN HTTP |

`TTS_POSTGRES_PASSWORD` supplies both PostgreSQL's password and TTN's database
connection password. The generator keeps them consistent. There is no need to
set a separate password in TTN.

On a network using a managed CA, supply the certificate file and set
`SBMS_LOCAL_CA_FILE` in `.env.building` before building. Otherwise leave it
unset to use the committed empty placeholder.

Validate the Compose configuration:

```text
docker compose --env-file .env.building -f docker-compose.building.yml config --quiet
```

## 4. Initialize TTN once

On Linux:

```bash
bash linux/initialize-building.sh admin@building.example
```

On Windows, open **Git Bash** in the repository folder and run:

```bash
MSYS_NO_PATHCONV=1 bash linux/initialize-building.sh admin@building.example
```

The script starts PostgreSQL and Redis, sets TTN blob-volume ownership, applies
database migrations, creates TTN's administrator and Console OAuth client, and
starts TTN. Choose a TTN administrator password when prompted. Save it: this
account is separate from the SBMS administrator.

Run this initialization only for a new installation. If it stops midway,
resume the failed step from `linux/initialize-building.sh` rather than blindly
repeating account or OAuth-client creation.

## 5. Build and start all services

Run from PowerShell, Git Bash, or a Linux shell:

```text
docker compose --env-file .env.building -f docker-compose.building.yml up -d --build --wait
docker compose --env-file .env.building -f docker-compose.building.yml ps
```

All four services should report healthy. Open these URLs using your server's
actual address:

| Service | URL | Login |
| --- | --- | --- |
| SBMS | `http://192.168.10.20:8000/admin` | `ADMIN_EMAIL` and `ADMIN_PASSWORD` from `.env.building` |
| TTN Console | `http://192.168.10.20:1885/console` | Username `admin` and the password chosen during initialization |

Use the configured `SBMS_HTTP_PORT` if you changed it. The health endpoints are
`http://192.168.10.20:8000/healthz` and `http://192.168.10.20:1885/healthz`.

## 6. Connect TTN to SBMS

In the TTN Console:

1. Create an application whose ID matches `TTN_APP_ID`, normally
   `smart-building-lora-2`.
2. Create an application API key with the rights required to read/write end
   devices and their keys, read traffic, and configure integrations.
3. Save that API key as `TTN_API_KEY` in `.env.building`.
4. Under the application's **Integrations > Webhooks**, create a custom JSON
   webhook with these values:

| Webhook setting | Value |
| --- | --- |
| Base URL | `http://sbms-app:8000` |
| Uplink message path | `/ttn-webhook` |
| Header name | `X-Webhook-Secret` |
| Header value | The generated `TTN_WEBHOOK_SECRET` from `.env.building` |

The webhook uses Docker's internal service name, not the host IP. Keep port
`8000` in this internal URL even if you changed the published SBMS host port.
SBMS reaches TTN at `http://stack:1885`, configured by Compose.

Apply changes to the environment file:

```text
docker compose --env-file .env.building -f docker-compose.building.yml up -d --wait
```

## 7. Connect the gateway and sensors

Register the gateway in the TTN Console with its actual EUI and frequency plan,
then configure its server destination:

| Gateway transport | Destination |
| --- | --- |
| Semtech UDP packet forwarder | Server IP, UDP port `1700` |
| LoRa Basics Station | `ws://192.168.10.20:1887`, with a gateway API key |

For Basics Station, create an API key with gateway link rights. Match the
frequency plan to the gateway and sensor firmware; the repository defaults
use EU868.

Register/provision the sensors with matching JoinEUI, AppKey, LoRaWAN versions,
and frequency plan. The generator creates a new shared AppKey for a new
building. Existing sensors need their original credentials, or must be
reflashed with the new credentials. The example AppKey is not a recovered key
for existing hardware.

After connecting a sensor, verify:

1. The gateway is online in TTN.
2. TTN receives and decodes a real uplink.
3. The webhook delivers it to SBMS.
4. The device appears in SBMS's discovery inbox.
5. You can place it on a floor and see readings, history, and alarms.

Healthy containers alone do not verify the radio, decoder, or webhook path.

## Restarts, updates, and troubleshooting

For ordinary starts or environment updates:

```text
docker compose --env-file .env.building -f docker-compose.building.yml up -d --wait
```

After updating repository source, add `--build` to rebuild SBMS. Keep using
both `--env-file .env.building` and `-f docker-compose.building.yml`.

To inspect failures:

```text
docker compose --env-file .env.building -f docker-compose.building.yml logs --tail 100 stack sbms-app
```

| Symptom | Check |
| --- | --- |
| Required environment variable is missing | Include `--env-file .env.building`; generate the configuration first |
| TTN configuration "is a directory" | Ensure `deploy/stack/ttn-lw-stack.yml` is a generated file; recreate the TTN container after correcting the mount |
| TTN tries to load `cert.pem` in LAN HTTP mode | Use the updated generator, which disables unused TLS listeners |
| Port `8000` is already allocated | Free the port or change `SBMS_HTTP_PORT`, then rerun Compose |
| Docker Hub hostname lookup/download fails | Check host DNS, network access, and any required Docker Desktop proxy; retry the pull |

## Moving an existing installation

Moving the running installation requires backup/restore of all five volumes:

- `sbms-data`
- `sbms-uploads`
- `tts-postgres`
- `tts-redis`
- `tts-blob`

Preserve `.env.building` and `deploy/stack/ttn-lw-stack.yml` as well. Keep the
Compose project name stable, and update the public host address and TTN URLs
for the destination machine. Copying only the repository does not transfer
accounts, devices, readings, or LoRaWAN sessions.

For a move, preserve the existing credentials rather than running the fresh
installation generator. `docker compose down` preserves volumes; adding `-v`
deletes them. See [backups and upgrades](README.md#backups-and-upgrades) for the
backup procedure.

## References

- [Repository deployment guide](README.md)
- [Docker Desktop installation on Windows](https://docs.docker.com/desktop/setup/install/windows-install/)
- [Official TTN initialization guide](https://www.thethingsindustries.com/docs/enterprise/docker/running-the-stack/)
