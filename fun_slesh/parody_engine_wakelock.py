# fun_slesh/parody_engine_wakelock.py
"""
Утилита для предотвращения сна Windows во время обучения.
Использует ctypes SetThreadExecutionState — без сторонних библиотек.
"""
import ctypes
import sys

# Флаги Windows
ES_CONTINUOUS        = 0x80000000
ES_SYSTEM_REQUIRED   = 0x00000001
ES_DISPLAY_REQUIRED  = 0x00000002  # экран не гасить

def prevent_sleep(keep_display: bool = False):
    """Запрещает Windows уходить в сон. Вызывать перед длинными задачами."""
    if sys.platform != "win32":
        return
    flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED
    if keep_display:
        flags |= ES_DISPLAY_REQUIRED
    ctypes.windll.kernel32.SetThreadExecutionState(flags)

def allow_sleep():
    """Снимает блокировку сна. Вызывать после завершения задачи."""
    if sys.platform != "win32":
        return
    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)


async def setup(bot):
    pass  # утилитный модуль, не Cog
