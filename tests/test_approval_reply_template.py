"""prepare_plan / prepare_service_control 返回固定格式回执 reply_template 的契约测试。

背景：zeroclaw agent 自由发挥组装 PENDING_APPROVAL 房间消息导致格式错乱
（截断、漏字段、乱排序）。责任分配：OPS 是格式唯一事实源——结构化字段
供程序化消费，reply_template 供 Agent 原样转发（msgtype=m.text 可直接用）。
"""
import pytest

from app.services.tool_adapters.approval_tools import _approval_reply_template


class _Step:
    def __init__(self, action_type):
        self.action_type = action_type


def test_reply_template_contains_required_fields():
    """固定中文 Markdown：全部要素 + 末尾可复制审批行。"""
    tpl = _approval_reply_template(
        kind="plan",
        status="PENDING_APPROVAL",
        object_id="d62c065a674043d4b47b9a155c317a81",
        short_code="批准拉取附件+上传+发布 crypto-trader@test 2A65AB7E",
        system_name="crypto-trader",
        service_name="crypto-trader-web",
        environment="test",
        targets=["203.0.113.10"],
        expires_at="2026-08-27T10:56:49+00:00",
        steps=[_Step("MATRIX_PULL"), _Step("FILE_UPLOAD"), _Step("RELEASE")],
        approvers=["@jack.han:hubtel.xyz"],
        package_size_bytes=1686218,
        package_sha256="f94140bbd39cfd9fde5b3162c0dbe3d9ac7d2c00f97d2a71b154c397544e3728",
    )
    # 用户点名的要素逐一在场
    assert "OPS 审批执行计划已创建" in tpl
    assert "当前状态：`PENDING_APPROVAL`" in tpl
    assert "计划 ID：`d62c065a674043d4b47b9a155c317a81`" in tpl
    assert "确认短语：`批准拉取附件+上传+发布 crypto-trader@test 2A65AB7E`" in tpl
    assert "指定审批人：`@jack.han:hubtel.xyz`" in tpl
    assert "服务：`crypto-trader-web`" in tpl
    assert "环境：`test`" in tpl
    assert "目标：`203.0.113.10`" in tpl
    assert "步骤：拉取附件 → 上传制品 → 发布" in tpl
    assert "包大小：`1,686,218 bytes`" in tpl
    assert "SHA256：`f94140bb" in tpl
    assert "有效期至：" in tpl and "北京时间" in tpl
    # 单独的可复制审批行
    assert "请指定审批人回复：" in tpl
    assert tpl.endswith("```text\n批准 批准拉取附件+上传+发布 crypto-trader@test 2A65AB7E\n```")


def test_reply_template_graceful_omission():
    """可选字段缺省时行自动省略，不产生空占位。"""
    tpl = _approval_reply_template(
        kind="action",
        status="PENDING_APPROVAL",
        object_id="abc123",
        short_code="批准服务控制 crypto@test A0FAB137",
        system_name="crypto",
        service_name="",
        environment="test",
        targets=[],
        expires_at=None,
        steps=None,
        approvers=None,
    )
    assert "审批工单已创建" in tpl
    assert "服务：" not in tpl
    assert "目标：" not in tpl
    assert "指定审批人：" not in tpl
    assert "包大小" not in tpl and "SHA256" not in tpl
    assert "有效期至：`-`" in tpl
    assert tpl.endswith("```text\n批准 批准服务控制 crypto@test A0FAB137\n```")


def test_reply_template_expiry_beijing_time():
    """有效期显示北京时间。"""
    tpl = _approval_reply_template(
        kind="plan", status="PENDING_APPROVAL", object_id="x",
        short_code="批准发布 a@t 12345678",
        system_name="a", service_name="", environment="t",
        targets=[], expires_at="2026-08-27T10:56:49+00:00",
    )
    assert "2026-08-27 18:56:49（北京时间）" in tpl
