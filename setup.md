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

In the TTN Console:

1. Create an application with ID `smart-building-lora-2` (or your `.env` value
   for `TTN_APP_ID`).
2. Create an application API key with device/key management, traffic, and
   integration rights. Paste it into `.env` as `TTN_API_KEY`.
3. Under **Integrations > Webhooks**, add a custom JSON webhook:

| Setting | Value |
| --- | --- |
| Base URL | `http://sbms-app:8000` |
| Uplink message path | `/ttn-webhook` |
| Header name | `X-Webhook-Secret` |
| Header value | The `TTN_WEBHOOK_SECRET` value from `.env` |

Register your gateway with its actual EUI and frequency plan. Configure its
destination as the server's **LAN address**, UDP port **1700**, or
`ws://<server>:1887` for Basics Station with a gateway link API key.
Allow the relevant ports through the host firewall.

For portal discovery, set `AUTO_ENROLLMENT_GATEWAY_API_KEY` in `.env` to a
gateway traffic API key. Match the sensors' JoinEUI, AppKey, frequency plan,
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
