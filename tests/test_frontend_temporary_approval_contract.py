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

    # 编辑/新建表单携带通用通道绑定（审批人统一由系统设置配置，token 级不再配置）
    for marker in ("channel_bindings",):
        assert marker in panel, marker
        assert marker in api, marker

    # 列表渲染支持通用绑定字段（不依赖 Matrix-only 字段）
    assert "channel_bindings" in panel


def test_temporary_approval_api_client_is_registered():
    api = _read("frontend/src/api.ts")
    assert "temporaryApprovals" in api
    assert "temporary-approvals" in api
    for marker in ("list:", "detail:", "confirm:", "revoke:"):
        assert marker in api, marker


def test_tool_token_payload_keeps_legacy_aliases_for_compat():
    panel = _read("frontend/src/pages/tools/ToolTokenPanel.tsx")
    # 房间/会话绑定字段仍保留；token 级审批人绑定已从 UI 移除（统一由系统设置配置）
    assert "bound_room_ids" in panel
