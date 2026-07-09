from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Iterator

import httpx

BASE_URL = "https://clinicaltrials.gov/api/v2"
MAX_PAGE_SIZE = 1000
DEFAULT_PAGE_SIZE = 100
DEFAULT_RATE_LIMIT = 10
USER_AGENT = "ClinicalTrialsGovParser/0.1.0 (+https://github.com/clinicaltrials-parser)"


class RateLimiter:
    def __init__(self, calls_per_sec: float):
        if calls_per_sec <= 0:
            raise ValueError(f"calls_per_sec must be > 0, got {calls_per_sec}")
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

    def iter_studies(self, **params: Any) -> Iterator[dict[str, Any]]:
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


class AsyncRateLimiter:
    def __init__(self, calls_per_sec: float):
        if calls_per_sec <= 0:
            raise ValueError(f"calls_per_sec must be > 0, got {calls_per_sec}")
        self.interval = 1.0 / calls_per_sec
        self._last = 0.0

    async def wait(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last
        if elapsed < self.interval:
            await asyncio.sleep(self.interval - elapsed)
        self._last = time.monotonic()


@dataclass
class AsyncClinicalTrialsClient:
    """Async counterpart of ClinicalTrialsClient for concurrent fetches."""

    base_url: str = BASE_URL
    page_size: int = DEFAULT_PAGE_SIZE
    rate_limit: float = DEFAULT_RATE_LIMIT
    timeout: float = 30.0
    max_retries: int = 3
    _client: httpx.AsyncClient = field(default=None, repr=False)
    _rate_limiter: AsyncRateLimiter = field(default=None, repr=False)

    def __post_init__(self):
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                headers={"User-Agent": USER_AGENT},
            )
        if self._rate_limiter is None:
            self._rate_limiter = AsyncRateLimiter(self.rate_limit)

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> AsyncClinicalTrialsClient:
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        url = f"{self.base_url}{path}"
        for attempt in range(self.max_retries + 1):
            await self._rate_limiter.wait()
            try:
                resp = await self._client.request(method, url, **kwargs)
            except (httpx.TimeoutException, httpx.ConnectError) as e:
                if attempt < self.max_retries:
                    await asyncio.sleep(2 ** attempt)
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
                    await asyncio.sleep(retry_after)
                    continue
                raise RuntimeError("Rate limited, exhausted retries")

            if resp.status_code == 503:
                if attempt < self.max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise RuntimeError("Service unavailable, exhausted retries")

            resp.raise_for_status()
            return resp

    async def get_stats(self, **params: Any) -> dict[str, Any]:
        resp = await self._request("GET", "/stats/size", params=params)
        return resp.json()

    async def get_total_count(self, **params: Any) -> int:
        stats = await self.get_stats(**params)
        return stats["totalCount"]

    async def get_study(self, nct_id: str, fields: str | None = None) -> dict[str, Any]:
        params = {}
        if fields:
            params["fields"] = fields
        resp = await self._request("GET", f"/studies/{nct_id}", params=params)
        return resp.json()

    async def get_studies_page(
        self, page_token: str | None = None, **params: Any
    ) -> tuple[list[dict[str, Any]], str | None]:
        params_out = dict(params)
        params_out.setdefault("pageSize", self.page_size)
        if page_token:
            params_out["pageToken"] = page_token
        resp = await self._request("GET", "/studies", params=params_out)
        data = resp.json()
        studies = data.get("studies", [])
        next_token = data.get("nextPageToken")
        return studies, next_token

    async def iter_studies(self, **params: Any) -> AsyncIterator[dict[str, Any]]:
        next_token = None
        while True:
            page, next_token = await self.get_studies_page(page_token=next_token, **params)
            for study in page:
                yield study
            if not next_token:
                break

    async def fetch_all_studies(self, **params: Any) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        async for study in self.iter_studies(**params):
            results.append(study)
        return results

    async def get_field_metadata(self) -> dict[str, Any]:
        resp = await self._request("GET", "/studies/metadata")
        return resp.json()

    async def get_search_areas(self) -> list[str]:
        resp = await self._request("GET", "/studies/search-areas")
        return resp.json()

    async def get_enums(self) -> dict[str, Any]:
        resp = await self._request("GET", "/studies/enums")
        return resp.json()
