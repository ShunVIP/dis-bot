import json
import os
from pathlib import Path
from aiohttp import web

_RUNTIME_CONFIG_PATH = Path(__file__).resolve().parent.parent / ".model_bridge.runtime.json"


def _load_runtime_config() -> dict:
    if not _RUNTIME_CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


_runtime = _load_runtime_config()

MODEL_TOKEN = (os.environ.get("REMOTE_MODEL_API_TOKEN") or _runtime.get("token") or "").strip()
MODEL_HOST = (os.environ.get("REMOTE_MODEL_API_HOST") or _runtime.get("host") or "127.0.0.1").strip() or "127.0.0.1"
MODEL_PORT = int(os.environ.get("REMOTE_MODEL_API_PORT") or _runtime.get("port") or "8787")

if not MODEL_TOKEN:
    raise RuntimeError("REMOTE_MODEL_API_TOKEN is required")

_gpt_model_exists = None
_generate_neuro_phrase = None


def _load_gpt_functions():
    global _gpt_model_exists, _generate_neuro_phrase
    if _gpt_model_exists is None or _generate_neuro_phrase is None:
        from fun_slesh.parody_gpt import (
            generate_neuro_phrase,
            gpt_model_exists,
        )

        _gpt_model_exists = gpt_model_exists
        _generate_neuro_phrase = generate_neuro_phrase

    return _gpt_model_exists, _generate_neuro_phrase


def _is_authorized(request: web.Request) -> bool:
    return request.headers.get("X-Model-Token", "") == MODEL_TOKEN


async def health(_: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def model_exists(request: web.Request) -> web.Response:
    if not _is_authorized(request):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)

    data = await request.json()
    user_id = int(data["user_id"])
    gpt_model_exists, _ = _load_gpt_functions()
    return web.json_response({"ok": True, "exists": bool(gpt_model_exists(user_id))})


async def generate(request: web.Request) -> web.Response:
    if not _is_authorized(request):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)

    data = await request.json()
    user_id = int(data["user_id"])
    max_new_tokens = int(data.get("max_new_tokens", 80))
    _, generate_neuro_phrase = _load_gpt_functions()
    phrase = generate_neuro_phrase(user_id, max_new_tokens=max_new_tokens)
    return web.json_response({"ok": True, "phrase": phrase})


app = web.Application()
app.add_routes([
    web.get("/health", health),
    web.post("/model_exists", model_exists),
    web.post("/generate_neuro_phrase", generate),
])


if __name__ == "__main__":
    web.run_app(app, host=MODEL_HOST, port=MODEL_PORT)
