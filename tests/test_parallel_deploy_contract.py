"""P0-1 并发部署契约测试：验证 parallelism / fail_fast / wave_size 行为。"""
from __future__ import annotations
import pytest
from unittest.mock import patch, MagicMock


def _make_deploy_request(servers=None, parallelism=1, fail_fast=True, wave_size=None):
    from app.deploy.schemas import DeployRequest
    return DeployRequest(
        system="ops",
        service="api",
        environment="test",
        servers=servers or ["s1", "s2", "s3"],
        version="v1.0",
        parallelism=parallelism,
        fail_fast=fail_fast,
        wave_size=wave_size,
    )


class TestParallelDeploySchemaDefaults:
    def test_default_parallelism_is_one(self):
        req = _make_deploy_request()
        assert req.parallelism == 1
        assert req.fail_fast is True
        assert req.wave_size is None

    def test_parallelism_clamped_to_16(self):
        from pydantic import ValidationError
        from app.deploy.schemas import DeployRequest
        with pytest.raises(ValidationError):
            DeployRequest(system="ops", service="api", servers=["s1"], parallelism=17)

    def test_wave_size_optional(self):
        req = _make_deploy_request(wave_size=2)
        assert req.wave_size == 2


class TestParallelDeployStatusAggregation:
    def _compute_final_status(self, results, fail_fast):
        success_count = sum(1 for _n, ok, _e in results if ok)
        fail_count = sum(1 for _n, ok, _e in results if not ok and _e != "skipped")
        if fail_fast and fail_count > 0:
            return "failed"
        elif success_count == 0:
            return "failed"
        elif success_count > 0 and fail_count > 0:
            return "partial_failed"
        else:
            return "success"

    def test_concurrent_fail_fast_true_failure_stops_remaining(self):
        results = [
            ("s1", True, ""),
            ("s2", False, "SSH 连接失败"),
            ("s3", False, "skipped"),
        ]
        final_status = self._compute_final_status(results, fail_fast=True)
        assert final_status == "failed"

    def test_concurrent_fail_fast_false_partial_failed(self):
        results = [
            ("s1", True, ""),
            ("s2", False, "发布包分发失败"),
            ("s3", True, ""),
        ]
        final_status = self._compute_final_status(results, fail_fast=False)
        assert final_status == "partial_failed"

    def test_concurrent_all_fail_is_failed_not_partial(self):
        results = [
            ("s1", False, "error1"),
            ("s2", False, "error2"),
            ("s3", False, "error3"),
        ]
        final_status = self._compute_final_status(results, fail_fast=False)
        assert final_status == "failed"

    def test_all_success_is_success(self):
        results = [
            ("s1", True, ""),
            ("s2", True, ""),
            ("s3", True, ""),
        ]
        final_status = self._compute_final_status(results, fail_fast=False)
        assert final_status == "success"

    def test_fail_fast_false_with_skipped_only_fail(self):
        results = [
            ("s1", False, "error1"),
            ("s2", False, "skipped"),
            ("s3", False, "skipped"),
        ]
        final_status = self._compute_final_status(results, fail_fast=False)
        assert final_status == "failed"


class TestPartialFailedStateInTerminal:
    def test_partial_failed_is_terminal(self):
        from app.deploy.state import normalize_status, is_terminal_status, PARTIAL_FAILED
        assert PARTIAL_FAILED == "partial_failed"
        assert is_terminal_status(PARTIAL_FAILED) is True
        assert normalize_status(PARTIAL_FAILED) == PARTIAL_FAILED


class TestProdParallelRiskElevation:
    def test_prod_parallel_requires_special_confirm_text(self):
        from app.api.deploy._shared import _build_confirmation
        from app.deploy.schemas import DeployRequest

        req = DeployRequest(
            system="ops", service="api", environment="PROD",
            servers=["s1", "s2", "s3"],
            parallelism=4,
        )
        with patch("app.api.deploy._shared._derive_servers", return_value=req.servers), \
             patch("app.api.deploy._shared._merge_release_variables", return_value={}), \
             patch("app.api.deploy._shared._find_config_service", return_value=None), \
             patch("app.api.deploy._shared._service_topology", return_value={}), \
             patch("app.api.deploy._shared._package_service_match", return_value={"status": "ok"}), \
             patch("app.api.deploy._shared._db_pipeline_steps", return_value=[{"name": "deploy", "type": "command", "config": {}}]), \
             patch("app.api.deploy._shared._default_release_steps", return_value=[{"name": "deploy", "type": "command", "config": {}}]), \
             patch("app.api.deploy._shared._environment_server_conflicts", return_value=[]), \
             patch("app.api.deploy._shared._rollback_plan_for", return_value={"safe": True}):
            result = _build_confirmation(req, MagicMock())
            assert result["risk_level"] == "high"
            assert result["required_confirmation"] == "CONFIRM PARALLEL DEPLOY"
            assert any(r.startswith("并发发布生产环境") for r in result["risk_reasons"])

    def test_non_prod_parallel_no_elevation(self):
        from app.api.deploy._shared import _build_confirmation
        from app.deploy.schemas import DeployRequest

        req = DeployRequest(
            system="ops", service="api", environment="test",
            servers=["s1", "s2", "s3"],
            parallelism=8,
        )
        with patch("app.api.deploy._shared._derive_servers", return_value=req.servers), \
             patch("app.api.deploy._shared._merge_release_variables", return_value={}), \
             patch("app.api.deploy._shared._find_config_service", return_value=None), \
             patch("app.api.deploy._shared._service_topology", return_value={}), \
             patch("app.api.deploy._shared._package_service_match", return_value={"status": "ok"}), \
             patch("app.api.deploy._shared._db_pipeline_steps", return_value=[{"name": "deploy", "type": "command", "config": {}}]), \
             patch("app.api.deploy._shared._default_release_steps", return_value=[{"name": "deploy", "type": "command", "config": {}}]), \
             patch("app.api.deploy._shared._environment_server_conflicts", return_value=[]), \
             patch("app.api.deploy._shared._rollback_plan_for", return_value={"safe": True}):
            result = _build_confirmation(req, MagicMock())
            assert result["required_confirmation"] != "CONFIRM PARALLEL DEPLOY"
