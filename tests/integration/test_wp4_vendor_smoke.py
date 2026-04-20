from __future__ import annotations

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cpdshadow.cli import app


pytestmark = pytest.mark.skipif(
    os.getenv("CPDSHADOW_RUN_VENDOR_TESTS") != "1" or not os.getenv("DATABENTO_API_KEY"),
    reason="vendor smoke test disabled",
)


def test_wp4_vendor_smoke() -> None:
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
            "definition,statistics",
            "--max-cost-usd",
            "1.0",
            "--execute",
            "--repo-root",
            str(Path(".")),
        ],
    )
    assert result.exit_code == 0, result.stdout
