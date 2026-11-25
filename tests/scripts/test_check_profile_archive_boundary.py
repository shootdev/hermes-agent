"""Behavioral tests for the profile archive CI guard."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "ci"
    / "check_profile_archive_boundary.py"
)


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_clean_root_passes(tmp_path):
    result = _run(tmp_path)

    assert result.returncode == 0
    assert "No profile export archives" in result.stdout


def test_root_profile_archive_fails_without_printing_contents(tmp_path):
    default_archive = tmp_path / "default.tar.gz"
    alternate_archive = tmp_path / "backup.TGZ"
    default_archive.write_bytes(b"profile webhook secret must never be printed")
    alternate_archive.write_bytes(b"another archive")

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "default.tar.gz" in result.stdout
    assert "backup.TGZ" in result.stdout
    assert "profile webhook secret" not in result.stdout
    assert "another archive" not in result.stdout


def test_nested_profile_archive_is_also_rejected(tmp_path):
    nested = tmp_path / "fixtures"
    nested.mkdir()
    (nested / "fixture.tar.gz").write_bytes(b"test fixture")

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "fixtures/fixture.tar.gz" in result.stdout


def test_local_build_cache_archives_are_skipped(tmp_path):
    """Tool chains download third-party archives into .build/ and .cache/; those are
    build inputs, not profile exports, and .dockerignore keeps both trees out of every
    build context."""
    for cache in (".build", ".cache"):
        toolchain = tmp_path / cache / "desktop-inputs" / "packager"
        toolchain.mkdir(parents=True)
        (toolchain / "7zip-darwin-arm64.tar.gz").write_bytes(b"third-party toolchain")
    real_export = tmp_path / ".build-adjacent.tar.gz"
    real_export.write_bytes(b"a profile export outside the caches")

    result = _run(tmp_path)

    assert result.returncode == 1
    assert ".build-adjacent.tar.gz" in result.stdout
    assert "7zip-darwin-arm64.tar.gz" not in result.stdout


def test_profile_archive_inside_the_build_caches_is_skipped(tmp_path):
    """The caches are local build outputs: a file placed there is not published."""
    for cache in (".build", ".cache"):
        cache_dir = tmp_path / cache
        cache_dir.mkdir()
        (cache_dir / "profile-export.tar.gz").write_bytes(b"inside a local cache")

    result = _run(tmp_path)

    assert result.returncode == 0
    assert "No profile export archives" in result.stdout
