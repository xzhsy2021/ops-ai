from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import System, SystemEnvironment, TemporaryApprovalGrant
from app.services.message_context import MessageContext
from app.services.temporary_approval import (
    TEMPORARY_SELF_APPROVAL_ACTIONS,
    TemporaryApprovalService,
)


def _context(*, channel="wechat", account="primary", conversation="room-1", message="message-1", sender="requester"):
    return MessageContext(
        channel=channel,
        channel_account_id=account,
        conversation_id=conversation,
        message_id=message,
        sender_id=sender,
        content_sha256="a" * 64,
    )


def _db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'temporary-approval.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as session:
        session.add(System(
            name="crypto-trader",
            display_name="Crypto Trader",
            message_routing={
                "approvers": [{
                    "channel": "wechat",
                    "channel_account_id": "primary",
                    "sender_id": "owner",
                }],
            },
        ))
        session.add_all([
            SystemEnvironment(system_name="crypto-trader", name="test", category="test"),
            SystemEnvironment(system_name="crypto-trader", name="prod", category="prod"),
        ])
        session.commit()
    return engine


def _service(tmp_path):
    engine = _db(tmp_path)
    Session = sessionmaker(bind=engine)
    return engine, Session(), TemporaryApprovalService


def _request(service, context=None, **overrides):
    context = context or _context()
    values = dict(
        message_context=context,
        beneficiary_actor_key=context.actor_key,
        system_name="crypto-trader",
        environment_name="test",
        allowed_actions=["FILE_UPLOAD", "RELEASE", "SERVICE_CONTROL", "HEALTH_CHECK"],
        reason="urgent test integration",
        authorized_identities=[
            {"channel": context.channel, "channel_account_id": context.channel_account_id, "sender_id": "owner"}
        ],
    )
    values.update(overrides)
    return service.request(**values)


def test_request_supports_day_week_and_default_duration_and_rejects_prod(tmp_path):
    _, session, service_cls = _service(tmp_path)
    service = service_cls(session)

    default_grant, default_code = _request(service)
    assert default_grant.requested_duration_seconds == 24 * 60 * 60
    assert len(default_code) == 8

    day_grant, _ = _request(service, _context(message="day"), duration_value=2, duration_unit="day")
    assert day_grant.requested_duration_seconds == 2 * 24 * 60 * 60
    week_grant, _ = _request(service, _context(message="week"), duration_value=2, duration_unit="week")
    assert week_grant.requested_duration_seconds == 2 * 7 * 24 * 60 * 60

    with pytest.raises(ValueError, match="4 weeks"):
        _request(service, _context(message="too-long"), duration_value=5, duration_unit="week")
    with pytest.raises(ValueError, match="test environment"):
        _request(service, _context(message="prod"), environment_name="prod")


def test_confirm_requires_original_approver_and_same_conversation_and_is_one_time(tmp_path):
    _, session, service_cls = _service(tmp_path)
    service = service_cls(session)
    request_context = _context()
    grant, code = _request(service, request_context)

    cross_room = _context(conversation="other-room", message="confirm-cross", sender="owner")
    assert service.confirm(grant.id, code, actor_key=cross_room.actor_key, message_context=cross_room) is None
    stored = session.query(TemporaryApprovalGrant).filter(TemporaryApprovalGrant.id == grant.id).one()
    assert stored.status == "PENDING"

    unauthorized = _context(message="confirm-unauthorized", sender="mallory")
    assert service.confirm(grant.id, code, actor_key=unauthorized.actor_key, message_context=unauthorized) is None
    stored = session.query(TemporaryApprovalGrant).filter(TemporaryApprovalGrant.id == grant.id).one()
    assert stored.status == "PENDING"

    confirmation = _context(message="confirm-1", sender="owner")
    active = service.confirm(grant.id, code, actor_key=confirmation.actor_key, message_context=confirmation)
    assert active is not None
    assert active.status == "ACTIVE"
    assert active.approved_by_actor_key == "wechat:primary:owner"
    assert active.confirmation_message_id == "confirm-1"
    assert active.starts_at is not None
    assert active.expires_at > active.starts_at
    assert not hasattr(active, "confirmation_code_hash")

    assert service.confirm(grant.id, code, actor_key=confirmation.actor_key, message_context=confirmation) is None


def test_request_cannot_override_configured_approver_policy(tmp_path):
    _, session, service_cls = _service(tmp_path)
    service = service_cls(session)
    with pytest.raises(ValueError, match="configured policy"):
        _request(
            service,
            _context(message="forged-approver"),
            authorized_identities=[
                {"channel": "wechat", "channel_account_id": "primary", "sender_id": "mallory"}
            ],
        )


def test_confirmation_code_attempt_limit_and_environment_recheck(tmp_path):
    _, session, service_cls = _service(tmp_path)
    service = service_cls(session)
    grant, _ = _request(service, _context(message="attempts"))
    confirmation = _context(message="confirm-attempts", sender="owner")
    for _ in range(5):
        assert service.confirm(grant.id, "BADCODE", actor_key=confirmation.actor_key, message_context=confirmation) is None
    stored = session.query(TemporaryApprovalGrant).filter(TemporaryApprovalGrant.id == grant.id).one()
    assert stored.status == "EXPIRED"
    assert stored.confirmation_attempts == 5

    grant, code = _request(service, _context(message="environment-recheck"))
    environment = session.query(SystemEnvironment).filter(
        SystemEnvironment.system_name == "crypto-trader",
        SystemEnvironment.name == "test",
    ).one()
    environment.category = "prod"
    session.commit()
    assert service.confirm(grant.id, code, actor_key=confirmation.actor_key, message_context=confirmation) is None
    stored = session.query(TemporaryApprovalGrant).filter(TemporaryApprovalGrant.id == grant.id).one()
    assert stored.status == "PENDING"


def test_confirmation_attempt_limit_survives_stale_sessions(tmp_path):
    engine, session, service_cls = _service(tmp_path)
    Session = sessionmaker(bind=engine)
    service = service_cls(session)
    grant, _ = _request(service, _context(message="stale-attempts"))
    confirmation = _context(message="confirm-stale", sender="owner")

    stale_sessions = [Session() for _ in range(6)]
    services = [service_cls(item) for item in stale_sessions]
    for item in stale_sessions:
        item.query(TemporaryApprovalGrant).filter(TemporaryApprovalGrant.id == grant.id).one()

    for item in services:
        assert item.confirm(
            grant.id,
            "BADCODE",
            actor_key=confirmation.actor_key,
            message_context=confirmation,
        ) is None

    fresh = Session()
    stored = fresh.query(TemporaryApprovalGrant).filter(TemporaryApprovalGrant.id == grant.id).one()
    assert stored.confirmation_attempts == 5
    assert stored.status == "EXPIRED"


def test_pending_requests_can_exist_but_only_one_can_become_active(tmp_path):
    _, session, service_cls = _service(tmp_path)
    service = service_cls(session)
    first, first_code = _request(service, _context(message="pending-1"))
    second, second_code = _request(service, _context(message="pending-2"))
    assert first.id != second.id

    confirmation = _context(message="confirm-1", sender="owner")
    assert service.confirm(first.id, first_code, actor_key=confirmation.actor_key, message_context=confirmation) is not None
    assert service.confirm(second.id, second_code, actor_key=confirmation.actor_key, message_context=confirmation) is None
    stored = session.query(TemporaryApprovalGrant).filter(TemporaryApprovalGrant.id == second.id).one()
    assert stored.status == "PENDING"


def test_list_returns_views_without_confirmation_hash(tmp_path):
    _, session, service_cls = _service(tmp_path)
    service = service_cls(session)
    _request(service, _context(message="list"))
    item = service.list()[0]
    assert "confirmation_code_hash" not in item.to_dict()


def test_active_grant_is_scoped_to_fixed_beneficiary_and_allowed_actions(tmp_path):
    _, session, service_cls = _service(tmp_path)
    service = service_cls(session)
    request_context = _context(sender="user-a")
    grant, code = _request(service, request_context)
    confirmation = _context(message="confirm-1", sender="owner")
    service.confirm(grant.id, code, actor_key=confirmation.actor_key, message_context=confirmation)

    assert not service.is_self_approval_allowed(
        actor_key="wechat:primary:user-a",
        system_name="crypto-trader",
        environment_name="test",
        action_types=["SERVICE_CONTROL"],
        message_context=_context(sender="user-b"),
    )

    assert service.is_self_approval_allowed(
        actor_key="wechat:primary:user-a",
        system_name="crypto-trader",
        environment_name="test",
        action_types=["FILE_UPLOAD", "SERVICE_CONTROL", "HEALTH_CHECK"],
        message_context=request_context,
    )

    assert not service.is_self_approval_allowed(
        actor_key="wechat:primary:user-b",
        system_name="crypto-trader",
        environment_name="test",
        action_types=["SERVICE_CONTROL"],
        message_context=request_context,
    )
    assert not service.is_self_approval_allowed(
        actor_key="wechat:primary:user-a",
        system_name="crypto-trader",
        environment_name="test",
        action_types=["DML"],
        message_context=request_context,
    )

    assert not service.is_self_approval_allowed(
        actor_key="wechat:primary:user-a",
        system_name="crypto-trader",
        environment_name="prod",
        action_types=["SERVICE_CONTROL"],
        message_context=request_context,
    )


def test_duplicate_active_scope_does_not_stack_and_revoke_restores_original_policy(tmp_path):
    _, session, service_cls = _service(tmp_path)
    service = service_cls(session)
    request_context = _context(sender="user-a")
    grant, code = _request(service, request_context)
    confirmation = _context(message="confirm-1", sender="owner")
    service.confirm(grant.id, code, actor_key=confirmation.actor_key, message_context=confirmation)

    duplicate, duplicate_code = _request(service, _context(message="duplicate", sender="user-a"))
    assert duplicate.id == grant.id
    assert duplicate_code == ""

    cross_channel = _context(channel="telegram", account="primary", message="revoke-cross", sender="owner")
    assert service.revoke(grant.id, actor_key=cross_channel.actor_key, message_context=cross_channel, reason="wrong room") is None
    stored = session.query(TemporaryApprovalGrant).filter(TemporaryApprovalGrant.id == grant.id).one()
    assert stored.status == "ACTIVE"

    revoked = service.revoke(grant.id, actor_key=confirmation.actor_key, message_context=confirmation, reason="normal service resumed")
    assert revoked.status == "REVOKED"
    assert not service.is_self_approval_allowed(
        actor_key="wechat:primary:user-a",
        system_name="crypto-trader",
        environment_name="test",
        action_types=["RELEASE"],
        message_context=request_context,
    )

    replacement, replacement_code = _request(service, _context(message="replacement", sender="user-a"))
    assert replacement.id != grant.id
    replacement_active = service.confirm(
        replacement.id,
        replacement_code,
        actor_key=confirmation.actor_key,
        message_context=confirmation,
    )
    assert replacement_active is not None
    assert replacement_active.status == "ACTIVE"


def test_expired_active_grant_is_not_returned_without_background_worker(tmp_path):
    _, session, service_cls = _service(tmp_path)
    service = service_cls(session)
    request_context = _context(sender="user-a")
    grant, code = _request(service, request_context)
    confirmation = _context(message="confirm-1", sender="owner")
    service.confirm(grant.id, code, actor_key=confirmation.actor_key, message_context=confirmation)
    active = session.query(TemporaryApprovalGrant).filter(TemporaryApprovalGrant.id == grant.id).one()
    active.expires_at = active.starts_at - timedelta(seconds=1)
    session.commit()

    assert service.get_active_grant(
        actor_key="wechat:primary:user-a",
        system_name="crypto-trader",
        environment_name="test",
        message_context=request_context,
    ) is None
    session.refresh(active)
    assert active.status == "EXPIRED"

    replacement, _ = _request(service, _context(message="after-expiry", sender="user-a"))
    assert replacement.id != grant.id


def test_allowlist_is_fixed_and_cannot_be_empty_or_include_forbidden_actions(tmp_path):
    _, session, service_cls = _service(tmp_path)
    service = service_cls(session)
    assert TEMPORARY_SELF_APPROVAL_ACTIONS == {"FILE_UPLOAD", "RELEASE", "SERVICE_CONTROL", "HEALTH_CHECK"}
    with pytest.raises(ValueError, match="allowed actions"):
        _request(service, _context(message="empty"), allowed_actions=[])
    with pytest.raises(ValueError, match="not allowed"):
        _request(service, _context(message="rollback"), allowed_actions=["ROLLBACK"])
