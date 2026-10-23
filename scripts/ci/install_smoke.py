#!/usr/bin/env python3
"""Check an installed CLI and its product artifacts without using an operator's home.

Run with the INSTALL'S Python, after the installer path stage and product builds:
  venv/bin/python scripts/ci/install_smoke.py --root . --bin-dir "$HOME/.local/bin"
No installs, repairs, lock cleanup, or provider calls are performed here.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata as metadata
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
from html.parser import HTMLParser


class AssetLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        key = {"script": "src", "link": "href"}.get(tag)
        if key and (value := attrs.get(key)) and value.startswith("/assets/"):
            self.paths.append(value.lstrip("/"))


def check_artifacts(root: Path) -> None:
    """Require the real output locations, including assets referenced by the HTML."""
    dist = root / "hermes_cli" / "web_dist"
    html = dist / "index.html"
    parser = AssetLinks()
    parser.feed(html.read_text(encoding="utf-8"))
    if not parser.paths:
        raise RuntimeError("dashboard HTML has no built asset references")
    for path in [root / "ui-tui" / "dist" / "entry.js", html,
                 *(dist / name for name in parser.paths)]:
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"missing or empty build artifact: {path.relative_to(root)}")


def check_dependencies(root: Path) -> None:
    from packaging.requirements import Requirement

    if Path(sys.prefix).resolve() not in (root / "venv", root / ".venv"):
        raise RuntimeError("run the smoke check with this installation's venv Python")
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    # Inspect metadata with the target interpreter, rather than trusting an install exit code.
    for spec in project["dependencies"]:
        req = Requirement(spec)
        if req.marker is not None and not req.marker.evaluate({"extra": ""}):
            continue
        if not req.specifier.contains(metadata.version(req.name), prereleases=True):
            raise RuntimeError(f"installed dependency does not satisfy {req.name}")
    for name in ("hermes_cli", "openai", "yaml", "pydantic_core", "psutil"):
        module = importlib.import_module(name)
        if name == "hermes_cli" and Path(module.__file__).resolve().parent != root / "hermes_cli":
            raise RuntimeError("hermes_cli was imported from another checkout")


def check_launchers(bin_dir: Path) -> None:
    # An allowlist prevents credentials and another checkout's Python settings reaching startup.
    with tempfile.TemporaryDirectory(prefix="hermes-install-smoke-") as directory:
        home = Path(directory)
        env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR")
               if key in os.environ}
        env.update(HOME=directory, USERPROFILE=directory, LOCALAPPDATA=directory,
                   APPDATA=directory, TEMP=directory, TMP=directory,
                   HERMES_HOME=str(home / ".hermes"), PYTHONUTF8="1")
        suffix = ".cmd" if os.name == "nt" else ""
        for name, args in (("hermes", ["--version"]), ("hermes", ["--help"]),
                           ("hermes-agent", ["--help"]), ("hermes-acp", ["--help"])):
            launcher = bin_dir / (name + suffix)
            result = subprocess.run([str(launcher), *args], cwd=home, env=env,
                                    capture_output=True, text=True, timeout=60)
            # argparse uses stdout; Fire's successful --help uses stderr.
            if result.returncode or not (result.stdout + result.stderr).strip():
                # Do not echo arbitrary child output into CI logs.
                raise RuntimeError(f"{name} {' '.join(args)} failed (exit {result.returncode})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--bin-dir", required=True, type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    check_dependencies(root)
    print("PASS: declared core dependencies and native imports")
    check_artifacts(root)
    print("PASS: TUI bundle, dashboard HTML, referenced assets")
    check_launchers(args.bin_dir.resolve())
    print("PASS: public launchers from an unrelated directory and isolated home")


if __name__ == "__main__":
    main()
