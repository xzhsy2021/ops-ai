# 巡检中心落地实施结果

## 已完成

- 新增后端巡检服务 `app/services/inspection.py`。
- 新增后端巡检 API `app/api/inspection.py`。
- 新增巡检数据模型：`InspectionRun`、`InspectionEvidence`、`InspectionItemResult`、`InspectionIssue`、`InspectionRule`、`InspectionBaseline`。
- 注册 `/api/v2/inspection` Router。
- 报告中心新增 `inspection` 报告类型，支持从巡检 run 生成 JSON / Markdown 报告。
- 前端新增“巡检”菜单与 `/inspection` 页面。
- 前端新增服务器巡检、项目巡检、巡检记录、风险问题、生成报告交互。
- 项目巡检会关联服务配置中的部署服务器，并引用近 7 天服务器巡检摘要。
- 首版执行边界为只读巡检，不包含自动封禁、冻结、删除、修改防火墙等高风险动作。

## 关键入口

- 前端：`/inspection`
- API：`/api/v2/inspection/overview`
- 报告生成：`POST /api/v2/reports/generate`，`report_type=inspection`

## 验证结果

已执行：

```bash
python3 -m py_compile app/services/inspection.py app/api/inspection.py main.py app/db/models.py app/db/__init__.py app/services/report_center.py app/pages.py
```

结果：通过。

未执行完整后端测试：当前沙箱未安装 `requirements.txt` 中的 `sqlalchemy`、`fastapi` 等依赖。

未完成前端构建：上传源码包未包含 `node_modules`，执行 `npm ci` 时内置 registry 对 `proxy-from-env@2.1.1` 返回 404。请在项目实际开发环境或可访问 npm registry 的 CI 环境执行：

```bash
cd frontend
npm ci
npm run typecheck
npm run build
```

## 后续建议

1. 在真实开发环境完成依赖安装和完整测试。
2. 将现有状态页核心指标进一步嵌入巡检总览。
3. 将诊断页检查项转为巡检 checker。
4. 增加定时巡检任务和每日/每周/月度报告。
5. 增加项目文件 SHA256 基线和配置基线比对。
6. 接入客户白名单、客户权限、客户操作日志数据源。
