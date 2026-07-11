#!/usr/bin/env python3
"""Build a self-contained offline install bundle, for a corporate/air-gapped
machine where a TLS-inspecting proxy (e.g. Netskope) or a network policy
blocks PyPI and/or the CloakBrowser binary download. Run on a machine WITH
internet access:

    uv run poe bundle

Produces `dist/offline/`:
  wheels/                  this package's own wheel + every dependency wheel,
                            for `pip install --no-index --find-links wheels webllm-proxy`
  cloakbrowser-*.tar.gz     the stealth browser binary (only if already
                            installed locally -- run `webllm-proxy install` first)
  install_offline.sh / .ps1  do the above on the target machine

See README.md's "Corporate / air-gapped install" section.
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dist" / "offline"
WHEELS = OUT / "wheels"

_INSTALL_SH = """#!/usr/bin/env bash
# Offline install for webllm-proxy. Run from inside this dist/offline/ directory.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 -m pip install --no-index --find-links "$HERE/wheels" webllm-proxy
ARCHIVE=$(ls "$HERE"/cloakbrowser-*.tar.gz 2>/dev/null | head -n1 || true)
if [ -n "$ARCHIVE" ]; then
  DEST="$HOME/.cloakbrowser"
  mkdir -p "$DEST"
  tar -xzf "$ARCHIVE" -C "$DEST"
  echo "CloakBrowser binary extracted to $DEST"
else
  echo "No CloakBrowser binary archive in this bundle -- set CLOAKBROWSER_BINARY_PATH"
  echo "to a pre-staged binary, or CLOAKBROWSER_DOWNLOAD_URL to an internal mirror."
fi
echo "Done. Try: webllm-proxy --version"
"""

_INSTALL_PS1 = """# Offline install for webllm-proxy. Run from inside this dist/offline/ directory.
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
python -m pip install --no-index --find-links "$Here\\wheels" webllm-proxy
$Archive = Get-ChildItem -Path $Here -Filter "cloakbrowser-*.tar.gz" | Select-Object -First 1
if ($Archive) {
    $Dest = Join-Path $HOME ".cloakbrowser"
    New-Item -ItemType Directory -Force -Path $Dest | Out-Null
    tar -xzf $Archive.FullName -C $Dest
    Write-Host "CloakBrowser binary extracted to $Dest"
} else {
    Write-Host "No CloakBrowser binary archive in this bundle -- set CLOAKBROWSER_BINARY_PATH"
    Write-Host "to a pre-staged binary, or CLOAKBROWSER_DOWNLOAD_URL to an internal mirror."
}
Write-Host "Done. Try: webllm-proxy --version"
"""


def _run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=ROOT)


def _build_package_wheel() -> None:
    _run(["uv", "build", "--wheel", "--out-dir", str(WHEELS)])


def _download_dependency_wheels() -> None:
    """Every runtime dependency (not dev tools), as wheels -- `uv` itself has
    no `pip download` equivalent, so export the resolved lockfile as
    requirements and hand it to `pip download` (ephemeral via `uv run --with
    pip`, so this works even though the project's own venv has no pip).

    `--no-emit-project`: without it, `uv export` includes this package itself
    (as a local path requirement) alongside its real dependencies; `pip
    download` then tries to build/archive it into `wheels/` too, colliding
    with the wheel `_build_package_wheel` already placed there a moment
    earlier -- pip's conflict prompt (`(i)gnore/(w)ipe/...`) hangs
    non-interactively. Found by actually running this script, not by reading
    `uv export --help`."""
    requirements = OUT / "requirements.txt"
    _run(
        [
            "uv",
            "export",
            "--format",
            "requirements.txt",
            "--no-dev",
            "--no-hashes",
            "--no-emit-project",
            "--output-file",
            str(requirements),
        ]
    )
    _run(
        [
            "uv",
            "run",
            "--with",
            "pip",
            "python",
            "-m",
            "pip",
            "download",
            "-r",
            str(requirements),
            "-d",
            str(WHEELS),
        ]
    )


def _bundle_cloakbrowser_binary() -> None:
    """Copy the already-installed CloakBrowser binary archive into the
    bundle, if present (`webllm-proxy install` fetches it); print guidance if
    it isn't, rather than failing the whole bundle build over it."""
    try:
        from cloakbrowser import binary_info
    except ImportError:
        print("cloakbrowser not importable here; skipping binary bundling.")
        return
    info = binary_info()
    cache_dir = Path(info.get("cache_dir") or "")
    if not info.get("installed") or not cache_dir.is_dir():
        print(
            "CloakBrowser binary isn't installed locally yet -- run "
            "`uv run webllm-proxy install` first, then re-run this script, "
            "to include it in the bundle."
        )
        return
    # Archive name is always `cloakbrowser-<real-dir-name>.tar.gz`, NOT just
    # `<real-dir-name>.tar.gz` -- the real cache dir is named after the
    # bundled Chromium build (e.g. `chromium-146.0.7680.177.5`), which does
    # not match the `cloakbrowser-*.tar.gz` glob the install scripts search
    # for below. Found by actually listing the bundle output, not by
    # inspection alone.
    archive_base = OUT / f"cloakbrowser-{cache_dir.name}"
    print(f"+ archiving {cache_dir} -> {archive_base}.tar.gz")
    shutil.make_archive(
        str(archive_base), "gztar", root_dir=cache_dir.parent, base_dir=cache_dir.name
    )


def _write_install_scripts() -> None:
    sh = OUT / "install_offline.sh"
    sh.write_text(_INSTALL_SH)
    sh.chmod(0o755)
    (OUT / "install_offline.ps1").write_text(_INSTALL_PS1)


def main() -> int:
    # Wipe any previous bundle rather than merging into it -- a stale wheel
    # left over from a prior run is exactly what caused the pip-download
    # collision this script now avoids (see _download_dependency_wheels).
    shutil.rmtree(OUT, ignore_errors=True)
    WHEELS.mkdir(parents=True, exist_ok=True)
    _build_package_wheel()
    _download_dependency_wheels()
    _bundle_cloakbrowser_binary()
    _write_install_scripts()
    print(f"\nOffline bundle ready: {OUT}")
    print("Copy this whole directory to the target machine and run install_offline.sh / .ps1.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
