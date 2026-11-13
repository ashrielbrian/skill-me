import json
import time
import urllib.error
import urllib.request

from ..config import settings
from ..errors import UpstreamError


class CarrierClient:
    def __init__(self, base_url: str, timeout_s: float, retries: int = 1, backoff_s: float = 0.5):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.retries = retries
        self.backoff_s = backoff_s

    def _post(self, path: str, payload: dict) -> dict:
        request = urllib.request.Request(
            f"{self.base_url}/{path}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        last = None
        for attempt in range(self.retries):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                    return json.loads(response.read())
            except urllib.error.URLError as exc:
                last = exc
                if attempt < self.retries - 1:
                    time.sleep(self.backoff_s * (attempt + 1))
        raise UpstreamError(
            f"carrier call {path} failed after {self.retries} attempts: {last.reason}"
        ) from last

    def get_rate(self, carrier: str, weight_g: int, dest_country: str) -> int:
        data = self._post("rates", {"carrier": carrier, "weight_g": weight_g, "dest": dest_country})
        return int(data["rate_cents"])

    def create_label(self, carrier: str, weight_g: int, dest_country: str) -> str:
        data = self._post("labels", {"carrier": carrier, "weight_g": weight_g, "dest": dest_country})
        return data["label_id"]


_client: CarrierClient | None = None


def get_client() -> CarrierClient:
    global _client
    if _client is None:
        _client = CarrierClient(
            settings.carrier_base_url,
            settings.carrier_timeout_s,
            retries=settings.carrier_retries,
            backoff_s=settings.carrier_backoff_s,
        )
    return _client
