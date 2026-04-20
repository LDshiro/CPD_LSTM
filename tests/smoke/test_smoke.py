from pathlib import Path



def test_smoke_dirs_exist() -> None:
    dirs = [
        Path("data/raw"),
        Path("data/staging"),
        Path("data/curated"),
        Path("data/features"),
        Path("data/backtests"),
        Path("data/shadow"),
        Path("data/meta"),
        Path("artifacts"),
        Path("logs"),
    ]
    assert all(d.exists() for d in dirs)
