from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
import hashlib
import json
import uuid
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        default=_json_default,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def stable_sha256_hex(value: Any) -> str:
    if isinstance(value, bytes):
        payload = value
    elif isinstance(value, str):
        payload = value.encode("utf-8")
    else:
        payload = canonical_json_bytes(value)
    return hashlib.sha256(payload).hexdigest()


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def request_params_hash(value: Any) -> str:
    return stable_sha256_hex(value)


def config_hash(paths: list[str | Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(Path(item) for item in paths):
        digest.update(path.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(Path(path).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def manifest_hash(value: Any) -> str:
    return stable_sha256_hex(value)


def make_run_id() -> str:
    return f"run_{_time_token()}_{uuid.uuid4().hex[:12]}"


def make_snapshot_id(run_id: str) -> str:
    suffix = stable_sha256_hex(run_id)[:12]
    return f"snapshot_{_time_token()}_{suffix}"


def make_file_id(snapshot_id: str, logical_table: str, path: str | Path) -> str:
    suffix = stable_sha256_hex({"snapshot_id": snapshot_id, "logical_table": logical_table, "path": str(path)})[:16]
    return f"file_{suffix}"


def _time_token() -> str:
    return utc_now().strftime("%Y%m%dT%H%M%S%fZ")


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, set):
        return sorted(value)
    if isinstance(value, tuple):
        return list(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "__dict__"):
        return value.__dict__
    raise TypeError(f"Object of type {type(value)!r} is not JSON serializable")
