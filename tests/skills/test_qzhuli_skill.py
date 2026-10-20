"""Contract tests for the bundled qzhuli skill and its qz launcher.

Offline only: fakes the qz executable on PATH / in a local node_modules/.bin and
exercises the launcher's resolution chain; never reaches the network or npx.
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

import hermes_yaml as yaml

REPO = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO / "skills" / "productivity" / "qzhuli"
LAUNCHER = SKILL_DIR / "scripts" / "qz"
SKILL_MD = SKILL_DIR / "SKILL.md"

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="POSIX sh launcher; skill is linux/macos gated"
)


def _fake_executable(target: Path, record: Path) -> Path:
    """An executable fake that records its argv to *record* and exits 0."""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        f"#!/bin/sh\nprintf '%s\\n' \"$@\" > '{record}'\n",
        encoding="utf-8",
    )
    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return target


def _run_launcher(cwd: Path, path: str, args: list[str]) -> subprocess.CompletedProcess:
    env = {**os.environ, "PATH": path, "QZ_BIN": ""}
    return subprocess.run(
        ["/bin/sh", str(LAUNCHER), *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_launcher_passes_through_path_qz(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    record = tmp_path / "argv.txt"
    _fake_executable(bin_dir / "qz", record)
    work = tmp_path / "work"
    work.mkdir()

    result = _run_launcher(work, str(bin_dir), ["auth", "status"])

    assert result.returncode == 0, result.stderr
    assert record.read_text(encoding="utf-8").split() == ["auth", "status"]


def test_launcher_falls_back_to_local_node_bin(tmp_path: Path) -> None:
    # No qz on PATH; a checkout-local node_modules/.bin/qz wins over npx.
    empty_bin = tmp_path / "empty"
    empty_bin.mkdir()
    work = tmp_path / "checkout"
    record = work / "argv.txt"
    _fake_executable(work / "node_modules" / ".bin" / "qz", record)

    result = _run_launcher(work, str(empty_bin), ["--version"])

    assert result.returncode == 0, result.stderr
    assert record.read_text(encoding="utf-8").split() == ["--version"]


def test_launcher_static_contract() -> None:
    text = LAUNCHER.read_text(encoding="utf-8")
    # Pinned npx fallback, never a global install.
    assert "@qzhuli/qzhuli-cli@0.5.12" in text
    assert "install -g" not in text
    assert LAUNCHER.stat().st_mode & stat.S_IXUSR


def test_skill_frontmatter_contract() -> None:
    content = SKILL_MD.read_text(encoding="utf-8")
    fm = yaml.safe_load(content.split("---", 2)[1])
    assert fm["name"] == SKILL_DIR.name
    assert len(fm["description"]) <= 60
    assert fm["description"].rstrip().endswith(".")
    for field in ("version", "author", "license", "platforms"):
        assert field in fm
    assert fm["platforms"] == ["linux", "macos"]
