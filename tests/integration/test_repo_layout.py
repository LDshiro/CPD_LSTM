from pathlib import Path



def test_expected_paths_exist() -> None:
    expected = [
        Path("docs/spec_v1.md"),
        Path("docs/repo_conventions.md"),
        Path(".codex/config.toml"),
        Path("AGENTS.md"),
        Path("scripts/setup.sh"),
        Path("scripts/check_env.sh"),
    ]
    assert all(path.exists() for path in expected)
