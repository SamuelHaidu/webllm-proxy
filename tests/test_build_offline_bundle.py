"""Pure-logic checks for scripts/build_offline_bundle.py's cross-platform
wheel targeting (no network, no subprocess) -- catches typos in the pip
platform-tag / abi construction without needing a real `pip download` run."""

import sys

import pytest

from scripts.build_offline_bundle import (
    _PIP_PLATFORM_TAGS,
    _UV_PYTHON_PLATFORM,
    _parse_args,
    _pip_download_platform_args,
)


def test_pip_download_platform_args_windows():
    args = _pip_download_platform_args("windows-x64", "3.12")
    assert args == [
        "--platform",
        "win_amd64",
        "--python-version",
        "3.12",
        "--implementation",
        "cp",
        "--abi",
        "cp312",
        "--only-binary=:all:",
    ]


def test_pip_download_platform_args_linux_lists_every_manylinux_generation():
    args = _pip_download_platform_args("linux-x64", "3.13")
    platform_values = [args[i + 1] for i, v in enumerate(args) if v == "--platform"]
    assert platform_values == _PIP_PLATFORM_TAGS["linux-x64"]
    assert args[args.index("--abi") + 1] == "cp313"


def test_every_pip_target_has_a_matching_uv_python_platform():
    # `_download_dependency_wheels` looks both dicts up by the same `--target`
    # value; a target present in one but not the other would only surface as
    # a KeyError at bundle-build time, not at argparse time.
    assert set(_PIP_PLATFORM_TAGS) == set(_UV_PYTHON_PLATFORM)


def test_target_defaults_to_host_native():
    args = _parse_args([])
    assert args.target is None


def test_target_rejects_unsupported_platform():
    with pytest.raises(SystemExit):
        _parse_args(["--target", "darwin-arm64"])


def test_python_version_defaults_to_running_interpreter():
    args = _parse_args([])
    assert args.python_version == f"{sys.version_info.major}.{sys.version_info.minor}"
