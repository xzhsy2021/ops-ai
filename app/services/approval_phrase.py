"""审批确认短语生成：描述性中文短语 + 计划内容指纹。

格式：``批准<动作摘要> <system>@<environment> <指纹8位>``

示例：``批准发布+健康检查 crypto-trader@test A3F9C2D1``

设计要点：
- 动作摘要来自计划步骤/动作类型，授权人一眼看出批的是什么（替代原纯随机短码）；
- 指纹 = sha256(digest) 前 8 位十六进制大写，绑定本次计划/动作内容——
  保留防跨计划复用能力：不同内容的计划即使动作与目标相同，短语也不同，
  恶意或出错的 Agent 无法把 A 计划的批准短语挪用到 B 计划上。
- 校验机制不变：明文只出现一次，数据库仍存加盐哈希（PBKDF2），一次性、
  15 分钟过期、绑定房间+事件。
"""
from __future__ import annotations

import hashlib

# 动作类型 → 中文动词（按 ExecutionPlanStep.action_type / AiActionApproval.action_type）
_ACTION_VERBS: dict[str, str] = {
    "RELEASE": "发布",
    "SERVICE_CONTROL": "服务控制",
    "HEALTH_CHECK": "健康检查",
    "FILE_UPLOAD": "上传制品",
    "ROLLBACK": "回滚",
    "DML": "SQL变更",
    "PACKAGE_CLEANUP": "包清理",
    "MATRIX_PULL": "拉取附件",
    "EXEC_REMOTE": "远程命令",
}

_MAX_VERBS = 3  # 动作过多时截断，保持短语可读

# 生产环境额外确认从句（第 4 层 strict_prod_confirmation 的强制内容）。
# 审批人必须把这一句与一次性短语写在同一条消息里；短语本身只证明"批准了这次
# 内容指纹"，多这一句才证明"清楚知道这是生产"。措辞固定、可背诵，因此单独
# 出现不构成确认，必须与当次短语同时出现。
PROD_CONFIRM_CLAUSE = "我确认生产操作"


def split_prod_confirmation(
    short_code: str, prod_confirm_text: str | None = None
) -> tuple[str, bool]:
    """拆分「一次性短语」与「生产额外确认从句」。

    返回 ``(用于哈希校验的短语, 从句是否满足)``。

    审批人可能把两句写在同一行照抄（回复模板就是这么给的），因此这里容忍
    "短语 + 从句"整行形式：剥掉尾部的从句后再校验短语本身。但**从句必须出现**，
    不能因为能剥掉就当没要求——那正是第 4 层的意义。
    """
    code = (short_code or "").strip()
    provided = (prod_confirm_text or "").strip()
    if not provided and code.endswith(PROD_CONFIRM_CLAUSE):
        provided = PROD_CONFIRM_CLAUSE
        code = code[: -len(PROD_CONFIRM_CLAUSE)].strip()
    return code, provided == PROD_CONFIRM_CLAUSE


def fingerprint_of(digest: str) -> str:
    """由 digest 派生 8 位十六进制大写指纹，供短语生成与校验对称复用。"""
    return hashlib.sha256((digest or "").encode("utf-8")).hexdigest()[:8].upper()


def build_approval_phrase(
    *,
    action_types: list[str] | tuple[str, ...] | None,
    system_name: str,
    environment: str,
    digest: str,
) -> str:
    """生成描述性审批确认短语。

    - action_types：计划步骤或单动作的类型列表（去重保序，最多展示 3 个）；
    - digest：plan_digest / action_digest（用于派生内容指纹）。
    """
    verbs: list[str] = []
    for action in action_types or []:
        verb = _ACTION_VERBS.get(str(action).strip().upper())
        if verb and verb not in verbs:
            verbs.append(verb)
        if len(verbs) >= _MAX_VERBS:
            break
    if not verbs:
        verbs = ["执行"]

    sys_name = (system_name or "").strip() or "-"
    env = (environment or "").strip() or "-"
    fingerprint = fingerprint_of(digest)
    return f"批准{'+'.join(verbs)} {sys_name}@{env} {fingerprint}"
