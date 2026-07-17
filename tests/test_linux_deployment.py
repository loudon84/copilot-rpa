from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYSTEMD_UNIT = ROOT / "deploy" / "systemd" / "nodeskclaw-rpa-engine.service"
LINUX_GUIDE = ROOT / "docs" / "LINUX_DEPLOYMENT.md"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def test_systemd_unit_uses_one_non_root_engine_process() -> None:
    unit = SYSTEMD_UNIT.read_text(encoding="utf-8")

    assert "User=nodeskclaw-rpa" in unit
    assert "Group=nodeskclaw-rpa" in unit
    assert "WorkingDirectory=/opt/nodeskclaw-rpa-engine" in unit
    assert "EnvironmentFile=/etc/nodeskclaw-rpa-engine/engine.env" in unit
    assert unit.count("ExecStart=") == 1
    assert "-m nodeskclaw_rpa_engine" in unit
    assert "--workers" not in unit
    assert "Restart=on-failure" in unit
    assert "KillSignal=SIGTERM" in unit
    assert "TimeoutStopSec=60s" in unit
    assert "UMask=0077" in unit


def test_linux_guide_documents_runtime_and_browser_boundaries() -> None:
    guide = LINUX_GUIDE.read_text(encoding="utf-8")

    for expected in (
        ".venv/bin/python",
        "RUNTIME_CACHE_DIR=/var/lib/nodeskclaw-rpa-engine/flows",
        "RUNTIME_WORK_DIR=/var/lib/nodeskclaw-rpa-engine/runs",
        "PLAYWRIGHT_BROWSERS_PATH=/var/lib/nodeskclaw-rpa-engine/ms-playwright",
        '"channel": "chromium"',
        '"headless": true',
        "WORKER_LEASE_ENABLED=false",
        "RUNTIME_ENABLED=true",
        "CREDENTIAL_RESOLVER_MODE=mock_env",
    ):
        assert expected in guide
    assert guide.count("```") % 2 == 0
    assert "192.168." not in guide


def test_ubuntu_ci_runs_real_chromium_smoke() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "runs-on: ubuntu-latest" in workflow
    assert "python -m playwright install-deps chromium" in workflow
    assert "python -m playwright install chromium" in workflow
    assert "python scripts/run_phase5_demo.py" in workflow
    assert "--start-mock-srm" in workflow
    assert "--channel chromium" in workflow
