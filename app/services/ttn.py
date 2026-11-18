import logging
from typing import Any
from urllib.parse import quote

import truststore

# Use the operating system CA store so managed Windows installations and
# ordinary Linux containers validate TTN TLS through their native trust roots.
# Verification remains enabled; this does not use verify=False.
truststore.inject_into_ssl()

import requests

from app.config import settings

logger = logging.getLogger(__name__)


class TTNClient:
    def __init__(self):
        self.base_url = settings.TTN_BASE_URL.rstrip("/")
        self.app_id = settings.TTN_APP_ID
        self.api_key = settings.TTN_API_KEY.strip() or settings.TTN_GATEWAY_MANAGEMENT_API_KEY.strip()
        # Where we connect. Not necessarily what the cluster calls itself.
        self.host = (
            settings.TTN_CLUSTER_ADDRESS.strip()
            or self.base_url.replace("https://", "").replace("http://", "")
        )

    def get_headers(self, *, gateway: bool = False, management: bool = False) -> dict[str, str]:
        api_key = self.api_key
        if management:
            api_key = settings.TTN_GATEWAY_MANAGEMENT_API_KEY.strip()
            if not api_key:
                raise RuntimeError("TTN gateway management key is not configured")
        elif gateway:
            api_key = (settings.TTN_GATEWAY_MANAGEMENT_API_KEY.strip()
                       or settings.AUTO_ENROLLMENT_GATEWAY_API_KEY.strip() or api_key)
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(self, method: str, path: str, *, gateway: bool = False,
                 management: bool = False, sensitive: bool = False, **kwargs) -> requests.Response:
        if settings.PREVIEW_MODE:
            raise RuntimeError("TTN changes are unavailable in the preview workspace")
        url = f"{self.base_url}{path}"
        try:
            r = requests.request(
                method,
                url,
                headers=self.get_headers(gateway=gateway, management=management),
                timeout=20,
                **kwargs,
            )
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            logger.error(f"TTN request failed [{method} {path}]: {e}")
            if not management and not sensitive and hasattr(e, "response") and e.response is not None:
                logger.error(f"TTN response text: {e.response.text}")
            raise

    def ensure_application(self) -> None:
        path = f"/api/v3/applications/{quote(self.app_id, safe='')}"
        try:
            self._request("GET", path, params={"field_mask": "ids"})
            return
        except requests.HTTPError as exc:
            if exc.response is None or exc.response.status_code != 404:
                raise
        owner_type = settings.TTN_GATEWAY_OWNER_TYPE
        owner = settings.TTN_GATEWAY_OWNER_ID.strip()
        if owner_type not in {"users", "organizations"} or not owner:
            raise ValueError("Configure a valid TTN gateway/application owner")
        try:
            self._request(
                "POST", f"/api/v3/{owner_type}/{quote(owner, safe='')}/applications", management=True,
                json={"application": {
                    "ids": {"application_id": self.app_id}, "name": "Smart Building",
                    "network_server_address": self.host, "application_server_address": self.host,
                    "join_server_address": self.host,
                }},
            )
        except requests.HTTPError as exc:
            if exc.response is None or exc.response.status_code != 409:
                raise
            # Another worker may have created it. Verify access before continuing.
            self._request("GET", path, params={"field_mask": "ids"})

    def list_application_webhooks(self) -> list[dict[str, Any]]:
        hooks = []
        page = 1
        while True:
            entries = self._request(
                "GET", f"/api/v3/as/webhooks/{quote(self.app_id, safe='')}", sensitive=True,
                params={"field_mask": "ids,base_url,headers,format,uplink_message,paused", "limit": 1000, "page": page},
            ).json().get("webhooks", [])
            hooks.extend(entries)
            if len(entries) < 1000:
                return hooks
            page += 1

    def set_application_webhook(self, webhook: dict[str, Any]) -> None:
        self._request(
            "PUT", f"/api/v3/as/webhooks/{quote(self.app_id, safe='')}/{quote(webhook['ids']['webhook_id'], safe='')}",
            sensitive=True, json={"webhook": webhook, "field_mask": {
                "paths": ["base_url", "headers", "format", "uplink_message", "paused"],
            }},
        )

    def get_managed_gateway(self, gateway_id: str) -> dict[str, Any] | None:
        try:
            return self._request(
                "GET", f"/api/v3/gateways/{quote(gateway_id, safe='')}", management=True,
                params={"field_mask": "ids,name,attributes,frequency_plan_ids,require_authenticated_connection"},
            ).json()
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return None
            raise

    def register_gateway(self, candidate: dict) -> dict[str, Any]:
        owner_type = settings.TTN_GATEWAY_OWNER_TYPE
        if owner_type not in {"users", "organizations"}:
            raise ValueError("Gateway owner type must be users or organizations")
        owner = settings.TTN_GATEWAY_OWNER_ID.strip()
        if not owner:
            raise ValueError("Gateway owner ID is required")
        return self._request(
            "POST", f"/api/v3/{owner_type}/{quote(owner, safe='')}/gateways", management=True,
            json={"gateway": {
                "ids": {"gateway_id": candidate["gateway_id"], "eui": candidate["eui"]},
                "name": candidate["name"],
                "attributes": {"sbms-approval": candidate["approval_token"]},
                "frequency_plan_ids": [candidate["frequency_plan_id"]],
                "gateway_server_address": settings.TTN_GATEWAY_SERVER_ADDRESS.strip() or self.host,
                "require_authenticated_connection": bool(candidate["require_authenticated_connection"]),
                "status_public": False, "location_public": False, "enforce_duty_cycle": True,
            }},
        ).json()

    def create_gateway_link_key(self, gateway_id: str) -> dict[str, Any]:
        return self._request(
            "POST", f"/api/v3/gateways/{quote(gateway_id, safe='')}/api-keys", management=True,
            json={"name": "SBMS gateway connection", "rights": ["RIGHT_GATEWAY_LINK"]},
        ).json()

    def delete_gateway(self, gateway_id: str) -> None:
        try:
            self._request(
                "DELETE", f"/api/v3/gateways/{quote(gateway_id, safe='')}", management=True,
            )
        except requests.HTTPError as exc:
            # An already absent registration also completes a retried deletion.
            if exc.response is None or exc.response.status_code != 404:
                raise

    def create_application_device(
        self, device_id: str, dev_eui: str, join_eui: str, name: str, description: str
    ) -> dict[str, Any]:
        payload = {
            "end_device": {
                "ids": {
                    "device_id": device_id,
                    "dev_eui": dev_eui,
                    "join_eui": join_eui,
                },
                "name": name,
                "description": description,
                "lorawan_version": settings.LORAWAN_VERSION,
                "lorawan_phy_version": settings.LORAWAN_PHY_VERSION,
                "frequency_plan_id": settings.LORAWAN_FREQUENCY_PLAN_ID,
                "supports_join": True,
            },
            "field_mask": {
                "paths": [
                    "join_server_address",
                    "network_server_address",
                    "application_server_address",
                    "lorawan_version",
                    "lorawan_phy_version",
                    "frequency_plan_id",
                    "supports_join",
                    "name",
                    "description",
                ]
            },
        }

        # TTN typically routes IS/NS/AS/JS traffic through different components,
        # but in TTN V3 CE, the API is served centrally.
        # Identity server
        payload["end_device"]["join_server_address"] = self.host
        payload["end_device"]["network_server_address"] = self.host
        payload["end_device"]["application_server_address"] = self.host
        
        return self._request(
            "POST",
            f"/api/v3/applications/{self.app_id}/devices",
            json=payload,
        ).json()

    def create_join_server_entry(
        self, device_id: str, dev_eui: str, join_eui: str, app_key: str
    ) -> dict[str, Any]:
        payload = {
            "end_device": {
                "ids": {
                    "device_id": device_id,
                    "dev_eui": dev_eui,
                    "join_eui": join_eui,
                },
                "network_server_address": self.host,
                "application_server_address": self.host,
                "root_keys": {"app_key": {"key": app_key}},
            },
            "field_mask": {
                "paths": [
                    "network_server_address",
                    "application_server_address",
                    "root_keys.app_key.key",
                ]
            },
        }
        return self._request(
            "PUT",
            f"/api/v3/js/applications/{self.app_id}/devices/{device_id}",
            json=payload,
        ).json()

    def create_network_server_entry(
        self, device_id: str, dev_eui: str, join_eui: str
    ) -> dict[str, Any]:
        payload = {
            "end_device": {
                "ids": {
                    "device_id": device_id,
                    "dev_eui": dev_eui,
                    "join_eui": join_eui,
                },
                "lorawan_version": settings.LORAWAN_VERSION,
                "lorawan_phy_version": settings.LORAWAN_PHY_VERSION,
                "frequency_plan_id": settings.LORAWAN_FREQUENCY_PLAN_ID,
                "supports_join": True,
            },
            "field_mask": {
                "paths": [
                    "lorawan_version",
                    "lorawan_phy_version",
                    "frequency_plan_id",
                    "supports_join",
                ]
            },
        }
        return self._request(
            "PUT",
            f"/api/v3/ns/applications/{self.app_id}/devices/{device_id}",
            json=payload,
        ).json()

    def create_application_server_entry(
        self, device_id: str, dev_eui: str, join_eui: str
    ) -> dict[str, Any]:
        payload = {
            "end_device": {
                "ids": {
                    "device_id": device_id,
                    "dev_eui": dev_eui,
                    "join_eui": join_eui,
                }
            },
            "field_mask": {"paths": []},
        }
        return self._request(
            "PUT",
            f"/api/v3/as/applications/{self.app_id}/devices/{device_id}",
            json=payload,
        ).json()

    def set_payload_formatter(self, device_id: str) -> None:
        formatter_script = """
function decodeUplink(input) {
  return {
    data: input.bytes,
    warnings: [],
    errors: []
  };
}
"""
        payload = {
            "end_device": {
                "formatters": {
                    "up_formatter": "FORMATTER_JAVASCRIPT",
                    "up_formatter_parameter": formatter_script,
                }
            },
            "field_mask": {
                "paths": [
                    "formatters.up_formatter",
                    "formatters.up_formatter_parameter",
                ]
            },
        }
        self._request(
            "PUT",
            f"/api/v3/as/applications/{self.app_id}/devices/{device_id}",
            json=payload,
        )

    def delete_device(self, device_id: str) -> None:
        app_id = quote(self.app_id, safe="")
        device_id = quote(device_id, safe="")
        paths = (
            f"/api/v3/as/applications/{app_id}/devices/{device_id}",
            f"/api/v3/ns/applications/{app_id}/devices/{device_id}",
            f"/api/v3/js/applications/{app_id}/devices/{device_id}",
            f"/api/v3/applications/{app_id}/devices/{device_id}",
        )
        for path in paths:
            try:
                self._request("DELETE", path)
            except requests.HTTPError as error:
                if error.response is not None and error.response.status_code == 404:
                    continue
                raise

    def get_application_device(self, device_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/api/v3/applications/{quote(self.app_id, safe='')}/devices/{quote(device_id, safe='')}",
            params={"field_mask": "ids,name,created_at,updated_at"},
        ).json()

    def push_downlink(
        self, device_id: str, *, f_port: int, frm_payload: str,
        confirmed: bool, correlation_id: str,
    ) -> None:
        """Append one message; preserve the existing queue and Class A timing."""
        self._request(
            "POST",
            f"/api/v3/as/applications/{quote(self.app_id, safe='')}/devices/{quote(device_id, safe='')}/down/push",
            json={"downlinks": [{
                "f_port": f_port,
                "frm_payload": frm_payload,
                "confirmed": confirmed,
                "priority": "NORMAL",
                "correlation_ids": [correlation_id],
            }]},
        )

    def get_gateway_info(self, gateway_id: str) -> dict[str, Any]:
        if settings.PREVIEW_MODE:
            return {"ids": {"gateway_id": gateway_id}, "name": "Preview gateway"}
        return self._request(
            "GET",
            f"/api/v3/gateways/{gateway_id}",
            gateway=True,
        ).json()

    def get_gateway_stats(self, gateway_id: str) -> dict[str, Any]:
        if settings.PREVIEW_MODE:
            from app.services.preview import gateway_stats
            return gateway_stats(gateway_id)
        try:
            return self._request(
                "GET",
                f"/api/v3/gs/gateways/{gateway_id}/connection/stats",
                gateway=True,
            ).json()
        except requests.HTTPError as error:
            # A registered gateway with no active connection is offline.
            # Preserve authentication failures and genuinely missing gateways.
            if error.response is not None and error.response.status_code == 404:
                try:
                    details = error.response.json().get("details", [])
                except ValueError:
                    details = []
                if any(
                    isinstance(detail, dict)
                    and detail.get("namespace") == "pkg/gatewayserver"
                    and detail.get("name") == "not_connected"
                    for detail in details
                ):
                    return {}
            raise

    def inspect_device_registration(self, device_id: str, dev_eui: str) -> dict[str, Any]:
        """Read registration/session metadata only; never request session keys."""
        suffix = f"applications/{quote(self.app_id, safe='')}/devices/{quote(device_id, safe='')}"
        components = {}
        session_started_at = None
        for component in ("is", "ns", "as", "js"):
            prefix = "" if component == "is" else component + "/"
            fields = "ids,session.started_at" if component == "ns" else "ids"
            try:
                entry = self._request("GET", f"/api/v3/{prefix}{suffix}", params={"field_mask": fields}).json()
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 404:
                    components[component] = "missing"
                    continue
                raise
            observed_eui = entry.get("ids", {}).get("dev_eui", "")
            if observed_eui and observed_eui.upper() != dev_eui.upper():
                raise ValueError("TTN identity does not match the enrollment DevEUI")
            components[component] = "registered"
            if component == "ns":
                session_started_at = entry.get("session", {}).get("started_at")
        return {"components": components, "complete": all(v == "registered" for v in components.values()),
                "session_started_at": session_started_at}

ttn_client = TTNClient()
