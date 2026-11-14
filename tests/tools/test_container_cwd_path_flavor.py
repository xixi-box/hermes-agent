"""Regression for #155650: container paths use POSIX grammar on every host."""

import json
import ntpath
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("cwd", ["/factory", "/workspace"])
def test_profile_container_cwd_survives_windows_path_grammar(tmp_path, monkeypatch, cwd):
    from tools import terminal_tool as tt
    from tools import terminal_tool_config
    from tools.terminal_scope import install_and_reset_profile_terminal_scope

    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "config.yaml").write_text(json.dumps({"terminal": {
        "backend": "docker", "cwd": cwd,
        "container_persistent": True, "docker_mount_cwd_to_workspace": False,
        "docker_volumes": [f"C:/Projects/Factory:{cwd}:rw"],
    }}), encoding="utf-8")
    # Exercise the stdlib's Windows lexical parser without changing the interpreter's
    # platform or using Windows filesystem/process APIs on another host.
    monkeypatch.setattr(terminal_tool_config, "os", SimpleNamespace(path=ntpath))

    with install_and_reset_profile_terminal_scope(profile):
        config = tt._get_env_config()
        assert config["cwd"] == cwd
        assert config["host_cwd"] is None
        key = "container-path-flavor"
        try:
            tt.record_session_cwd(key, cwd)
            assert tt._resolve_command_cwd(
                workdir=None, default_cwd="/root", session_key=key, env_type="docker",
            ) == cwd
        finally:
            tt.clear_session_cwd(key)


@pytest.mark.platforms("windows")
@pytest.mark.parametrize("cwd", ["/factory", "/workspace"])
def test_native_windows_accepts_container_absolute_cwd(cwd):
    from tools.terminal_tool_config import _is_unusable_container_cwd

    assert not _is_unusable_container_cwd(cwd)
