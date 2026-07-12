#!/usr/bin/env python3
"""Build a self-contained offline install bundle, for a corporate/air-gapped
machine where a TLS-inspecting proxy (e.g. Netskope) or a network policy
blocks PyPI and/or the CloakBrowser binary download. Run on a machine WITH
internet access:

    uv run poe bundle                    # this machine's own OS (unchanged)
    uv run poe bundle-linux              # cross-build Linux wheels from any OS
    uv run poe bundle-windows            # cross-build Windows wheels from any OS

Produces `dist/offline/` (or `dist/offline/<target>/` when `--target` is
given):
  wheels/                  this package's own wheel + every dependency wheel,
                            for `pip install --no-index --find-links wheels webllm-proxy`
  cloakbrowser-*.tar.gz     the stealth browser binary -- only bundled when
                            building for THIS machine's own OS (see below)
  install_offline.sh / .ps1  do the above on the target machine

Cross-building wheels for another OS from this one works (verified: PyPI
ships prebuilt wheels for every runtime dependency on both linux-x64 and
windows-x64, so nothing needs a foreign-arch compiler) via `uv pip compile
--python-platform` + `pip download --platform ... --only-binary=:all:`; see
`_download_dependency_wheels`.

The CloakBrowser binary is the one piece that CANNOT be cross-built this way:
its download is authenticated with a signed manifest checked against the
*running* OS (`cloakbrowser.config.get_platform_tag()`), with no supported
override, so faking another platform's tag would mean reimplementing that
verification ourselves against a guess -- not done here. `--target` on a
foreign OS therefore skips the binary and prints what to do instead; see
`.github/workflows/offline-bundle.yml` for how CI gets a *complete* bundle
(incl. the binary) for both OSes anyway -- a matrix with one native runner
per OS, each running this same script with no `--target` at all.

See README.md's "Corporate / air-gapped install" section.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Wheel-tag / resolver-triple mappings for each cross-buildable target, named
# after cloakbrowser's own platform tags (kept consistent -- see
# cloakbrowser.config.SUPPORTED_PLATFORMS). Only linux-x64/windows-x64 are
# wired up (what was asked for); a `--target` outside this map is rejected by
# argparse rather than silently mishandled.
#
# Linux has no single wheel platform tag -- PyPI only accepts manylinux/
# musllinux-tagged wheels, and packages migrate to newer manylinux baselines
# over time, so several generations are listed (one `--platform` per pip
# invocation, pip accepts a wheel matching ANY of them). Verified against this
# project's actual locked dependencies (not guessed): every one of them
# resolved to a wheel under this list, for both targets, with no sdist
# fallback needed.
_PIP_PLATFORM_TAGS: dict[str, list[str]] = {
    "linux-x64": [
        "manylinux2014_x86_64",
        "manylinux_2_17_x86_64",
        "manylinux_2_28_x86_64",
        "manylinux_2_34_x86_64",
    ],
    "windows-x64": ["win_amd64"],
}
_UV_PYTHON_PLATFORM: dict[str, str] = {
    "linux-x64": "x86_64-unknown-linux-gnu",
    "windows-x64": "x86_64-pc-windows-msvc",
}

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


def _pip_download_platform_args(target: str, python_version: str) -> list[str]:
    """`pip download` flags that fetch wheels for `target`/`python_version`
    without needing that OS locally: one `--platform` per compatible wheel
    tag, plus the interpreter facts pip otherwise infers from the host.
    `--only-binary=:all:` is load-bearing -- without it, a package with no
    prebuilt wheel for `target` would have pip try to build its sdist with
    THIS host's toolchain for the TARGET platform, which cannot work; with
    it, that case fails loudly instead of silently producing a broken wheel."""
    args = []
    for tag in _PIP_PLATFORM_TAGS[target]:
        args += ["--platform", tag]
    abi = "cp" + python_version.replace(".", "")
    args += [
        "--python-version",
        python_version,
        "--implementation",
        "cp",
        "--abi",
        abi,
        "--only-binary=:all:",
    ]
    return args


def _build_package_wheel(wheels_dir: Path) -> None:
    _run(["uv", "build", "--wheel", "--out-dir", str(wheels_dir)])


def _download_dependency_wheels(
    out_dir: Path, wheels_dir: Path, target: str | None, python_version: str
) -> None:
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
    requirements = out_dir / "requirements.txt"
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

    if target is None:
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
                str(wheels_dir),
            ]
        )
        return

    # Cross-target: re-resolve against `requirements` as constraints (pins
    # never drift from what's actually locked) but let uv evaluate
    # environment markers (sys_platform, python_full_version, ...) for
    # `target` instead of this host. Plain `pip download --platform ...`
    # does NOT do this -- it evaluates a requirements file's markers against
    # the HOST environment regardless of --platform, silently dropping
    # target-only deps (e.g. `colorama ; sys_platform == "win32"` vanishes
    # when cross-building the Windows bundle from Linux). Verified by
    # actually running it and watching colorama get skipped, not assumed.
    resolved = out_dir / f"requirements-{target}.txt"
    _run(
        [
            "uv",
            "pip",
            "compile",
            str(ROOT / "pyproject.toml"),
            "--python-platform",
            _UV_PYTHON_PLATFORM[target],
            "--python-version",
            python_version,
            "--constraints",
            str(requirements),
            "--output-file",
            str(resolved),
            "--no-header",
            "--no-annotate",
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
            str(resolved),
            "-d",
            str(wheels_dir),
            *_pip_download_platform_args(target, python_version),
        ]
    )


def _host_platform_tag() -> str | None:
    """This machine's own CloakBrowser platform tag (e.g. 'linux-x64'), or
    None if cloakbrowser isn't importable or the platform is unrecognized."""
    try:
        from cloakbrowser.config import get_platform_tag

        return get_platform_tag()
    except Exception:
        return None


def _bundle_cloakbrowser_binary(out_dir: Path, target: str | None) -> None:
    """Copy the already-installed CloakBrowser binary archive into the
    bundle, if present (`webllm-proxy install` fetches it) AND `target` is
    this machine's own OS -- CloakBrowser has no supported way to fetch
    another platform's binary (its download is authenticated against the
    running OS, see the module docstring), so cross-building for a different
    `target` skips this step with guidance rather than failing the whole
    bundle build over it."""
    if target is not None and target != _host_platform_tag():
        print(
            f"Cross-building for {target} on a different host -- CloakBrowser has no "
            "supported cross-platform binary download (its download is authenticated "
            "against the running OS). Skipping the browser binary here; either merge in "
            f"an archive from running this script's binary step natively on a {target} "
            "machine/CI runner (see .github/workflows/offline-bundle.yml), or rely on "
            "CLOAKBROWSER_DOWNLOAD_URL / CLOAKBROWSER_BINARY_PATH on the target instead."
        )
        return
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
    archive_base = out_dir / f"cloakbrowser-{cache_dir.name}"
    print(f"+ archiving {cache_dir} -> {archive_base}.tar.gz")
    shutil.make_archive(
        str(archive_base), "gztar", root_dir=cache_dir.parent, base_dir=cache_dir.name
    )


def _write_install_scripts(out_dir: Path) -> None:
    sh = out_dir / "install_offline.sh"
    sh.write_text(_INSTALL_SH)
    sh.chmod(0o755)
    (out_dir / "install_offline.ps1").write_text(_INSTALL_PS1)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--target",
        choices=sorted(_PIP_PLATFORM_TAGS),
        default=None,
        help="cross-build the dependency wheels for this OS instead of the host's own "
        "(default: host-native, exactly today's single-machine bundle, incl. the "
        "CloakBrowser binary). Output goes to dist/offline/<target>/ so it never "
        "collides with a host-native build. The CloakBrowser binary itself is only "
        "included when --target matches this host (see module docstring).",
    )
    p.add_argument(
        "--python-version",
        default=f"{sys.version_info.major}.{sys.version_info.minor}",
        help="target Python major.minor for wheel selection (default: this "
        "interpreter's, %(default)s)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = ROOT / "dist" / "offline" / args.target if args.target else ROOT / "dist" / "offline"
    wheels_dir = out_dir / "wheels"
    # Wipe any previous bundle rather than merging into it -- a stale wheel
    # left over from a prior run is exactly what caused the pip-download
    # collision this script now avoids (see _download_dependency_wheels).
    shutil.rmtree(out_dir, ignore_errors=True)
    wheels_dir.mkdir(parents=True, exist_ok=True)
    _build_package_wheel(wheels_dir)
    _download_dependency_wheels(out_dir, wheels_dir, args.target, args.python_version)
    _bundle_cloakbrowser_binary(out_dir, args.target)
    _write_install_scripts(out_dir)
    print(f"\nOffline bundle ready: {out_dir}")
    print("Copy this whole directory to the target machine and run install_offline.sh / .ps1.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
