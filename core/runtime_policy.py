import os
import socket


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


HOSTNAME = socket.gethostname()
IS_WINDOWS = os.name == "nt"
IS_SERVER_RUNTIME = _env_flag("BOT_SERVER_MODE", default=not IS_WINDOWS)

ALLOW_GPT_TRAINING_ON_SERVER = _env_flag("ALLOW_GPT_TRAINING_ON_SERVER", default=False)
ALLOW_FULL_MAINTENANCE_ON_SERVER = _env_flag("ALLOW_FULL_MAINTENANCE_ON_SERVER", default=False)
ALLOW_REMOTE_MODEL_INFERENCE = _env_flag("ALLOW_REMOTE_MODEL_INFERENCE", default=True)

WEB_ADMIN_ENABLED = _env_flag("WEB_ADMIN_ENABLED", default=False)
WEB_ADMIN_HOST = os.getenv("WEB_ADMIN_HOST", "127.0.0.1").strip() or "127.0.0.1"
WEB_ADMIN_PORT = int(os.getenv("WEB_ADMIN_PORT", "8080"))
WEB_ADMIN_TOKEN = os.getenv("WEB_ADMIN_TOKEN", "").strip()
WEB_ADMIN_TITLE = os.getenv("WEB_ADMIN_TITLE", "ViPik Bot Control").strip() or "ViPik Bot Control"

_runtime_state = {
    "remote_model_inference": ALLOW_REMOTE_MODEL_INFERENCE,
}


def is_gpt_training_allowed() -> bool:
    return (not IS_SERVER_RUNTIME) or ALLOW_GPT_TRAINING_ON_SERVER


def is_full_maintenance_allowed() -> bool:
    return (not IS_SERVER_RUNTIME) or ALLOW_FULL_MAINTENANCE_ON_SERVER


def is_remote_model_inference_enabled() -> bool:
    return bool(_runtime_state["remote_model_inference"])


def set_remote_model_inference_enabled(enabled: bool) -> None:
    _runtime_state["remote_model_inference"] = bool(enabled)


def policy_summary() -> dict:
    return {
        "hostname": HOSTNAME,
        "is_server_runtime": IS_SERVER_RUNTIME,
        "gpt_training_allowed": is_gpt_training_allowed(),
        "full_maintenance_allowed": is_full_maintenance_allowed(),
        "remote_model_inference_enabled": is_remote_model_inference_enabled(),
        "web_admin_enabled": WEB_ADMIN_ENABLED,
        "web_admin_host": WEB_ADMIN_HOST,
        "web_admin_port": WEB_ADMIN_PORT,
    }
