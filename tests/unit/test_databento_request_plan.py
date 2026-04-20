from datetime import date
from pathlib import Path

from cpdshadow.config import load_data_schema_yaml, load_databento_ingest_yaml
from cpdshadow.ingest.databento_raw import DatabentoIngestService, parent_symbols_from_roots
from cpdshadow.instruments import load_instrument_master


def _service() -> DatabentoIngestService:
    return DatabentoIngestService(
        repo_root=Path("."),
        ingest_config=load_databento_ingest_yaml("config/databento.ingest.yml"),
        data_schema=load_data_schema_yaml("config/data_schema.yml"),
        instrument_master=load_instrument_master("config/instruments.yml"),
        client=None,
    )


def test_parent_symbol_planning_from_roots() -> None:
    assert parent_symbols_from_roots(["NQ", "ES", "ES"]) == ["ES.FUT", "NQ.FUT"]


def test_plan_is_stable_for_same_inputs() -> None:
    service = _service()
    first = service.plan(
        start_date=date.fromisoformat("2024-01-01"),
        end_date=date.fromisoformat("2024-02-01"),
        roots=["ES", "NQ"],
        schemas=["definition", "statistics"],
    )
    second = service.plan(
        start_date=date.fromisoformat("2024-01-01"),
        end_date=date.fromisoformat("2024-02-01"),
        roots=["ES", "NQ"],
        schemas=["definition", "statistics"],
    )
    assert first.request_plan_hash == second.request_plan_hash
    assert [request.request_id for request in first.requests] == [request.request_id for request in second.requests]


def test_plan_uses_monthly_chunks_for_short_range() -> None:
    service = _service()
    plan = service.plan(
        start_date=date.fromisoformat("2024-01-01"),
        end_date=date.fromisoformat("2024-02-01"),
        roots=["ES", "NQ"],
        schemas=["definition"],
    )
    assert plan.chunk_mode == "month"
    assert plan.requests[0].output_path.endswith(".parquet")
    assert "schema=definition" in plan.requests[0].output_path
