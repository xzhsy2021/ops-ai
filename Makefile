# ops-ai 项目 Makefile
# CI gate / 本地一致性检查
# 详细: docs/ssot.md

PYTHON ?= python

.PHONY: help inventory-check inventory-report migrate-inventory ssot-guard

help: ## 显示帮助
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

inventory-check: ## CI gate: 跑 inventory_reconcile --fail-on-drift, 漂移即非零退出
	$(PYTHON) -m app.maintenance.inventory_reconcile --fail-on-drift --ignore-systems=insider,sleuther,bot-hub,dovo

inventory-report: ## 输出对账报告(JSON + 控制台)
	$(PYTHON) -m app.maintenance.inventory_reconcile --save-json

migrate-inventory: ## 一次性回填 config_kv -> DB(已 SSOT 化,通常 no-op)
	$(PYTHON) -m app.maintenance.migrate_inventory_once --apply

ssot-guard: ## 触发 'systems' 写废弃警告(自检)
	$(PYTHON) -c "import sys; sys.path.insert(0, '.'); \
from app.config.repository import load_config, save_config; \
c = load_config(); c['systems']['__guard_test__'] = {'display_name': 'guard'}; \
print('save_config returned:', save_config(c)); \
c2 = load_config(); c2['systems'].pop('__guard_test__', None); save_config(c2); \
print('cleanup done')"
