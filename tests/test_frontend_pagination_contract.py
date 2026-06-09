from __future__ import annotations


def test_report_center_page_uses_backend_pagination_contract():
    page = open("frontend/src/pages/ReportCenterPage.tsx", encoding="utf-8").read()
    api = open("frontend/src/api.ts", encoding="utf-8").read()

    assert "const [offset, setOffset]" in page
    assert "const [pageSize, setPageSize]" in page
    assert "reports.list({ report_type: reportType || undefined, limit: pageSize, offset })" in page
    assert "setTotal(Number(listRes.data?.total || 0))" in page
    assert "PageControls" in page
    assert "上一页" in page and "下一页" in page
    assert "limit?: number; offset?: number" in api


def test_inspection_issues_are_paginated_and_ledger_reports_are_primary():
    page = open("frontend/src/pages/InspectionCenterPage.tsx", encoding="utf-8").read()
    api = open("frontend/src/api.ts", encoding="utf-8").read()
    css = open("frontend/src/index.css", encoding="utf-8").read()

    assert "const [issueOffset, setIssueOffset]" in page
    assert "const [issuePageSize, setIssuePageSize]" in page
    assert "const [issuesTotal, setIssuesTotal]" in page
    assert "inspection.issues({ status: issueFilter || undefined, limit: issuePageSize, offset: issueOffset })" in page
    assert "PaginationControls total={issuesTotal}" in page
    assert "issues: (params?: { scope_type?: string; risk_level?: string; status?: string; server_id?: string; project_id?: string; limit?: number; offset?: number })" in api
    assert "inspection-ledger-layout" in page
    assert "inspection-report-primary" in page
    assert ".inspection-ledger-layout" in css


def test_inspection_page_exposes_profile_preview_and_confirmation_workflow():
    page = open("frontend/src/pages/InspectionCenterPage.tsx", encoding="utf-8").read()
    api = open("frontend/src/api.ts", encoding="utf-8").read()

    assert "profiles: () => api.get('/inspection/profiles')" in api
    assert "profilePreview: (data: { profile_id: string }) => api.post('/inspection/profiles/preview'" in api
    assert "profileRun: (data: { profile_id: string; confirm_text: string; expected_count?: number; fingerprint?: string }) => api.post('/inspection/profiles/run'" in api
    assert "profilePreview" in page
    assert "runInspectionProfile" in page
    assert "profile-confirm-hint" in page
    assert "confirmMode=\"one-click\"" in page
    assert "showConfirmTextInOneClick={false}" in page
    assert "无需手动输入字符串" in page
    assert "RiskConfirmDialog" in page


def test_task_center_uses_server_pagination_and_compact_header_layout():
    page = open("frontend/src/pages/TaskCenterPage.tsx", encoding="utf-8").read()
    api = open("frontend/src/api.ts", encoding="utf-8").read()
    backend = open("app/api/task_center.py", encoding="utf-8").read()
    runtime_jobs = open("app/domain/runtime/jobs.py", encoding="utf-8").read()
    css = open("frontend/src/index.css", encoding="utf-8").read()

    assert "const [offset, setOffset]" in page
    assert "const [pageSize, setPageSize]" in page
    assert "taskCenter.list({ kind: kind || undefined, status: status || undefined, limit: pageSize, offset })" in page
    assert "setTotal(Number(res.data?.total || 0))" in page
    assert "task-center-toolbar" in page
    assert "task-center-content" in page
    assert "TaskPagination" in page
    assert "list: (params?: { status?: string; kind?: string; limit?: number; offset?: number })" in api
    assert "offset: int = 0" in backend
    assert "total = count_runtime_jobs(db, status=status, kind=kind)" in backend
    assert "fetch_limit = max(1, min(offset + limit, 500))" in runtime_jobs
    assert ".task-center-toolbar" in css


def test_backend_issue_and_task_pagination_return_total_and_offset_contract():
    inspection_service = open("app/services/inspection_center.py", encoding="utf-8").read()
    inspection_api = open("app/api/inspection.py", encoding="utf-8").read()
    task_api = open("app/api/task_center.py", encoding="utf-8").read()

    assert "def list_issues(db: Session, *, scope_type: str = \"\", risk_level: str = \"\", status: str = \"\", server_id: str = \"\", project_id: str = \"\", limit: int = 200, offset: int = 0)" in inspection_service
    assert "total = q.count()" in inspection_service
    assert ".offset(offset).limit(limit)" in inspection_service
    assert "\"offset\": offset" in inspection_service
    assert "def issues(request: Request, scope_type: str = \"\", risk_level: str = \"\", status: str = \"\", server_id: str = \"\", project_id: str = \"\", limit: int = 200, offset: int = 0" in inspection_api
    assert "limit=limit, offset=offset" in inspection_api
    assert "total = count_runtime_jobs(db, status=status, kind=kind)" in task_api
    assert "tasks = list_runtime_jobs(db, status=status, kind=kind, limit=limit, offset=offset)" in task_api
    assert "\"offset\": offset" in task_api
