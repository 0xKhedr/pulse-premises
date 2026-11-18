# Quick setup

## 1. Install and start Docker

- Install **Python 3.10+** and **Docker with Compose v2.24+**.
- On Windows, use Docker Desktop with **Linux containers**.
- Start Docker, then open PowerShell or a terminal in the project root
  (the folder containing `docker-compose.yml`).

## 2. Run one command

```text
python tools/setup.py
```

On Linux, use `python3` if that is your Python command. The first run downloads
images, builds the project, and starts SBMS, TTN, PostgreSQL, and Redis.
TTN creates its administrator and Console OAuth client automatically.

For access from other computers or a physical gateway, use the server's LAN
address instead of localhost:

```text
python tools/setup.py --host 192.168.1.50
```

Replace `192.168.1.50` with your server's address. The SBMS email defaults to
`admin@{host}`; add `--admin-email owner@example.com` to choose another email.

**Every setup run overwrites `.env` and the stack configuration and regenerates
cookie, cluster, webhook, and device keys.** Use the restart command below to
keep your configuration.

## 3. Open the apps

Fresh-install defaults:

| App | Address | Login | Password |
| --- | --- | --- | --- |
| SBMS | <http://localhost:8000/admin> | `admin@localhost` | `admin` |
| TTN Console | <http://localhost:1885/console> | `admin` | `admin` |

For a LAN setup, replace `localhost` in the addresses with your chosen host.
SBMS uses the email selected during setup. TTN's initial admin email is
`admin@127.0.0.1`. Existing database accounts retain their credentials.

Generated settings always use PostgreSQL password `root`, Console secret
`console`, SBMS password `admin`, and `AUTO_ENROLLMENT_ENABLED=true`.

## 4. Connect real devices

In the TTN Console, create one user backend key and save it in `.env` as
`TTN_GATEWAY_MANAGEMENT_API_KEY`. Grant `RIGHT_USER_APPLICATIONS_CREATE`,
`RIGHT_APPLICATION_ALL`, gateway create/info/settings/delete/API-key/link
rights, `RIGHT_GATEWAY_STATUS_READ` and `RIGHT_GATEWAY_TRAFFIC_READ`.
SBMS creates `TTN_APP_ID` if missing and configures its uplink webhook on
startup. Existing applications and matching webhooks are reused. You can
leave `TTN_API_KEY` empty. Compose supplies the internal webhook address and
SBMS copies `TTN_WEBHOOK_SECRET` into the webhook's authentication header.
The Gateways page reports setup status and permission errors.

Register your gateway with its actual EUI and frequency plan. Configure its
destination as the server's **LAN address**, UDP port **1700**, or
`ws://<server>:1887` for Basics Station with a gateway link API key.
Allow the relevant ports through the host firewall.

The shared backend key also handles portal discovery; no separate gateway
traffic key is needed. Match the sensors' JoinEUI, AppKey, frequency plan,
and protocol versions to `.env`. Apply environment changes with the command
below, then open **Scan for new devices** in SBMS and approve the discovered
devices. Place a device on a floor and verify a real reading.

## Restart, apply settings, or update

```text
docker compose up -d --build --wait
```

This keeps `.env`, generated keys, and stored data. To check startup problems:

```text
docker compose ps
docker compose logs --tail 100 stack sbms-app
```

See [the detailed setup guide](deploy/NEW_MACHINE_SETUP.md) for more options and
[the deployment guide](deploy/README.md) for backups and upgrades.
