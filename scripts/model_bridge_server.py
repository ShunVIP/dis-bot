import os
from aiohttp import web

MODEL_TOKEN = os.environ.get("REMOTE_MODEL_API_TOKEN", "").strip()
MODEL_HOST = os.environ.get("REMOTE_MODEL_API_HOST", "127.0.0.1").strip() or "127.0.0.1"
MODEL_PORT = int(os.environ.get("REMOTE_MODEL_API_PORT", "8787"))

if not MODEL_TOKEN:
    raise RuntimeError("REMOTE_MODEL_API_TOKEN is required")

from fun_slesh.parody_gpt import (
    generate_neuro_phrase,
    gpt_model_exists,
)


def _is_authorized(request: web.Request) -> bool:
    return request.headers.get("X-Model-Token", "") == MODEL_TOKEN


async def health(_: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def model_exists(request: web.Request) -> web.Response:
    if not _is_authorized(request):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)

    data = await request.json()
    user_id = int(data["user_id"])
    return web.json_response({"ok": True, "exists": bool(gpt_model_exists(user_id))})


async def generate(request: web.Request) -> web.Response:
    if not _is_authorized(request):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)

    data = await request.json()
    user_id = int(data["user_id"])
    max_new_tokens = int(data.get("max_new_tokens", 80))
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
