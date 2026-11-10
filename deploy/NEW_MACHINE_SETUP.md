# Set up SBMS and The Things Stack

The default setup runs four services: SBMS, The Things Stack (TTN), PostgreSQL,
and Redis. Docker builds SBMS from this repository and adds an initialization
entrypoint to the upstream TTN image.

## 1. Install the prerequisites

- Python 3.10 or newer (used only by the setup/configuration tools on the host).
- Docker Engine or Docker Desktop with Compose v2.24 or newer.
- A complete checkout of this repository.

On Windows, configure Docker Desktop for Linux containers. Run setup from
PowerShell; Git Bash is not required. On Linux, use `python3` in place of
`python` if needed. Start Docker before continuing.

## 2. Run setup

From the repository root:

```text
python tools/setup.py
```

For a server reached by other computers or physical gateways:

```text
python tools/setup.py --host 192.168.10.20 --admin-email owner@example.com
```

Replace the LAN address and email with your own. The host defaults to `localhost`
and the SBMS email defaults to `admin@{host}`. Setup writes `.env` and
`deploy/stack/ttn-lw-stack.yml`, overwriting either file if it already exists,
builds both application images, and waits for the services to become healthy.
Every run reapplies the arguments/defaults and regenerates cookie, cluster,
webhook, and device keys. For ordinary restarts with the current settings,
use `docker compose up -d --wait`.

The TTN entrypoint migrates its database, creates the initial administrator and
Console OAuth client, then starts the server. Completed creation steps are
recorded in the blob volume, so interrupted initialization can resume. Existing
database accounts remain in place when configuration files are overwritten.
Connection or validation errors stop initialization and are visible in
`docker compose logs stack`.

To inspect configuration before starting services:

```text
python tools/setup.py --prepare-only
```

Then review `.env` and start with `docker compose up -d --build --wait`.
Do not copy `.env.example` as a replacement for the generator: its key values
are samples. Setup also replaces partial configurations when only one generated
file exists.

## 3. Sign in

For the default localhost setup:

| Service | URL | Login |
| --- | --- | --- |
| SBMS | `http://localhost:8000/admin` | `admin@localhost` / `admin` |
| TTN Console | `http://localhost:1885/console` | `admin` / `admin` |

Use the configured server address instead of `localhost` for a LAN setup.
SBMS credentials are `ADMIN_EMAIL` / `ADMIN_PASSWORD` in `.env`.
TTN bootstrap settings are `TTS_BOOTSTRAP_EMAIL` (default `admin@127.0.0.1`)
and `TTS_BOOTSTRAP_PASSWORD` (default `admin`). Existing accounts keep their
current credentials; changing bootstrap settings does not reset them.

PostgreSQL defaults to password `root`. Compose passes the same password to
PostgreSQL and TTN. The Console OAuth secret is `console`.
Auto-enrollment is enabled by default. ThingsBoard is
optional and disabled by default.

The host ports are TCP 8000 for SBMS, TCP 1885 for TTN, UDP 1700 for Semtech
packet forwarders, and TCP 1887 for Basics Station. PostgreSQL and Redis have
no published ports. Use a stable LAN address for gateways and allow the
relevant ports through the host firewall. The default transport is HTTP on a
trusted LAN; configure TLS for exposure beyond it.

## 4. Connect TTN to SBMS

In the TTN Console:

1. Create an application matching `TTN_APP_ID` in `.env`
   (`smart-building-lora-2` by default).
2. Create an application API key with rights to read/write end devices and
   their keys, read traffic, and configure integrations. Set it as `TTN_API_KEY`
   in `.env`.
3. Add a custom JSON webhook under the application's Integrations > Webhooks:

| Setting | Value |
| --- | --- |
| Base URL | `http://sbms-app:8000` |
| Uplink message path | `/ttn-webhook` |
| Header name | `X-Webhook-Secret` |
| Header value | `TTN_WEBHOOK_SECRET` from `.env` |

Apply environment changes with `docker compose up -d --wait`.

Register the gateway with its actual EUI and matching frequency plan. Point
Semtech UDP forwarders at the server's LAN address on UDP 1700, or Basics
Station at `ws://<server>:1887` with a gateway link API key.

For portal discovery, set `AUTO_ENROLLMENT_GATEWAY_API_KEY` to a key with gateway
traffic access and configure `AUTO_ENROLLMENT_GATEWAY_IDS` as appropriate.
Auto-enrollment still requires an administrator to open a scan window and
approve each device.

The generator creates a new shared `LORAWAN_APP_KEY`. Existing sensors need
their original `JOIN_EUI` and AppKey preserved, or must be reflashed with the
new values. Match the gateway/sensor frequency plans and protocol versions.
Verify a real uplink reaches TTN, the webhook delivers it, and the device
appears in SBMS before placing it on a floor.

## Direct Ethernet gateway connection

1. Connect the gateway to the PC's Ethernet port. Use a stable IPv4 address
   on the PC's Ethernet adapter.
2. Set the gateway's packet-forwarder server to that **PC Ethernet IP**, with
   upstream and downstream UDP ports **1700**.
3. If the gateway uses `169.254.x.x`, ensure replies to `169.254.0.0/16` use
   Ethernet. Otherwise it can register in TTN but remain **Awaiting join**.

Replace the adapter/interface/profile names below with your own.

**Windows — permanent route, Administrator PowerShell:**

```powershell
Get-NetAdapter
New-NetRoute -DestinationPrefix '169.254.0.0/16' `
    -InterfaceAlias 'Ethernet' -NextHop '0.0.0.0' -RouteMetric 5
```

**Linux — immediate route (until restart):**

```bash
ip -br address
sudo ip route replace 169.254.0.0/16 dev enp3s0 scope link metric 5
```

For persistence, use **one** method matching your existing Linux configuration:

- **NetworkManager:** save the route to the wired profile, then reapply it:

  ```bash
  nmcli -f NAME,DEVICE connection show --active
  sudo nmcli connection modify "WIRED_PROFILE" +ipv4.routes "169.254.0.0/16 0.0.0.0 5"
  sudo nmcli device reapply enp3s0
  ```

- **Netplan:** append this route under the existing Ethernet interface in
  `/etc/netplan/*.yaml`, keeping its current addresses/DHCP settings:

  ```yaml
  routes:
    - to: 169.254.0.0/16
      scope: link
      metric: 5
  ```

  Apply locally with `sudo netplan try` and confirm when connectivity works.

Start Docker/the deployment, restart the sensor once, and confirm **Online**.
No firmware change is needed. Normal LAN connections use their existing routes.

References: [Windows routes](https://learn.microsoft.com/en-us/powershell/module/nettcpip/new-netroute),
[NetworkManager](https://networkmanager.pages.freedesktop.org/NetworkManager/NetworkManager/nmcli.html),
[Netplan routes](https://netplan.readthedocs.io/en/stable/netplan-yaml/#routing).

## Restarts and troubleshooting

```text
docker compose up -d --build --wait
docker compose ps
docker compose logs --tail 100 stack sbms-app
```

Both `.env` and `docker-compose.yml` load automatically. Source or entrypoint
changes need `--build`. Templates/static files can be mounted for development
with `-f docker-compose.yml -f docker-compose.dev.yml`.

On a network using a managed CA, save the root certificate in DER format as
`.docker-local-ca.cer` and set `SBMS_LOCAL_CA_FILE=.docker-local-ca.cer` in `.env`
before building SBMS. Otherwise the empty certificate placeholder is used.

If startup fails, fix the reported problem and rerun `python tools/setup.py`.
The compatibility command `bash linux/initialize-building.sh [email]` starts
only TTN and its dependencies; normal setup does not require it.

## Moving an existing installation

Preserve `.env`, `deploy/stack/ttn-lw-stack.yml`, and all five volumes:
`sbms-data`, `sbms-uploads`, `tts-postgres`, `tts-redis`, and `tts-blob`.
The blob volume includes the initialization checkpoints and must remain paired
with the corresponding PostgreSQL backup. Keep `COMPOSE_PROJECT_NAME` stable
and update the public host address/URLs for the destination.

Copying the repository alone does not transfer accounts, registrations,
readings, or LoRaWAN sessions. `docker compose down` preserves volumes;
`docker compose down -v` deletes them. See [the deployment guide](README.md)
for backups, restoration, upgrades, and optional integrations.
