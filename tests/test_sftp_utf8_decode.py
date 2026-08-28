"""Paramiko 4.0 严格 UTF-8 解码回归修复的回归测试。

背景：切换目录/SFTP 列目录报 `'utf-8' codec can't decode byte 0xe8 ...`。
根因：Paramiko 4.0 的 `util.u()` 改为 `s.decode('utf-8')` 严格模式，
远程目录含非 UTF-8（GBK/GB18030 中文）文件名时 `Message.get_text()` 直接抛
UnicodeDecodeError。app/api/sftp.py 在模块加载时对 `paramiko.message.u` 打
宽容解码补丁（errors='replace'），恢复旧版行为——有效 UTF-8 名保留、乱码名
显示为 �，不再整体报错。
"""
import pytest


def _make_sftp_messages_with_bytes(name_bytes: bytes):
    """构造一个 SFTP READDIR 响应，其中文件名含非 UTF-8 字节。"""
    from paramiko.message import Message

    # 文件名 + longname 两个 get_text() 字段
    msg = Message()
    msg.add_bytes(b"")       # 占位（get_string 内部结构）
    # 直接走真实服务解析路径：构造含 name 的 Message
    inner = Message()
    # name 字符串
    inner.add_string(name_bytes)
    inner.add_string(b"drwxr-xr-x 1 root root 0 Aug 25 10:00 " + name_bytes)
    inner.add_int(0o40755)   # st_mode
    inner.add_int(0)         # st_size
    inner.add_bytes(b"\x00" * 32)  # uid/gid/atime/file attrs
    return inner


def test_sftp_module_patch_loads_tolerant_decode():
    """app.api.sftp 导入后，paramiko.message.u 已是宽容解码。"""
    import importlib
    import app.api.sftp as sftp_mod
    assert sftp_mod._UTF8_REPLACEMENT_LOCK is True

    import paramiko.message as pm
    # 用 GBK 编码中文文件名字节（含多余孤立 0xe8 触发替替换，但不抛）
    gbk_name = "报表.txt".encode("gbk")
    # 直接验证补丁后的 u 不抛异常
    decoded = pm.u(gbk_name, encoding="utf8")
    assert "\ufffd" in decoded or decoded  # 宽容降级，绝不抛

    # 干净 UTF-8 中文名仍可正常解码
    utf8_name = "报表.txt".encode("utf-8")
    assert pm.u(utf8_name) == "报表.txt"


def test_sftp_module_patch_idempotent():
    """补丁重复调用不改变行为。"""
    import app.api.sftp as sftp_mod
    import paramiko.message as pm
    before = pm.u(b"\xe8", encoding="utf8")
    sftp_mod._apply_tolerant_utf8_decode_patch()
    after = pm.u(b"\xe8", encoding="utf8")
    assert before == after  # 都是替换字符，非异常


def test_message_get_text_does_not_raise_on_gbk_filename():
    """Paramiko Message.get_text 对非 UTF-8 文件名字节不再抛 UnicodeDecodeError。"""
    import importlib
    import app.api.sftp  # noqa: F401 — 触发补丁
    from paramiko.message import Message

    # 关键复现：真实 SFTP 响应里，文件名是 GBK 字节（0xe8 开头）
    gbk_name = "中文报表.txt".encode("gbk")
    inner = Message()
    inner.add_string(gbk_name)
    inner.add_string(b"longname-dummy")
    inner.add_int(0o40755)
    inner.add_int(0)
    inner.add_bytes(b"\x00" * 32)

    # 从响应字节重建并读取第一个 get_text()（文件名）
    raw = inner.asbytes()
    m2 = Message(raw)
    # 第一个字符串
    first = m2.get_string()
    # 若未打补丁，u(first) 会抛；打补丁后宽容解码为替换串
    import paramiko.message as pm
    name = pm.u(first)
    assert name == first.decode("utf-8", errors="replace")