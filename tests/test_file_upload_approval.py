import asyncio
import hashlib
from io import BytesIO
from types import SimpleNamespace
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.services.approval_executor import ApprovalExecutor
from app.services.message_context import MessageContext
from app.services.qclaw_routing import compute_routing_revision, issue_ticket
from app.services.tool_adapters import file_transfer_tools
from app.services.tool_registry import ensure_builtin_registered, registry
from app.services.tool_policy import ai_tool_policy_metadata


@pytest.fixture(autouse=True)
def _strong_signing_key(monkeypatch):
    monkeypatch.setattr(
        "app.services.qclaw_routing.QCLAW_APPROVAL_SIGNING_KEY",
        "file-upload-test-key-0123456789abcdef",
    )


class _Upload:
    def __init__(self, filename: str, content: bytes):
        self.filename = filename
        self.file = BytesIO(content)

    async def seek(self, offset: int):
        self.file.seek(offset)


def test_file_upload_has_qclaw_approval_prepare_tool():
    ensure_builtin_registered()

    prepare_tool = registry.get("ops.approval.prepare_file_upload")
    upload_tool = registry.get("ops.upload_package")

    assert prepare_tool.category == "approval_prepare"
    assert "action_parameters" in prepare_tool.input_schema["properties"]
    assert upload_tool.category == "package_write"
    assert upload_tool.scopes == ["ops:read", "package:write"]


def test_upload_file_points_to_file_upload_approval():
    ensure_builtin_registered()

    metadata = ai_tool_policy_metadata(registry.get("ops.upload_file"))

    assert metadata["ai_level"] == "L4"
    assert "ops.approval.prepare_file_upload" in metadata["approval_hint"]


def test_approval_intake_accepts_room_bound_read_token_without_package_write(monkeypatch):
    from app.api import tools as tools_api
    from app.services import package_retention

    captured = {}
    ctx = SimpleNamespace(
        auth_type="tool_token",
        scopes=["ops:read"],
        channel_bindings=[{
            "channel": "matrix",
            "channel_account_id": "default",
            "conversation_id": "!ops:example.org",
        }],
        username="qclaw",
        token_owner="qclaw",
        client_name="qclaw",
        has_scope=lambda scope: scope == "ops:read",
    )

    def fake_save(db, **kwargs):
        captured.update(kwargs)
        return {
            "package_name": kwargs["filename"],
            "size_bytes": 7,
            "sha256": "d" * 64,
        }

    monkeypatch.setattr(tools_api, "get_tool_context", lambda request, db: ctx)
    monkeypatch.setattr(tools_api, "register_builtin_tools", lambda: None)
    monkeypatch.setattr(tools_api, "audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(package_retention, "save_package_fileobj", fake_save)

    package_sha256 = hashlib.sha256(b"package").hexdigest()
    response = asyncio.run(tools_api.upload_package_by_tool_token(
        request=SimpleNamespace(),
        file=_Upload("frontend.tar.gz", b"package"),
        system="crypto-trader",
        service="crypto-frontend",
        overwrite=True,
        approval_intake=True,
        room_id="!ops:example.org",
        request_event_id="$event",
        content_sha256="c" * 64,
        package_sha256=package_sha256,
        db=None,
    ))

    assert response["data"]["result"]["approval_intake"] is True
    assert captured["filename"].startswith(f"approval-{'c' * 12}-{package_sha256[:16]}-")
    assert captured["filename"].endswith("-frontend.tar.gz")
    assert captured["overwrite"] is False


def test_approval_intake_reuses_same_message_and_package_content(monkeypatch, tmp_path):
    from app.api import tools as tools_api
    from app.services import package_retention

    content = b"package"
    package_sha256 = hashlib.sha256(content).hexdigest()
    existing = tmp_path / "approval-package.tar.gz"
    existing.write_bytes(content)
    ctx = SimpleNamespace(
        auth_type="tool_token",
        channel_bindings=[{
            "channel": "matrix",
            "channel_account_id": "default",
            "conversation_id": "!ops:example.org",
        }],
        username="qclaw",
        token_owner="qclaw",
        client_name="qclaw",
        has_scope=lambda scope: scope == "ops:read",
    )

    monkeypatch.setattr(tools_api, "get_tool_context", lambda request, db: ctx)
    monkeypatch.setattr(tools_api, "register_builtin_tools", lambda: None)
    monkeypatch.setattr(tools_api, "audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(package_retention, "package_path", lambda filename: str(existing))
    monkeypatch.setattr(
        package_retention,
        "upsert_package_metadata",
        lambda *args, **kwargs: SimpleNamespace(package_name=args[1]),
    )
    monkeypatch.setattr(
        package_retention,
        "package_to_dict",
        lambda row, include_retention=False: {
            "package_name": row.package_name,
            "size_bytes": len(content),
            "sha256": package_sha256,
        },
    )
    monkeypatch.setattr(
        package_retention,
        "save_package_fileobj",
        lambda *args, **kwargs: pytest.fail("idempotent retry must not rewrite the package"),
    )

    response = asyncio.run(tools_api.upload_package_by_tool_token(
        request=SimpleNamespace(),
        file=_Upload("frontend.tar.gz", content),
        system="crypto-trader",
        service="crypto-frontend",
        overwrite=False,
        approval_intake=True,
        room_id="!ops:example.org",
        request_event_id="$event",
        content_sha256="c" * 64,
        package_sha256=package_sha256,
        db=None,
    ))

    assert response["data"]["result"]["reused"] is True


def test_approval_intake_rejects_unbound_read_token(monkeypatch):
    from app.api import tools as tools_api

    ctx = SimpleNamespace(
        auth_type="tool_token",
        channel_bindings=[],
        has_scope=lambda scope: scope == "ops:read",
    )
    monkeypatch.setattr(tools_api, "get_tool_context", lambda request, db: ctx)
    monkeypatch.setattr(tools_api, "register_builtin_tools", lambda: None)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(tools_api.upload_package_by_tool_token(
            request=SimpleNamespace(),
            file=_Upload("frontend.tar.gz", b"package"),
            system="crypto-trader",
            service="crypto-frontend",
            overwrite=False,
            approval_intake=True,
            room_id="!ops:example.org",
            request_event_id="$event",
            content_sha256="c" * 64,
            package_sha256=hashlib.sha256(b"package").hexdigest(),
            db=None,
        ))

    assert exc.value.status_code == 403


def test_file_upload_approval_dispatches_to_each_target(monkeypatch):
    calls = []

    def fake_upload(args, ctx, db):
        calls.append((args, ctx, db))
        return {"ok": True, "server": args["server"], "remote_path": args["remote_path"]}

    monkeypatch.setattr(file_transfer_tools, "upload_file", fake_upload)
    approval = SimpleNamespace(
        action_type="FILE_UPLOAD",
        approved_by="@approver:example.org",
        request_payload={
            "system_name": "crypto-trader",
            "service_name": "crypto-frontend",
            "targets": ["server-a", "server-b"],
            "action_parameters": {
                "package_name": "frontend.tar.gz",
                "remote_path": "/srv/releases/frontend.tar.gz",
                "overwrite": False,
                "expected_sha256": "abc123",
                "expected_size_bytes": 12,
            },
        },
    )

    result = ApprovalExecutor(None)._dispatch(approval)

    assert result["action"] == "FILE_UPLOAD"
    assert result["success_count"] == 2
    assert [call[0]["server"] for call in calls] == ["server-a", "server-b"]
    assert all(call[0]["confirm_text"] == "CONFIRM ops.upload_file" for call in calls)
    assert all(call[0]["expected_sha256"] == "abc123" for call in calls)


def test_file_upload_dispatch_raises_with_structured_result_when_any_target_fails(monkeypatch):
    def fake_upload(args, ctx, db):
        if args["server"] == "server-b":
            raise RuntimeError("simulated sftp failure")
        return {"ok": True, "server": args["server"]}

    monkeypatch.setattr(file_transfer_tools, "upload_file", fake_upload)
    approval = SimpleNamespace(
        action_type="FILE_UPLOAD",
        approved_by="@approver:example.org",
        request_payload={
            "targets": ["server-a", "server-b"],
            "action_parameters": {
                "package_name": "frontend.tar.gz",
                "remote_path": "/srv/releases/frontend.tar.gz",
            },
        },
    )

    with pytest.raises(RuntimeError) as exc:
        ApprovalExecutor(None)._dispatch(approval)

    assert exc.value.execution_result["action"] == "FILE_UPLOAD"
    assert exc.value.execution_result["success_count"] == 1
    assert exc.value.execution_result["fail_count"] == 1


def test_execution_plan_supports_file_upload_step(monkeypatch):
    from app.services.plan_executor import STEP_HANDLERS

    monkeypatch.setattr(
        file_transfer_tools,
        "upload_file",
        lambda args, ctx, db: {"ok": True, "server": args["server"]},
    )
    plan = SimpleNamespace(
        approved_by="@approver:example.org",
        system_name="crypto-trader",
        service_name="crypto-frontend",
        targets=["server-a"],
    )
    step = SimpleNamespace(parameters={
        "action_parameters": {
            "package_name": "frontend.tar.gz",
            "remote_path": "/srv/releases/frontend.tar.gz",
            "expected_sha256": "abc123",
            "expected_size_bytes": 12,
        },
    })

    result = STEP_HANDLERS["FILE_UPLOAD"](plan, step, None)

    assert result["action"] == "FILE_UPLOAD"
    assert result["success_count"] == 1


def test_prepare_file_upload_freezes_package_manifest(monkeypatch, tmp_path):
    from app.services.tool_adapters import approval_tools

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    package = upload_dir / "frontend.tar.gz"
    package.write_bytes(b"package-data")
    monkeypatch.setenv("UPLOAD_DIR", str(upload_dir))

    captured = {}

    class _Approval:
        id = "approval-1"
        action_type = "FILE_UPLOAD"
        action_digest = "digest-1"
        status = "PENDING_APPROVAL"
        expires_at = datetime.now(timezone.utc).replace(tzinfo=None)

    class _ApprovalService:
        def __init__(self, db):
            pass

        def prepare(self, **kwargs):
            captured.update(kwargs)
            return _Approval(), "ABCD1234"

    monkeypatch.setattr(approval_tools, "ActionApprovalService", _ApprovalService)
    monkeypatch.setattr(approval_tools, "_lookup_approvers", lambda *args, **kwargs: [])
    context = MessageContext(
        channel="matrix",
        channel_account_id="default",
        conversation_id="!room:example.org",
        message_id="$event",
        sender_id="@requester:example.org",
        content_sha256="b" * 64,
    )
    ticket = issue_ticket(
        context,
        "crypto-trader",
        "crypto-frontend",
        compute_routing_revision(approval_tools._routing_systems()),
    )

    result = approval_tools.approval_prepare_file_upload(
        {
            "message_context": context.to_dict(),
            "routing_ticket": ticket.ticket,
            "system_name": "crypto-trader",
            "service_name": "crypto-frontend",
            "environment": "test",
            "targets": ["server-a"],
            "action_parameters": {
                "package_name": "frontend.tar.gz",
                "remote_path": "/srv/releases/frontend.tar.gz",
            },
        },
        SimpleNamespace(bound_room_ids=[], approver_matrix_ids=["@approver:example.org"]),
        None,
    )

    assert result["approval_id"] == "approval-1"
    assert captured["action_type"] == "FILE_UPLOAD"
    assert captured["package_name"] == "frontend.tar.gz"
    assert captured["package_size_bytes"] == len(b"package-data")
    assert captured["action_parameters"]["package_name"] == "frontend.tar.gz"
    assert captured["action_parameters"]["expected_sha256"] == result["package_sha256"]


def test_upload_package_rejects_backend_path_outside_controlled_roots(tmp_path):
    from app.services.tool_adapters import file_tools

    outside = tmp_path / "secret.txt"
    outside.write_bytes(b"secret")

    with pytest.raises(HTTPException) as exc:
        file_tools.upload_package(
            {"local_path": str(outside), "filename": "secret.tar.gz"},
            SimpleNamespace(username="tester", token_owner=""),
            None,
        )

    assert exc.value.status_code == 400


def test_upload_file_rejects_approval_checksum_mismatch(monkeypatch, tmp_path):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    package = upload_dir / "app.tar.gz"
    package.write_bytes(b"test")

    monkeypatch.setenv("UPLOAD_DIR", str(upload_dir))

    with pytest.raises(HTTPException) as exc:
        file_transfer_tools.upload_file(
            {
                "server": "test-server",
                "package_name": "app.tar.gz",
                "remote_path": "/data/incoming/app.tar.gz",
                "confirm_text": "CONFIRM ops.upload_file",
                "expected_sha256": "not-the-local-digest",
            },
            None,
            None,
        )

    assert exc.value.status_code == 409
