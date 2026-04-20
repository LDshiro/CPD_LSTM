from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Protocol, runtime_checkable

from tenacity import retry, stop_after_attempt, wait_fixed


@dataclass(frozen=True)
class HistoricalRequest:
    dataset: str
    symbols: tuple[str, ...]
    schema: str
    start: str
    end: str
    stype_in: str
    stype_out: str
    limit: int | None = None


@dataclass(frozen=True)
class RequestEstimate:
    cost_usd: float | None
    billable_size_bytes: int | None


@runtime_checkable
class DatabentoClientProtocol(Protocol):
    def get_range(self, request: HistoricalRequest) -> Any:
        ...

    def estimate(self, request: HistoricalRequest) -> RequestEstimate | None:
        ...


class HistoricalDatabentoClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        max_retries: int = 3,
        retry_backoff_seconds: int = 5,
    ) -> None:
        try:
            import databento as db
        except ImportError as exc:  # pragma: no cover - depends on optional dependency
            raise RuntimeError("databento package is required for live vendor calls") from exc
        self._db = db
        self._client = db.Historical(api_key or os.getenv("DATABENTO_API_KEY"))
        self._max_retries = max_retries
        self._retry_backoff_seconds = retry_backoff_seconds

    def get_range(self, request: HistoricalRequest) -> Any:
        return self._timeseries_call(request)

    def estimate(self, request: HistoricalRequest) -> RequestEstimate | None:
        metadata = getattr(self._client, "metadata", None)
        if metadata is None:
            return None

        cost_usd: float | None = None
        billable_size_bytes: int | None = None
        params = {
            "dataset": request.dataset,
            "symbols": list(request.symbols),
            "schema": request.schema,
            "start": request.start,
            "end": request.end,
            "stype_in": request.stype_in,
            "stype_out": request.stype_out,
            "limit": request.limit,
        }
        if hasattr(metadata, "get_cost"):
            cost_usd = float(self._metadata_call("get_cost", params))
        if hasattr(metadata, "get_billable_size"):
            billable_size_bytes = int(self._metadata_call("get_billable_size", params))
        if cost_usd is None and billable_size_bytes is None:
            return None
        return RequestEstimate(cost_usd=cost_usd, billable_size_bytes=billable_size_bytes)

    def _timeseries_call(self, request: HistoricalRequest) -> Any:
        @retry(stop=stop_after_attempt(self._max_retries), wait=wait_fixed(self._retry_backoff_seconds), reraise=True)
        def _call() -> Any:
            return self._client.timeseries.get_range(
                dataset=request.dataset,
                symbols=list(request.symbols),
                schema=request.schema,
                start=request.start,
                end=request.end,
                stype_in=request.stype_in,
                stype_out=request.stype_out,
                limit=request.limit,
            )

        return _call()

    def _metadata_call(self, method_name: str, params: dict[str, object]) -> Any:
        @retry(stop=stop_after_attempt(self._max_retries), wait=wait_fixed(self._retry_backoff_seconds), reraise=True)
        def _call() -> Any:
            metadata = getattr(self._client, "metadata")
            method = getattr(metadata, method_name)
            return method(**params)

        return _call()
