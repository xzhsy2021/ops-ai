import copy
import logging

from app.config.default_seed import DEFAULT_CONFIG_SEED

logger = logging.getLogger(__name__)


def _load_default_config() -> dict:
    """Return the bootstrap default configuration from code seed.

    Runtime configuration is stored in the database table ``config_kv``.
    The legacy ``config/default_config.json`` file is no longer read as the
    default source. If that file exists on an upgraded installation, startup
    migration imports it into the database once and moves it out of ``config/``.
    """
    return copy.deepcopy(DEFAULT_CONFIG_SEED)


DEFAULT_CONFIG = _load_default_config()
