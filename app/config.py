from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    DB_FILE: str = "smarthome.db"

    TTN_BASE_URL: str = "https://eu1.cloud.thethings.network"
    TTN_APP_ID: str = "smart-building-lora-2"
    TTN_API_KEY: str = ""
    JOIN_EUI: str = "0000000000000000"
    LORAWAN_APP_KEY: str = ""
    LORAWAN_FREQUENCY_PLAN_ID: str = "EU_863_870_TTN"
    LORAWAN_VERSION: str = "MAC_V1_0_3"
    LORAWAN_PHY_VERSION: str = "PHY_V1_0_3_REV_A"
    TTN_WEBHOOK_SECRET: str = ""
    # The address the LoRaWAN cluster uses to identify its own Join, Network
    # and Application servers when a device is registered. On TTN's cloud this
    # is the same host the API is reached at, so leaving this blank derives it
    # from TTN_BASE_URL. A self-hosted stack reached through a different name
    # -- host.docker.internal from a container, say, while the stack calls
    # itself localhost -- rejects registration with
    # network_server_address_mismatch unless this names what the stack expects.
    TTN_CLUSTER_ADDRESS: str = ""

    THINGSBOARD_URL: str = "http://localhost:8080"
    TB_USERNAME: str = "tenant@thingsboard.org"
    TB_PASSWORD: str = "tenant"

    ALERT_EMAIL_ENABLED: bool = False
    ALERT_EMAIL_FROM: str = ""
    ALERT_EMAIL_PASSWORD: str = ""
    ALERT_EMAIL_TO: str = ""
    SMTP_SERVER: str = "smtp.gmail.com"
    SMTP_PORT: int = 587

    ADMIN_EMAIL: str = "admin@system.local"
    ADMIN_PASSWORD: str = "change-me"
    SESSION_TTL_SECONDS: int = 28800
    PASSWORD_PBKDF2_ITERATIONS: int = 200000
    COOKIE_SECURE: bool = False

    LOCAL_TEST_MODE: bool = False
    CORS_ORIGINS: str = ""
    DEVICE_STALE_AFTER_SECONDS: int = 300
    DEVICE_OFFLINE_AFTER_SECONDS: int = 86400

    # Ceiling on devices sitting in the unplaced discovery inbox. Zero-touch
    # discovery creates a row per unknown DevEUI, so anyone able to reach the
    # webhook could otherwise grow the devices table without bound. Existing
    # devices are unaffected; only new discoveries are refused at the cap.
    MAX_UNPLACED_DISCOVERED_DEVICES: int = 500

    # --- Over-the-air enrollment ------------------------------------------
    # A node whose DevEUI the network has never seen cannot join, and cannot
    # therefore be discovered by the webhook. Enrollment closes that gap: the
    # app watches gateway traffic for join requests, and registers a DevEUI in
    # TTN once the request's MIC proves the sender already holds this
    # deployment's shared AppKey.
    #
    # Off by default, and an open window is deliberately not persisted -- a
    # restart closes it, so the system fails closed.
    AUTO_ENROLLMENT_ENABLED: bool = False
    # How long a window stays open once an administrator opens one.
    AUTO_ENROLLMENT_WINDOW_SECONDS: int = 600
    # Registrations allowed per window, so one open window cannot be used to
    # flood the TTN device registry.
    AUTO_ENROLLMENT_MAX_PER_WINDOW: int = 10
    # Gateways whose traffic is watched. Empty means every gateway in the
    # gateways table.
    AUTO_ENROLLMENT_GATEWAY_IDS: str = ""
    # Enrollment reads gateway *traffic* events, which are a different scope
    # from the application rights TTN_API_KEY carries: an application-scoped
    # key silently receives only status events, never uplinks. Mint a key with
    # RIGHT_GATEWAY_READ_TRAFFIC on the gateway and set it here. Falls back to
    # TTN_API_KEY, which works only if that key happens to be gateway-scoped.
    AUTO_ENROLLMENT_GATEWAY_API_KEY: str = ""

    # A decoded uplink is self-describing, and the webhook already stores
    # every named value it carries. Learning registers a field the first
    # time it is seen so it also gets a label, display order and something
    # an alarm rule can point at -- which is what a profile adds over raw
    # storage. Learned fields are always optional and nullable, so no
    # existing device can be invalidated by one, and a profile stops
    # learning at the cap in app/services/profile_learning.py.
    PROFILE_FIELD_LEARNING_ENABLED: bool = True


settings = Settings()
