from .step import Step
from .steps import (
    CheckoutStep,
    BuildStep,
    UploadStep,
    DeployStep,
    SwitchStep,
    RestartStep,
    HealthCheckStep,
    RollbackStep,
    CommandStep,
    WaitStep,
    ScriptedServiceUpdateStep,
    WebScriptUpdateStep,
    DovoBlueGreenUpdateStep,
)

_registry = {
    "checkout": CheckoutStep,
    "build": BuildStep,
    "upload": UploadStep,
    "deploy": DeployStep,
    "switch": SwitchStep,
    "restart": RestartStep,
    "health_check": HealthCheckStep,
    "rollback": RollbackStep,
    "command": CommandStep,
    "wait": WaitStep,
    "scripted_service_update": ScriptedServiceUpdateStep,
    "web_script_update": WebScriptUpdateStep,
    "dovo_bluegreen_update": DovoBlueGreenUpdateStep,
}


class StepFactory:
    @staticmethod
    def get(step_type: str) -> Step:
        cls = _registry.get(step_type)
        if not cls:
            raise ValueError(f"Unknown step type: {step_type}")
        return cls()

    @staticmethod
    def register(step_type: str, cls: type):
        if not issubclass(cls, Step):
            raise TypeError("Must subclass Step")
        _registry[step_type] = cls

    @staticmethod
    def list_types() -> list:
        return list(_registry.keys())
