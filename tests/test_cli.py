"""Tests for SavageOps CLI commands."""

import subprocess
import sys


def test_cli_help():
    res = subprocess.run(
        [sys.executable, "-m", "savageops.cli", "--help"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    assert "SavageOps" in res.stdout
    assert "doctor" in res.stdout
    assert "simulate" in res.stdout
    assert "init" in res.stdout


def test_cli_init(tmp_path):
    # Run init in a temp directory
    res = subprocess.run(
        [sys.executable, "-m", "savageops.cli", "init"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    config_dir = tmp_path / "savageops-config"
    assert config_dir.exists()
    assert (config_dir / ".env.example").exists()
    assert (config_dir / "docker-compose.yml").exists()
    assert (config_dir / "grafana").exists()
