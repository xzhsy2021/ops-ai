import hashlib
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.services.tool_adapters import file_transfer_tools
from app.services.tool_policy import ai_tool_policy_metadata
from app.services.tool_registry import registry


class _FakeSSH:
    def __init__(self):
        self.uploads = []

    def exec(self, command, timeout=300):
        if command.startswith("test -e "):
            return 1, "", ""
        if command.startswith("readlink -f "):
            return 0, "/data/incoming", ""
        if "sha256sum" in command:
            return 0, hashlib.sha256(b"test").hexdigest(), ""
        return 0, "", ""

    def upload(self, local_path, remote_path):
        self.uploads.append((local_path, remote_path))

    def close(self):
        pass


def test_upload_file_uses_controlled_package_path_and_sftp(monkeypatch, tmp_path):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    package = upload_dir / "app.tar.gz"
    package.write_bytes(b"test")
    ssh = _FakeSSH()

    monkeypatch.setenv("UPLOAD_DIR", str(upload_dir))
    monkeypatch.setattr(file_transfer_tools, "_connect", lambda server: (ssh, {"name": server, "sftp_allowed_roots": ["/data/incoming"]}))

    result = file_transfer_tools.upload_file(
        {
            "server": "test-server",
            "package_name": "app.tar.gz",
            "remote_path": "/data/incoming/app.tar.gz",
            "confirm_text": "CONFIRM ops.upload_file",
        },
        None,
        None,
    )

    assert result["ok"] is True
    assert result["remote_path"] == "/data/incoming/app.tar.gz"
    assert ssh.uploads[0][0] == str(package)
    assert ssh.uploads[0][1] != "/data/incoming/app.tar.gz"


def test_upload_file_rejects_path_outside_controlled_roots(monkeypatch, tmp_path):
    outside = tmp_path / "outside.tar.gz"
    outside.write_bytes(b"test")
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))

    with pytest.raises(HTTPException) as exc:
        file_transfer_tools.upload_file(
            {
                "server": "test-server",
                "local_path": str(outside),
                "remote_path": "/data/incoming/outside.tar.gz",
                "confirm_text": "CONFIRM ops.upload_file",
            },
            None,
            None,
        )

    assert exc.value.status_code == 400


def test_upload_file_policy_does_not_point_to_service_control_approval():
    tool = registry.get("ops.upload_file")
    metadata = ai_tool_policy_metadata(tool)

    assert metadata["ai_level"] == "L4"
    assert "service_control" not in metadata["approval_hint"]


class _RecordingSSH:
    def __init__(self, local_sha256: str, *, real_parent: str = "/data/incoming", checksum_available: bool = True):
        self.local_sha256 = local_sha256
        self.real_parent = real_parent
        self.checksum_available = checksum_available
        self.commands = []
        self.uploads = []

    def exec(self, command, timeout=300):
        self.commands.append(command)
        if command.startswith("test -e "):
            return 1, "", ""
        if command.startswith("readlink -f "):
            return 0, self.real_parent, ""
        if "sha256sum" in command:
            return (0, self.local_sha256, "") if self.checksum_available else (0, "", "")
        return 0, "", ""

    def upload(self, local_path, remote_path):
        self.uploads.append((local_path, remote_path))

    def close(self):
        pass


def _upload_with_recording_ssh(monkeypatch, tmp_path, ssh):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    package = upload_dir / "app.tar.gz"
    package.write_bytes(b"test")
    monkeypatch.setenv("UPLOAD_DIR", str(upload_dir))
    monkeypatch.setattr(
        file_transfer_tools,
        "_connect",
        lambda server: (ssh, {"name": server, "sftp_allowed_roots": ["/data/incoming"]}),
    )
    return file_transfer_tools.upload_file(
        {
            "server": "test-server",
            "package_name": "app.tar.gz",
            "remote_path": "/data/incoming/app.tar.gz",
            "confirm_text": "CONFIRM ops.upload_file",
        },
        None,
        None,
    )


def test_upload_file_verifies_temp_file_before_atomic_publish(monkeypatch, tmp_path):
    local_sha256 = hashlib.sha256(b"test").hexdigest()
    ssh = _RecordingSSH(local_sha256)

    result = _upload_with_recording_ssh(monkeypatch, tmp_path, ssh)

    assert result["ok"] is True
    assert ssh.uploads[0][1] != "/data/incoming/app.tar.gz"
    assert any("ln --" in command and "/data/incoming/app.tar.gz" in command for command in ssh.commands)


def test_upload_file_rejects_resolved_parent_outside_allowed_root(monkeypatch, tmp_path):
    local_sha256 = hashlib.sha256(b"test").hexdigest()
    ssh = _RecordingSSH(local_sha256, real_parent="/etc")

    with pytest.raises(HTTPException) as exc:
        _upload_with_recording_ssh(monkeypatch, tmp_path, ssh)

    assert exc.value.status_code == 403
    assert ssh.uploads == []


def test_upload_file_fails_when_remote_checksum_is_unavailable(monkeypatch, tmp_path):
    local_sha256 = hashlib.sha256(b"test").hexdigest()
    ssh = _RecordingSSH(local_sha256, checksum_available=False)

    with pytest.raises(HTTPException) as exc:
        _upload_with_recording_ssh(monkeypatch, tmp_path, ssh)

    assert exc.value.status_code == 502
