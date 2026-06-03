# 巡检中心启动错误修复说明

## 修复的问题

启动时报错：

```text
ImportError: cannot import name 'InspectionRuleSet' from 'app.db.models'
```

根因通常是本地工程中同时存在：

```text
app/services/inspection.py
app/services/inspection/
```

Python 会优先把 `app/services/inspection/` 当作 package 导入，导致加载到旧版 `runner.py`，而旧版代码引用了不存在的 `InspectionRuleSet`。

## 本次修复

1. 将巡检服务改为唯一命名：`app/services/inspection_center.py`。
2. 将 `app/api/inspection.py` 的导入改为：

```python
from app.services import inspection_center as svc
```

3. 避免与历史残留目录 `app/services/inspection/` 发生导入冲突。

## 建议清理

在本地项目根目录执行：

Windows PowerShell：

```powershell
if (Test-Path .\app\services\inspection -PathType Container) {
  Remove-Item .\app\services\inspection -Recurse -Force
}
```

Linux/macOS：

```bash
rm -rf app/services/inspection
```

然后重新启动后端。
