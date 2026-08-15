"""Task 11: 前端临时审批与通道绑定契约测试。

断言：
- TemporaryApprovalPanel 已创建且只挂载一次，无 setInterval 轮询；
- Tool Token 编辑器使用通用通道绑定（channel/account/conversation/sender），
  不再只用 Matrix-only 房间/用户芯片；
- api.ts 注册临时审批客户端与通用绑定字段。
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = REPO_ROOT / "frontend" / "src"


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_temporary_panel_is_mounted_once_without_polling_loop():
    panel = FRONTEND_ROOT / "pages" / "tools" / "TemporaryApprovalPanel.tsx"
    assert panel.is_file(), "TemporaryApprovalPanel.tsx 不存在"
    source = panel.read_text(encoding="utf-8")
    assert "setInterval" not in source, "临时授权面板不得使用轮询"
    assert "confirmation_code_hash" not in source
    # 面板自包含 API 引用，只挂载一次，不做轮询
    assert "temporaryApprovals" in source

    page = _read("frontend/src/pages/ToolAccessPage.tsx")
    assert page.count("TemporaryApprovalPanel") >= 1, "ToolAccessPage 未挂载临时授权面板"


def test_tool_token_editor_uses_generic_channel_bindings():
    panel = _read("frontend/src/pages/tools/ToolTokenPanel.tsx")
    api = _read("frontend/src/api.ts")

    # Phase 2：token 级房间/审批人绑定已从 UI 彻底移除，统一由系统设置
    # （message_routing）配置授权房间/会话，面板不再配置任何 token 级绑定字段。
    for marker in ("channel_bindings", "bound_room_ids", "approver_identities", "approver_matrix_ids"):
        assert marker not in panel, f"ToolTokenPanel 不应再配置 token 级绑定字段: {marker}"

    # 面板应引导用户到系统设置配置授权房间/会话
    assert "已迁移至系统设置" in panel
    assert "系统设置" in panel


def test_temporary_approval_api_client_is_registered():
    api = _read("frontend/src/api.ts")
    assert "temporaryApprovals" in api
    assert "temporary-approvals" in api
    for marker in ("list:", "detail:", "confirm:", "revoke:"):
        assert marker in api, marker


def test_tool_token_payload_keeps_legacy_aliases_for_compat():
    panel = _read("frontend/src/pages/tools/ToolTokenPanel.tsx")
    api = _read("frontend/src/api.ts")
    # Phase 2：token 级房间/会话绑定已从 UI 移除（统一由系统设置配置）。
    assert "bound_room_ids" not in panel
    # API 载荷仍保留 legacy 别名，保证旧客户端/序列化向后兼容（后端忽略多余字段）。
    assert "bound_room_ids" in api
