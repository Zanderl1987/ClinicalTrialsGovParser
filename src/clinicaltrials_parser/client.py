from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx

BASE_URL = "https://clinicaltrials.gov/api/v2"
MAX_PAGE_SIZE = 1000
DEFAULT_PAGE_SIZE = 100
DEFAULT_RATE_LIMIT = 10
USER_AGENT = "ClinicalTrialsGovParser/0.1.0 (+https://github.com/clinicaltrials-parser)"


class RateLimiter:
    def __init__(self, calls_per_sec: float):
        self.interval = 1.0 / calls_per_sec
        self._last = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last
        if elapsed < self.interval:
            time.sleep(self.interval - elapsed)
        self._last = time.monotonic()


@dataclass
class ClinicalTrialsClient:
    base_url: str = BASE_URL
    page_size: int = DEFAULT_PAGE_SIZE
    rate_limit: float = DEFAULT_RATE_LIMIT
    timeout: float = 30.0
    max_retries: int = 3
    _client: httpx.Client = field(default=None, repr=False)
    _rate_limiter: RateLimiter = field(default=None, repr=False)

    def __post_init__(self):
        if self._client is None:
            self._client = httpx.Client(
                timeout=httpx.Timeout(self.timeout),
                headers={"User-Agent": USER_AGENT},
            )
        if self._rate_limiter is None:
            self._rate_limiter = RateLimiter(self.rate_limit)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ClinicalTrialsClient:
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        url = f"{self.base_url}{path}"
        for attempt in range(self.max_retries + 1):
            self._rate_limiter.wait()
            try:
                resp = self._client.request(method, url, **kwargs)
            except (httpx.TimeoutException, httpx.ConnectError) as e:
                if attempt < self.max_retries:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"Request failed after {self.max_retries} retries: {e}") from e

            if resp.status_code == 403:
                body = resp.text[:500]
                raise RuntimeError(
                    f"Access forbidden (403) to {url}. "
                    f"ClinicalTrials.gov may be blocking this IP or User-Agent. "
                    f"Response: {body}"
                )

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 5))
                if attempt < self.max_retries:
                    time.sleep(retry_after)
                    continue
                raise RuntimeError("Rate limited, exhausted retries")

            if resp.status_code == 503:
                if attempt < self.max_retries:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError("Service unavailable, exhausted retries")

            resp.raise_for_status()
            return resp

    def get_stats(self, **params: Any) -> dict[str, Any]:
        resp = self._request("GET", "/stats/size", params=params)
        return resp.json()

    def get_total_count(self, **params: Any) -> int:
        return self.get_stats(**params)["totalCount"]

    def get_study(self, nct_id: str, fields: str | None = None) -> dict[str, Any]:
        params = {}
        if fields:
            params["fields"] = fields
        resp = self._request("GET", f"/studies/{nct_id}", params=params)
        return resp.json()

    def get_studies_page(
        self, page_token: str | None = None, **params: Any
    ) -> tuple[list[dict[str, Any]], str | None]:
        params_out = dict(params)
        params_out.setdefault("pageSize", self.page_size)
        if page_token:
            params_out["pageToken"] = page_token
        resp = self._request("GET", "/studies", params=params_out)
        data = resp.json()
        studies = data.get("studies", [])
        next_token = data.get("nextPageToken")
        return studies, next_token

    def iter_studies(self, **params: Any) -> AsyncIterator[dict[str, Any]]:
        next_token = None
        while True:
            page, next_token = self.get_studies_page(page_token=next_token, **params)
            for study in page:
                yield study
            if not next_token:
                break

    def fetch_all_studies(self, **params: Any) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for study in self.iter_studies(**params):
            results.append(study)
        return results

    def get_field_metadata(self) -> dict[str, Any]:
        resp = self._request("GET", "/studies/metadata")
        return resp.json()

    def get_search_areas(self) -> list[str]:
        resp = self._request("GET", "/studies/search-areas")
        return resp.json()

    def get_enums(self) -> dict[str, Any]:
        resp = self._request("GET", "/studies/enums")
        return resp.json()
