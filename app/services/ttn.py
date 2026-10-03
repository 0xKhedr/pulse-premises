import logging
from typing import Any

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
        self.api_key = settings.TTN_API_KEY
        # Where we connect. Not necessarily what the cluster calls itself.
        self.host = (
            settings.TTN_CLUSTER_ADDRESS.strip()
            or self.base_url.replace("https://", "").replace("http://", "")
        )

    def get_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = f"{self.base_url}{path}"
        try:
            r = requests.request(
                method,
                url,
                headers=self.get_headers(),
                timeout=20,
                **kwargs,
            )
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            logger.error(f"TTN request failed [{method} {path}]: {e}")
            if hasattr(e, "response") and e.response is not None:
                logger.error(f"TTN response text: {e.response.text}")
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
        paths = (
            f"/api/v3/as/applications/{self.app_id}/devices/{device_id}",
            f"/api/v3/ns/applications/{self.app_id}/devices/{device_id}",
            f"/api/v3/js/applications/{self.app_id}/devices/{device_id}",
            f"/api/v3/applications/{self.app_id}/devices/{device_id}",
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
            f"/api/v3/applications/{self.app_id}/devices/{device_id}",
            params={"field_mask": "ids,name,created_at,updated_at"},
        ).json()

    def get_gateway_info(self, gateway_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/api/v3/gateways/{gateway_id}",
        ).json()

    def get_gateway_stats(self, gateway_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/api/v3/gs/gateways/{gateway_id}/connection/stats",
        ).json()

ttn_client = TTNClient()
