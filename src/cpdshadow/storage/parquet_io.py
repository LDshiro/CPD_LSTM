from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pandas as pd


def ensure_directory(path: str | Path) -> Path:
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    return target


def write_parquet_part(df: pd.DataFrame, path: str | Path, *, compression: str = "zstd") -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(target, compression=compression, index=False)
    return target


def write_parquet_dataset(
    df: pd.DataFrame,
    root: str | Path,
    *,
    compression: str = "zstd",
    partition_cols: list[str] | None = None,
) -> Path:
    target = Path(root)
    target.mkdir(parents=True, exist_ok=True)
    if partition_cols:
        df.to_parquet(target, compression=compression, index=False, partition_cols=partition_cols)
    else:
        df.to_parquet(target / "part-00000.parquet", compression=compression, index=False)
    return target


def read_parquet_dataset(path: str | Path) -> pd.DataFrame:
    target = Path(path)
    if not target.exists():
        return pd.DataFrame()
    parquet_files = list(target.rglob("*.parquet")) if target.is_dir() else [target]
    if not parquet_files:
        return pd.DataFrame()
    return pd.read_parquet(target)


def make_staging_dir(final_dir: str | Path) -> Path:
    final_path = Path(final_dir)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f"{final_path.name}__staging__", dir=final_path.parent))


def atomic_replace_dir(staging_dir: str | Path, final_dir: str | Path) -> Path:
    staging = Path(staging_dir)
    final = Path(final_dir)
    backup = final.with_name(f"{final.name}__backup__")
    if backup.exists():
        shutil.rmtree(backup)
    moved_existing_output = False
    try:
        if final.exists():
            os.replace(final, backup)
            moved_existing_output = True
        try:
            os.replace(staging, final)
        except PermissionError:
            # Windows can reject directory-to-directory os.replace() even when the
            # destination does not yet exist. Fall back to move within the same parent.
            shutil.move(str(staging), str(final))
    except Exception:
        if moved_existing_output and backup.exists() and not final.exists():
            shutil.move(str(backup), str(final))
        raise
    if backup.exists():
        shutil.rmtree(backup)
    return final
