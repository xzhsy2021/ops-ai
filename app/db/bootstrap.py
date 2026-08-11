"""Persist domain defaults for fresh or partially upgraded databases."""
from sqlalchemy.orm import Session

from app.db.repository import (
    CapabilitySettingsRepository,
    DeploymentDefaultsRepository,
    NotificationSettingsRepository,
    RetentionPolicyRepository,
)


DEFAULT_NOTIFICATION_SETTINGS = {
    "enabled": False,
    "webhook_urls": [],
    "events": [
        "deploy.success",
        "deploy.failed",
        "deploy.canceled",
        "rollback.success",
        "rollback.failed",
    ],
    "timeout": 5,
}


def ensure_domain_defaults(db: Session) -> None:
    from app.services.package_retention import DEFAULT_PACKAGE_RETENTION
    from app.services.release_retention import DEFAULT_RETENTION
    from app.services.runtime_resources import DEFAULT_RUNTIME_RETENTION
    from app.services.tool_policy import DEFAULT_CAPABILITY_SETTINGS

    capability_repo = CapabilitySettingsRepository(db)
    if capability_repo.get() is None:
        capability_repo.set(dict(DEFAULT_CAPABILITY_SETTINGS))

    retention_repo = RetentionPolicyRepository(db)
    for name, defaults in (
        ("release", DEFAULT_RETENTION),
        ("package", DEFAULT_PACKAGE_RETENTION),
        ("runtime", DEFAULT_RUNTIME_RETENTION),
    ):
        if retention_repo.get(name) is None:
            retention_repo.set(name, dict(defaults))

    notification_repo = NotificationSettingsRepository(db)
    if notification_repo.get() is None:
        notification_repo.set(dict(DEFAULT_NOTIFICATION_SETTINGS))

    deployment_repo = DeploymentDefaultsRepository(db)
    if deployment_repo.get() is None:
        deployment_repo.set({})

    db.commit()
