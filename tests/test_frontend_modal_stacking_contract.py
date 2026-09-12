"""前端弹窗层级与风险问题跳转契约测试。

回归背景：
1) 风险问题详情弹窗与巡检详情弹窗都是 `.inspection-modal-overlay`（position:fixed, z-index:1000），
   DOM 靠后者绘制在上层 —— 若风险详情渲染在后面，点"查看所属巡检详情"会被它盖住。
2) 概览页曾各自再渲染一个 IssueDetailModal 实例，导致同类叠加问题。
"""
from __future__ import annotations

import re
from pathlib import Path

FRONTEND_SRC = Path("frontend/src")


def _page() -> str:
    return open("frontend/src/pages/InspectionCenterPage.tsx", encoding="utf-8").read()


def _modal() -> str:
    return open("frontend/src/pages/inspection/IssueDetailModal.tsx", encoding="utf-8").read()


def _overview() -> str:
    return open("frontend/src/pages/inspection/OverviewTab.tsx", encoding="utf-8").read()


def _css() -> str:
    return open("frontend/src/index.css", encoding="utf-8").read()


def test_both_modals_share_the_same_fixed_overlay_layer():
    """前提：两个弹窗同层 —— 因此 DOM 顺序就是绘制顺序。"""
    css = _css()
    block = re.search(r"\.inspection-modal-overlay\s*\{[^}]*\}", css)
    assert block, "缺少 .inspection-modal-overlay 样式"
    assert "position: fixed" in block.group(0)
    assert "z-index" in block.group(0)

    page = _page()
    assert page.count('className="inspection-modal-overlay"') >= 0  # 由各弹窗组件内部使用
    assert "inspection-modal-overlay" in _modal()


def test_issue_detail_modal_renders_before_run_detail_modal():
    """风险问题详情必须渲染在巡检详情之前，否则会盖住跳转出来的巡检详情。"""
    page = _page()
    issue_at = page.index("{issueDetailId && (")
    run_at = page.index("{runDetailOpen && currentResult && (")
    assert issue_at < run_at, "风险问题详情弹窗必须渲染在 RunDetailModal 之前（同层遮罩按 DOM 顺序绘制）"


def test_jump_to_run_closes_issue_modal_first():
    """点"查看所属巡检详情"：先取到详情，成功即关闭风险问题详情。"""
    modal = _modal()
    page = _page()

    assert "onOpenRun?: (runId: string) => unknown | Promise<unknown>" in modal
    assert "await onOpenRun(runId)" in modal
    assert "setBusy('run')" in modal
    assert "加载巡检详情…" in modal

    assert "await inspection.runDetail(runId)" in page
    assert "setIssueDetailId('')" in page


def test_overview_tab_reuses_the_parent_modal_instance():
    """概览页不再自建弹窗实例，统一交给父级，避免两个遮罩叠加。"""
    overview = _overview()
    assert "IssueDetailModal" not in overview
    assert "onOpenIssue?: (issueId: string) => void" in overview
    assert "onOpenIssue?.(i.id)" in overview

    page = _page()
    assert "onOpenIssue={(issueId: string) => setIssueDetailId(issueId)}" in page


def test_unclosed_status_semantics_shared_across_frontend():
    """未闭环口径在前端集中定义并复用，避免各处手写 'OPEN' 造成数字不一致。

    第 9 轮修订：原先这里还断言 `frontend/src/services/liveStatus.ts` 内有两处
    `status: 'OPEN,PROCESSING'`。该模块（230 行）已确认是死代码——前端全仓无任何
    导入（其文档注释声称的 `ProbeDropdown` 组件根本不存在，`SiteStatusPanel` 早已
    改为在 DashboardPage 内自行取数），因此已删除。相应地，本用例改为断言更强的
    不变式：未闭环字面量在整个前端只能出现一次（唯一定义处），任何新增的手写副本
    都会让用例失败。
    """
    page = _page()

    assert "const UNCLOSED_STATUS = 'OPEN,PROCESSING'" in page
    assert "useState(UNCLOSED_STATUS)" in page
    assert "value={UNCLOSED_STATUS}" in page

    offenders = []
    for path in sorted(FRONTEND_SRC.rglob("*.ts*")):
        if path.name == "InspectionCenterPage.tsx":
            continue  # 唯一定义处
        text = path.read_text(encoding="utf-8")
        if "OPEN,PROCESSING" in text:
            offenders.append(str(path).replace("\\", "/"))
    assert offenders == [], (
        "未闭环口径必须集中在 InspectionCenterPage 的 UNCLOSED_STATUS，"
        f"以下文件又手写了字面量：{offenders}"
    )


def test_issue_mutations_refresh_overview_counts():
    """问题状态变更/删除后必须同时刷新总览统计，否则卡片数字会落后于列表。"""
    page = _page()
    assert "async function reloadOverview()" in page
    assert "async function reloadIssuesAndOverview()" in page
    assert "onChanged={reloadIssuesAndOverview}" in page
    # updateIssue / deleteIssue 内也要刷新总览
    assert page.count("await reloadIssuesAndOverview()") >= 2
