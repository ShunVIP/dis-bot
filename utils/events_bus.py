# utils/events_bus.py
import asyncio
from collections import defaultdict
from typing import Awaitable, Callable, Dict, List

Subscriber = Callable[..., Awaitable[None]]
_subs: Dict[str, List[Subscriber]] = defaultdict(list)

def subscribe(event: str, handler: Subscriber) -> None:
    if handler not in _subs[event]:
        _subs[event].append(handler)

def unsubscribe(event: str, handler: Subscriber) -> None:
    if handler in _subs[event]:
        _subs[event].remove(handler)

async def emit(event: str, **payload) -> None:
    # Выполняем подписчиков последовательно, чтобы упростить работу с SQLite
    for handler in list(_subs.get(event, [])):
        try:
            await handler(**payload)
        except Exception as e:
            # не падаем, просто логируем в консоль
            print(f"[events_bus] handler error on '{event}': {e}")
