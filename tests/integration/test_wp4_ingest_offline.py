from __future__ import annotations

from pathlib import Path
import shutil

import pandas as pd
from typer.testing import CliRunner

from cpdshadow.cli import app
from cpdshadow.storage.parquet_io import read_parquet_dataset


class FakeDBNStore:
    def __init__(self, df: pd.DataFrame) -> None:
        self._df = df

    def to_parquet(self, path: Path, **_: object) -> None:
        self._df.to_parquet(path, index=False)


class FakeDatabentoClient:
    def __init__(self, definitions: pd.DataFrame, statistics: pd.DataFrame, ohlcv: pd.DataFrame) -> None:
        self._frames = {
            "definition": definitions,
            "statistics": statistics,
            "ohlcv-1d": ohlcv,
        }

    def estimate(self, request) -> None:
        return None

    def get_range(self, request):
        return FakeDBNStore(self._frames[request.schema].copy())


def _copy_repo_config(tmp_path: Path) -> None:
    shutil.copytree(Path("config"), tmp_path / "config")
    (tmp_path / "data" / "raw").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "curated").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "meta").mkdir(parents=True, exist_ok=True)
    (tmp_path / "artifacts" / "wp4").mkdir(parents=True, exist_ok=True)


def test_wp4_offline_cli_flow(tmp_path: Path, monkeypatch) -> None:
    _copy_repo_config(tmp_path)
    definitions = pd.DataFrame([
        {
            "raw_symbol": "ESM4",
            "instrument_id": 1,
            "instrument_class": "F",
            "security_type": "FUT",
            "asset": "ES",
            "exchange": "CME",
            "currency": "USD",
            "expiration": "2024-06-21T13:30:00Z",
            "activation": "2023-09-15T00:00:00Z",
            "contract_multiplier": 50,
            "min_price_increment": 0.25,
            "ts_event": "2024-01-02T00:00:00Z",
            "ts_recv": "2024-01-02T00:00:01Z",
        },
        {
            "raw_symbol": "NQM4",
            "instrument_id": 2,
            "instrument_class": "F",
            "security_type": "FUT",
            "asset": "NQ",
            "exchange": "CME",
            "currency": "USD",
            "expiration": "2024-06-21T13:30:00Z",
            "activation": "2023-09-15T00:00:00Z",
            "contract_multiplier": 20,
            "min_price_increment": 0.25,
            "ts_event": "2024-01-02T00:00:00Z",
            "ts_recv": "2024-01-02T00:00:01Z",
        },
    ])
    statistics = pd.DataFrame([
        {
            "raw_symbol": "ESM4",
            "instrument_id": 1,
            "ts_event": "2024-01-03T23:00:00Z",
            "ts_recv": "2024-01-03T23:00:01Z",
            "ts_ref": "2024-01-02T00:00:00Z",
            "stat_type": 3,
            "price": 5001.0,
            "quantity": None,
            "sequence": 1,
            "is_final": True,
        },
        {
            "raw_symbol": "NQM4",
            "instrument_id": 2,
            "ts_event": "2024-01-03T23:00:00Z",
            "ts_recv": "2024-01-03T23:00:01Z",
            "ts_ref": "2024-01-02T00:00:00Z",
            "stat_type": 3,
            "price": 17001.0,
            "quantity": None,
            "sequence": 1,
            "is_final": True,
        },
    ])
    ohlcv = pd.DataFrame([
        {
            "raw_symbol": "ESM4",
            "instrument_id": 1,
            "ts_event": "2024-01-02T23:59:59Z",
            "open": 4990.0,
            "high": 5010.0,
            "low": 4980.0,
            "close": 4995.0,
            "volume": 10000,
        },
        {
            "raw_symbol": "NQM4",
            "instrument_id": 2,
            "ts_event": "2024-01-02T23:59:59Z",
            "open": 16990.0,
            "high": 17010.0,
            "low": 16980.0,
            "close": 16995.0,
            "volume": 8000,
        },
    ])
    fake_client = FakeDatabentoClient(definitions, statistics, ohlcv)
    monkeypatch.setenv("DATABENTO_API_KEY", "db-test")
    monkeypatch.setattr("cpdshadow.cli._make_databento_client", lambda repo_root: fake_client)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "ingest",
            "databento",
            "smoke",
            "--start",
            "2024-01-02",
            "--end",
            "2024-01-05",
            "--roots",
            "ES,NQ",
            "--schemas",
            "definition,statistics,ohlcv-1d",
            "--max-cost-usd",
            "1.0",
            "--execute",
            "--skip-preflight",
            "--repo-root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.stdout

    raw_files = list((tmp_path / "data" / "raw" / "databento").rglob("*.parquet"))
    assert raw_files
    file_registry = read_parquet_dataset(tmp_path / "data" / "meta" / "data_file_registry")
    assert not file_registry.empty
    snapshots = sorted(set(file_registry["snapshot_id"]))
    snapshot_id = snapshots[-1]

    contract_master = read_parquet_dataset(tmp_path / "data" / "curated" / "contract_master" / f"snapshot_id={snapshot_id}")
    contracts_daily = read_parquet_dataset(tmp_path / "data" / "curated" / "contracts_daily" / f"snapshot_id={snapshot_id}")
    assert not contract_master.empty
    assert not contracts_daily.empty
    assert not contracts_daily.duplicated(subset=["trade_date", "root", "raw_symbol"]).any()

    rerun = runner.invoke(
        app,
        [
            "ingest",
            "databento",
            "smoke",
            "--start",
            "2024-01-02",
            "--end",
            "2024-01-05",
            "--roots",
            "ES,NQ",
            "--schemas",
            "definition,statistics,ohlcv-1d",
            "--max-cost-usd",
            "1.0",
            "--execute",
            "--skip-preflight",
            "--repo-root",
            str(tmp_path),
        ],
    )
    assert rerun.exit_code == 0, rerun.stdout
    file_registry_rerun = read_parquet_dataset(tmp_path / "data" / "meta" / "data_file_registry")
    assert file_registry_rerun["cached"].fillna(False).any()
