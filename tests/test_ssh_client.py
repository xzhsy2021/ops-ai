import pytest


class _DummyTransport:
    def is_active(self):
        return True


class _DummyParamikoClient:
    def get_transport(self):
        return _DummyTransport()


class _DummyPKey:
    pass


def _patch_connect(monkeypatch):
    import ssh_client

    def fake_connect(self, *args, **kwargs):
        self._client = _DummyParamikoClient()

    monkeypatch.setattr(ssh_client.SSHClient, "connect", fake_connect)
    return ssh_client


def test_has_auth_helper():
    import ssh_client

    assert ssh_client._has_auth({}) is False
    assert ssh_client._has_auth({"key": "~/.ssh/id_rsa"}) is True
    assert ssh_client._has_auth({"key_file": "/path/to/key"}) is True
    assert ssh_client._has_auth({"key_content": "BEGIN OPENSSH PRIVATE KEY"}) is True
    assert ssh_client._has_auth({"password": "secret"}) is True
    assert ssh_client._has_auth({"password": ""}) is False
    assert ssh_client._has_auth({"key": None, "password": "x"}) is True


def test_build_auth_kwargs_priority(monkeypatch):
    import ssh_client

    dummy_key = _DummyPKey()
    monkeypatch.setattr(
        ssh_client.SSHClient,
        "_load_key_from_content",
        lambda self, content: dummy_key,
    )

    # key_content 优先级最高
    client = ssh_client.SSHClient(
        "h", 22, "u", key="key.pem", password="pwd", key_content="KEY_CONTENT"
    )
    kwargs = client._build_auth_kwargs()
    assert kwargs == {"pkey": dummy_key}

    # 其次 pkey
    client2 = ssh_client.SSHClient("h", 22, "u", key="key.pem", password="pwd")
    kwargs2 = client2._build_auth_kwargs(dummy_key)
    assert kwargs2 == {"pkey": dummy_key}

    # 最后 password
    client3 = ssh_client.SSHClient("h", 22, "u", password="pwd")
    kwargs3 = client3._build_auth_kwargs()
    assert kwargs3 == {"password": "pwd"}


def test_build_auth_kwargs_raises_when_no_auth():
    import ssh_client

    client = ssh_client.SSHClient("h", 22, "u")
    with pytest.raises(Exception):
        client._build_auth_kwargs()


def test_build_hop_auth_kwargs_raises_on_missing_creds():
    import ssh_client

    client = ssh_client.SSHClient("target", 22, "u")
    with pytest.raises(ConnectionError) as exc_info:
        client._build_hop_auth_kwargs({
            "host": "10.0.0.1",
            "port": 22,
            "user": "root",
        })
    assert "缺少认证凭据" in str(exc_info.value)


def test_ssh_pool_uses_stable_pool_key(monkeypatch):
    ssh_client = _patch_connect(monkeypatch)

    pool = ssh_client.SSHConnectionPool(max_idle_time=300)
    cfg = {"host": "127.0.0.1", "port": 2222, "user": "ops", "password": "secret"}

    client = pool.get(cfg)

    assert client is pool.get(cfg)
    assert "ops@127.0.0.1:2222:direct" in pool._pool
    assert "secret" not in pool._pool
    assert None not in pool._pool


def test_ssh_pool_resolves_jump_host_from_db_ssot(monkeypatch):
    """字符串型 jump_host 在旧 config_kv 查不到时，应回退到 jump_hosts DB 表。"""
    ssh_client = _patch_connect(monkeypatch)
    from app.config import servers as app_servers

    monkeypatch.setattr(
        "config_manager.load_config_cached",
        lambda: {"jump_hosts": [], "servers": []},
    )
    monkeypatch.setattr(
        app_servers,
        "get_jump_host_by_name",
        lambda name: {
            "name": name,
            "host": "1.2.3.4",
            "port": 22,
            "user": "bastion",
            "key": "~/.ssh/id_rsa",
            "password": "bastion_pwd",
        } if name == "new_bastion" else None,
    )

    pool = ssh_client.SSHConnectionPool(max_idle_time=300)
    cfg = {
        "host": "47.84.142.156",
        "port": 22,
        "user": "root",
        "password": "target_pwd",
        "jump_host": "new_bastion",
    }

    client = pool.get(cfg)

    assert client is not None
    pool_key = next(iter(pool._pool.keys()), "")
    assert "root@47.84.142.156:22" in pool_key
    assert "bastion@1.2.3.4:22" in pool_key
    assert pool_key != "root@47.84.142.156:22:"
    for k in pool._pool.keys():
        assert "bastion_pwd" not in k
        assert "target_pwd" not in k


def test_ssh_pool_falls_back_to_direct_when_db_jump_lacks_creds(monkeypatch):
    """DB 里同名跳板机存在但没有任何凭据时，应回退直连（兼容老配置）。"""
    ssh_client = _patch_connect(monkeypatch)
    from app.config import servers as app_servers

    monkeypatch.setattr(
        "config_manager.load_config_cached",
        lambda: {"jump_hosts": [], "servers": []},
    )
    monkeypatch.setattr(
        app_servers,
        "get_jump_host_by_name",
        lambda name: {
            "name": name, "host": "10.0.0.1", "port": 22, "user": "root",
        } if name == "old_jump" else None,
    )

    pool = ssh_client.SSHConnectionPool(max_idle_time=300)
    cfg = {
        "host": "8.219.71.126",
        "port": 22,
        "user": "root",
        "password": "real_target_pwd",
        "name": "8.219.71.126-推广-bacteria",
        "jump_host": "old_jump",
    }

    client = pool.get(cfg)

    pool_key = next(iter(pool._pool.keys()), "")
    assert pool_key == "root@8.219.71.126:22:direct"
    assert client.jump_hosts == []


def test_ssh_pool_resolves_dict_jump_without_auth_from_config_kv(monkeypatch):
    """dict 类型 jump 缺少凭据时，应从 config_kv 同名跳板机补齐。"""
    ssh_client = _patch_connect(monkeypatch)

    monkeypatch.setattr(
        "config_manager.load_config_cached",
        lambda: {
            "jump_hosts": [
                {
                    "name": "bastion",
                    "host": "1.2.3.4",
                    "port": 22,
                    "user": "ops",
                    "password": "bastion_pwd",
                }
            ],
            "servers": [],
        },
    )

    pool = ssh_client.SSHConnectionPool(max_idle_time=300)
    cfg = {
        "host": "target",
        "port": 22,
        "user": "root",
        "password": "target_pwd",
        # dict 类型 jump 已声明 host/user，仅缺少凭据，应从 config_kv 补齐
        "jump_host": {"name": "bastion", "host": "1.2.3.4", "port": 22, "user": "ops"},
    }

    client = pool.get(cfg)

    pool_key = next(iter(pool._pool.keys()), "")
    assert "ops@1.2.3.4:22" in pool_key
    assert client.jump_hosts
    assert client.jump_hosts[0].get("password") == "bastion_pwd"


def test_ssh_pool_rejects_missing_target_auth(monkeypatch):
    """目标服务器没有任何认证凭据时，应提前抛出 ConnectionError。"""
    ssh_client = _patch_connect(monkeypatch)

    pool = ssh_client.SSHConnectionPool(max_idle_time=300)
    cfg = {"host": "target", "port": 22, "user": "root"}

    with pytest.raises(ConnectionError) as exc_info:
        pool.get(cfg)
    assert "缺少认证凭据" in str(exc_info.value)


def test_create_ssh_client_forwards_connect_options(monkeypatch):
    """create_ssh_client 应将 max_retries/retry_delay/per_attempt_timeout 透传到 connect()。"""
    ssh_client = _patch_connect(monkeypatch)

    captured = {}
    original_connect = ssh_client.SSHClient.connect

    def fake_connect(self, max_retries=3, retry_delay=2.0, per_attempt_timeout=None):
        captured["max_retries"] = max_retries
        captured["retry_delay"] = retry_delay
        captured["per_attempt_timeout"] = per_attempt_timeout
        self._client = _DummyParamikoClient()

    monkeypatch.setattr(ssh_client.SSHClient, "connect", fake_connect)

    client = ssh_client.create_ssh_client(
        {"host": "target", "port": 22, "user": "root", "password": "pwd"},
        max_retries=1,
        retry_delay=0.5,
        per_attempt_timeout=5,
    )

    assert client is not None
    assert captured["max_retries"] == 1
    assert captured["retry_delay"] == 0.5
    assert captured["per_attempt_timeout"] == 5
