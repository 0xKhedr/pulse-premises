import logging
import time
from typing import Any

import requests

from app.config import settings

logger = logging.getLogger(__name__)


class ThingsBoardClient:
    def __init__(self):
        self.base_url = settings.THINGSBOARD_URL
        self.username = settings.TB_USERNAME
        self.password = settings.TB_PASSWORD
        self._token: str | None = None
        self._token_expiry: float = 0

    def login(self, force_refresh: bool = False) -> str:
        if self._token and not force_refresh and time.time() < self._token_expiry:
            return self._token

        try:
            r = requests.post(
                f"{self.base_url}/api/auth/login",
                json={
                    "username": self.username,
                    "password": self.password,
                },
                timeout=20,
            )
            r.raise_for_status()
            
            data = r.json()
            self._token = data.get("token")
            # Assume tokens are valid for 2.5 hours, refresh at 2 hours
            self._token_expiry = time.time() + 7200
            
            if not self._token:
                raise ValueError("ThingsBoard response missing token")
            
            return self._token
        except requests.RequestException as e:
            logger.error(f"Failed to authenticate with ThingsBoard: {e}")
            raise

    def get_headers(self, force_refresh: bool = False) -> dict[str, str]:
        token = self.login(force_refresh=force_refresh)
        return {
            "X-Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = f"{self.base_url}{path}"
        try:
            r = requests.request(
                method,
                url,
                headers=self.get_headers(),
                timeout=20,
                **kwargs,
            )

            if r.status_code == 401:
                logger.info("ThingsBoard token expired. Refreshing token...")
                r = requests.request(
                    method,
                    url,
                    headers=self.get_headers(force_refresh=True),
                    timeout=20,
                    **kwargs,
                )

            r.raise_for_status()
            return r
        except requests.RequestException as e:
            logger.error(f"ThingsBoard request failed [{method} {path}]: {e}")
            raise

    def get_device_by_name(self, device_name: str) -> dict[str, Any] | None:
        try:
            r = self.request(
                "GET",
                f"/api/tenant/devices?deviceName={device_name}",
            )
            return r.json()
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                return None
            raise

    def send_telemetry(self, device_id: str, payload: dict) -> None:
        # device_id here means the ThingsBoard ID
        self.request(
            "POST",
            f"/api/plugins/telemetry/DEVICE/{device_id}/timeseries/ANY",
            json=payload,
        )

tb_client = ThingsBoardClient()
