from __future__ import annotations


def test_frontend_syntax_check_loads_typescript_from_frontend_node_modules():
    script = open("scripts/frontend_syntax_check.js", encoding="utf-8").read()

    assert "frontendNodeModules" in script
    assert "requireFromFrontend" in script
    assert "path.join(process.cwd(), 'frontend', 'node_modules')" in script


def test_tool_access_page_uses_schema_light_initial_load_and_tab_lazy_loaders():
    page = open("frontend/src/pages/ToolAccessPage.tsx", encoding="utf-8").read()

    assert "include_schema: false" in page
    assert "profile: 'admin_full'" in page
    assert "const loadInitial = async" in page
    assert "const loadTabData = async" in page
    assert "loadTabData(activeTab)" in page
    assert "Promise.allSettled([" in page
    assert "capabilityTools.calls({ limit: 30 })" not in page.split("const loadInitial = async", 1)[1].split("const loadTabData = async", 1)[0]


def test_frontend_capability_cache_can_be_cleared_after_mutations():
    api = open("frontend/src/api.ts", encoding="utf-8").read()
    page = open("frontend/src/pages/ToolAccessPage.tsx", encoding="utf-8").read()

    assert "clearCachedGet" in api
    assert "clearCache:" in api
    assert "capabilityTools.clearCache()" in page
    assert "policyPreview:" in api


def test_token_panel_exposes_policy_preview_ui():
    panel = open("frontend/src/pages/tools/ToolTokenPanel.tsx", encoding="utf-8").read()
    page = open("frontend/src/pages/ToolAccessPage.tsx", encoding="utf-8").read()

    assert "onPreviewPolicy" in panel
    assert "权限预览" in panel
    assert "ops.inspection.run_server" in panel
    assert "capabilityTools.policyPreview" in page


def test_tool_catalog_has_inline_detail_and_compact_interaction_contract():
    panel = open("frontend/src/pages/tools/ToolCatalogPanel.tsx", encoding="utf-8").read()
    page = open("frontend/src/pages/ToolAccessPage.tsx", encoding="utf-8").read()
    css = open("frontend/src/index.css", encoding="utf-8").read()

    assert "selectedToolName" in panel
    assert "tool-directory-workspace" in panel
    assert "tool-directory-master" in panel
    assert "tool-directory-detail" in panel
    assert "复制工具名" in panel
    assert "onOpenPlayground" in panel
    assert "setCatalogDrawerOpen(true)" not in page
    assert ".tool-directory-workspace" in css
    assert "grid-template-columns: minmax(360px, .95fr) minmax(420px, 1.05fr)" in css


def test_tool_token_panel_uses_chinese_permission_labels():
    panel = open("frontend/src/pages/tools/ToolTokenPanel.tsx", encoding="utf-8").read()

    assert "SCOPE_LABELS" in panel
    for label in ["运维读取", "运维写入", "服务器读取", "审计读取", "发布预检", "数据库写入", "全部权限"]:
        assert label in panel
    assert "formatScopeLabel" in panel


def test_tool_token_modals_escape_backdrop_filter_compositing_context():
    panel = open("frontend/src/pages/tools/ToolTokenPanel.tsx", encoding="utf-8").read()
    css = open("frontend/src/index.css", encoding="utf-8").read()

    assert "createPortal" in panel
    assert "document.body" in panel
    assert panel.count("<ToolTokenModal") == 2
    assert "tool-token-modal-dialog" in panel
    modal_css = css.split(".tool-token-modal-dialog", 1)[1].split("}", 1)[0]
    assert "backdrop-filter: none" in modal_css
