"""Local launch isolation must not modify another project's environment or disable TLS."""

import pytest

from scripts.run_local import local_command, local_environment


def test_launcher_scopes_operator_ca_to_child_and_preserves_explicit_configuration(tmp_path):
    bundle = tmp_path / "docker/extra-ca-certs/local-avast-bundle.pem"
    bundle.parent.mkdir(parents=True)
    bundle.write_text("test-only-certificate-path", encoding="utf-8")
    original = {"REQUESTS_CA_BUNDLE": "operator-existing.pem", "OTHER_PROJECT_SETTING": "unchanged"}
    environment = local_environment(tmp_path, original)
    assert original == {"REQUESTS_CA_BUNDLE": "operator-existing.pem", "OTHER_PROJECT_SETTING": "unchanged"}
    assert environment["REQUESTS_CA_BUNDLE"] == "operator-existing.pem"
    assert environment["NODE_EXTRA_CA_CERTS"] == str(bundle)
    assert environment["GRPC_DEFAULT_SSL_ROOTS_FILE_PATH"] == str(bundle)
    assert "NODE_TLS_REJECT_UNAUTHORIZED" not in environment


def test_no_local_ca_does_not_change_standard_trust(tmp_path):
    original = {"OTHER_PROJECT_SETTING": "untouched"}
    assert local_environment(tmp_path, original) == original


def test_windows_launcher_refuses_a_wsl_only_environment(tmp_path):
    old = tmp_path / ".venv/bin/python"
    old.parent.mkdir(parents=True)
    old.touch()
    with pytest.raises(FileNotFoundError, match=".venv-windows"):
        local_command(tmp_path, [], windows=True)


def test_launcher_uses_explicit_executable_and_preserves_port_arguments(tmp_path):
    executable = tmp_path / ".venv-windows/Scripts/python.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()
    command = local_command(tmp_path, ["--server.port", "8502"], windows=True)
    assert command[0] == str(executable)
    assert command[1] == str(tmp_path / "scripts/local_runtime.py")
    assert command[-2:] == ["--server.port", "8502"]


def test_windows_runtime_loads_verified_system_trust_before_streamlit(monkeypatch):
    import sys
    from unittest.mock import Mock

    from scripts.local_runtime import configure_trust

    trust = Mock()
    monkeypatch.setitem(sys.modules, "truststore", trust)
    configure_trust(windows=False)
    trust.inject_into_ssl.assert_not_called()
    configure_trust(windows=True)
    trust.inject_into_ssl.assert_called_once_with()
