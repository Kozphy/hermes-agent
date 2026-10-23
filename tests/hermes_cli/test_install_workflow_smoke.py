"""Real launcher and build-watchdog probes; no downloads or operator state."""

import errno
import os
from pathlib import Path
import subprocess
import sys
import venv

import pytest

from hermes_cli.main_web_build import _run_with_idle_timeout
from scripts.ci.install_smoke import check_artifacts


@pytest.mark.linux_only
def test_installer_launcher_rerun_and_symlink_depth(tmp_path):
    root = Path(__file__).resolve().parents[2]
    install = tmp_path / "install with spaces"
    venv.create(install / "venv", with_pip=False)
    # Record argv/env using the real venv interpreter and installer-generated shims.
    entry = "import os, sys; print(repr(sys.argv[1:])); print(os.getenv('PYTHONPATH', 'clean'))\n"
    (install / "hermes").write_text(entry)
    (install / "run_agent.py").write_text(entry)
    bin_dir = tmp_path / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    old_console = install / "venv" / "bin" / "hermes"
    old_console.write_text("preserve console entrypoint\n")
    (bin_dir / "hermes").symlink_to(old_console)
    env = {"HOME": str(tmp_path), "PATH": os.environ["PATH"],
           "HERMES_HOME": str(tmp_path / ".hermes")}
    for _ in range(2):
        subprocess.run(["bash", str(root / "scripts/install.sh"), "--stage", "path",
                        "--dir", str(install), "--non-interactive"],
                       env=env, cwd=tmp_path, check=True, capture_output=True, timeout=30)
    assert old_console.read_text() == "preserve console entrypoint\n"
    for name, prefix in (("hermes", []), ("hermes-agent", []), ("hermes-acp", ["acp"])):
        result = subprocess.run([str(bin_dir / name), "two words", "--help"],
                                env={**env, "PYTHONPATH": "wrong-checkout", "PYTHONHOME": "wrong-home"},
                                cwd=tmp_path, capture_output=True, text=True, timeout=10, check=True)
        assert repr([*prefix, "two words", "--help"]) in result.stdout
        assert "clean" in result.stdout

    # Isolate Linux path lookup depth from recursion in Hermes or npm itself.
    target = install / "venv" / "bin" / "python"
    shallow = target
    for number in range(45):
        link = tmp_path / f"python-link-{number}"
        link.symlink_to(target)
        target = link
    with pytest.raises(OSError) as error:
        target.stat()
    assert error.value.errno == errno.ELOOP
    assert subprocess.run([str(shallow), "-V"], capture_output=True).returncode == 0


@pytest.mark.parametrize("silent", [True, False], ids=["silent-build", "progressing-build"])
def test_real_build_watchdog_distinguishes_idle_from_total_time(tmp_path, silent):
    # Both run longer than the short idle budget. Real output keeps only one alive.
    code = ("import time; time.sleep(30)" if silent else
            "import time\nfor _ in range(14):\n print('building', flush=True)\n time.sleep(0.5)")
    result = _run_with_idle_timeout([sys.executable, "-u", "-c", code], tmp_path,
                                    idle_timeout_seconds=3)
    if silent:
        assert result.returncode != 0
        assert "no output for 3s" in result.stdout
    else:
        assert result.returncode == 0
        assert result.stdout.count("building") == 14


@pytest.mark.parametrize("broken", ["bundle", "asset", "empty-asset", "html"])
def test_smoke_rejects_incomplete_build_outputs(tmp_path, broken):
    bundle = tmp_path / "ui-tui/dist/entry.js"
    html = tmp_path / "hermes_cli/web_dist/index.html"
    asset = html.parent / "assets/app.js"
    for path in (bundle, html, asset):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("built output")
    html.write_text('<script src="/assets/app.js"></script>')
    check_artifacts(tmp_path)
    if broken == "empty-asset":
        asset.write_text("")
    else:
        {"bundle": bundle, "asset": asset, "html": html}[broken].unlink()
    with pytest.raises((OSError, RuntimeError)):
        check_artifacts(tmp_path)
