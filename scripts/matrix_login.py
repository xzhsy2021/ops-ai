"""Matrix 专用设备登录：为 OPS 创建独立 Bot 设备并更新 .env。

用途（方案 A）：在不影响 qclaw 现有 E2EE 设备的前提下，用账号密码为 OPS
登录一个全新设备，拿到专属 access token 后自动写入 .env 并开启 E2EE。

用法：
  # 方式一（推荐，密码不落盘不留痕）——交互式输入密码：
  python scripts\\matrix_login.py --interactive

  # 方式二：经环境变量传密码（CI 可用）
  $env:MATRIX_LOGIN_PASSWORD='...' ; python scripts\\matrix_login.py

行为：
1. POST /_matrix/client/v3/login（m.login.password，device_id 固定 OPS-AI-BOT，
   initial_device_display_name "OPS-AI Command Center"）。
2. 校验返回设备 != qclaw 现用设备（XHBNAXVZFQ），whoami 复核 token 有效。
3. 原子更新 .env：写入新 token / device_id、开启 MATRIX_E2EE_ENABLED=true，
   并把旧 qclaw token 行注释保留作回溯。
4. 不打印明文密码；token 仅在 .env 与摘要中部分打码显示。

安全说明：新设备首次同步会生成并上传自己的 Olm 身份密钥；房间内其他成员
的客户端会看到"新设备登录"提示。历史加密消息的 room key 不会补发 —— 只能
解密设备创建之后新发送的消息/部署包。
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
import time
from pathlib import Path

import httpx

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

QCLAW_DEVICE_ID = "XHBNAXVZFQ"  # qclaw 在用设备，禁止复用
DEFAULT_DEVICE_ID = "OPS-AI-BOT"
DEFAULT_DEVICE_NAME = "OPS-AI Command Center"


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"')
    return values


def _mask(value: str) -> str:
    if len(value) <= 10:
        return "***"
    return f"{value[:8]}…{value[-4:]}"


def _update_env(path: Path, updates: dict[str, str], retire_keys: tuple[str, ...] = ()) -> None:
    """就地更新 .env：同 key 替换值；retire_keys 改写为注释保留行。"""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen: set[str] = set()

    def _render(key: str, value: str) -> str:
        return f"{key}={value}"

    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        matched_update = None
        matched_retire = None
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in retire_keys:
                # 退役优先：旧值以注释保留（如 qclaw 原 token），便于回溯/回滚
                matched_retire = key
            elif key in updates:
                matched_update = key
        if matched_update:
            value = updates[matched_update]
            out.append(_render(matched_update, value))
            seen.add(matched_update)
        elif matched_retire:
            out.append(f"# [retired by matrix_login.py {time.strftime('%Y-%m-%d %H:%M')}] {line}")
        else:
            out.append(line)

    stamp = time.strftime("%Y-%m-%d %H:%M")
    additions = [f"", f"# ── 由 scripts/matrix_login.py 于 {stamp} 写入 ──"]
    for key, value in updates.items():
        if key not in seen:
            additions.append(_render(key, value))
    out.extend(additions)

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Matrix 专用设备登录（OPS E2EE 接入方案 A）")
    parser.add_argument("--homeserver", default="", help="默认读 .env 的 MATRIX_HOMESERVER_URL")
    parser.add_argument("--user", default="", help="默认读 .env 的 MATRIX_USER_ID")
    parser.add_argument("--device-id", default=DEFAULT_DEVICE_ID, help=f"默认 {DEFAULT_DEVICE_ID}")
    parser.add_argument("--device-name", default=DEFAULT_DEVICE_NAME)
    parser.add_argument("--interactive", action="store_true", help="交互式输入密码（推荐）")
    parser.add_argument(
        "--env-file",
        default=str(ROOT_DIR / ".env"),
        help=".env 路径（默认仓库根 .env）",
    )
    args = parser.parse_args()

    env_path = Path(args.env_file)
    env_vals = _load_env_file(env_path)
    homeserver = (args.homeserver or env_vals.get("MATRIX_HOMESERVER_URL") or "").rstrip("/")
    user_id = args.user or env_vals.get("MATRIX_USER_ID") or ""
    if not homeserver or not user_id:
        print("[x] 缺少 homeserver/user：请确认 .env 已配置 MATRIX_HOMESERVER_URL 与 MATRIX_USER_ID")
        return 2

    # 密码来源优先级：环境变量 > --interactive 显式提示（避免在管道环境意外阻塞）
    password = os.environ.get("MATRIX_LOGIN_PASSWORD", "")
    if not password:
        if not args.interactive:
            print("[x] 未提供密码：设置 MATRIX_LOGIN_PASSWORD 或使用 --interactive")
            return 2
        password = getpass.getpass(f"Password for {user_id}: ")
    if not password:
        print("[x] 密码为空，已取消")
        return 2

    url = f"{homeserver}/_matrix/client/v3/login"
    payload = {
        "type": "m.login.password",
        "identifier": {"type": "m.id.user", "user": user_id},
        "password": password,
        "device_id": args.device_id,
        "initial_device_display_name": args.device_name,
    }
    try:
        resp = httpx.post(url, json=payload, timeout=20)
    except httpx.HTTPError as exc:
        print(f"[x] 登录请求失败：{exc}")
        return 1

    data = {}
    try:
        data = resp.json() or {}
    except ValueError:
        pass
    if resp.status_code >= 400:
        errcode = data.get("errcode") or resp.status_code
        hint = {
            "M_FORBIDDEN": "用户名或密码错误",
            "M_USER_DEACTIVATED": "账号已被停用",
            "M_LIMIT_EXCEEDED": "尝试过于频繁，稍后再试",
        }.get(str(errcode), str(data.get("error") or ""))
        print(f"[x] 登录失败 [{errcode}] {hint}")
        return 1

    got_user = data.get("user_id") or ""
    got_token = data.get("access_token") or ""
    got_device = data.get("device_id") or ""
    if not (got_user == user_id and got_token and got_device):
        print(f"[x] 登录响应异常：user_id={got_user!r} device={got_device!r}")
        return 1
    if got_device == QCLAW_DEVICE_ID:
        print(f"[x] 安全守卫：服务端返回了 qclaw 在用设备 {QCLAW_DEVICE_ID}，拒绝写入")
        return 1

    # whoami 复核
    try:
        who = httpx.get(
            f"{homeserver}/_matrix/client/v3/account/whoami",
            headers={"Authorization": f"Bearer {got_token}"},
            timeout=15,
        ).json()
    except Exception as exc:  # noqa: BLE001
        print(f"[!] whoami 校验失败（继续但请人工确认）：{exc}")
        who = {}
    if who.get("device_id") and who["device_id"] != got_device:
        print(f"[x] whoami 设备不一致（{who.get('device_id')} != {got_device}），拒绝写入")
        return 1

    # 更新 .env：旧 qclaw token 行退役注释，写新凭据并开启 E2EE
    _update_env(
        env_path,
        updates={
            "MATRIX_ACCESS_TOKEN": got_token,
            "MATRIX_DEVICE_ID": got_device,
            "MATRIX_E2EE_ENABLED": "true",
        },
        retire_keys=("MATRIX_ACCESS_TOKEN",),
    )

    print("")
    print("[OK] 专用设备登录成功")
    print(f"     homeserver : {homeserver}")
    print(f"     user       : {got_user}")
    print(f"     device_id  : {got_device}")
    print(f"     token      : {_mask(got_token)}")
    print(f"     .env       : {env_path} 已更新（E2EE 已开启）")
    print("")
    print("下一步：重启 OPS 服务后，访问 GET /api/v2/matrix/status 检查 data.e2ee；")
    print("此后新发送到加密房间的部署包即可被解密（历史消息的密钥不会补发）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
