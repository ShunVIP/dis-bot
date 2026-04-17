# utils/logger.py
"""
Настройка логгера для бота.

Консоль : цветной вывод с уровнями INFO / WARNING / ERROR / DEBUG
bot.log  : полные логи с ротацией 5MB, хранение 14 дней, сжатие zip

Использование в любом модуле:
    from utils.logger import log
    log.info("Сообщение")
    log.warning("Предупреждение")
    log.error("Ошибка {}", err)
"""

import os
import sys
from loguru import logger as log

# ─── Пути ─────────────────────────────────────────────────────────────────────
_BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_LOG_FILE  = os.path.join(_BASE_DIR, "bot.log")

# ─── Форматы ──────────────────────────────────────────────────────────────────
_CONSOLE_FMT = (
    "<green>{time:HH:mm:ss}</green> "
    "<level>{level: <8}</level> "
    "<cyan>{extra[src]: <20}</cyan> "
    "{message}"
)

_FILE_FMT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} "
    "{level: <8} "
    "{extra[src]: <20} "
    "{message}"
)

# ─── Настройка ────────────────────────────────────────────────────────────────
log.remove()  # убираем дефолтный handler

# Консоль — INFO и выше
log.add(
    sys.stdout,
    format=_CONSOLE_FMT,
    level="INFO",
    colorize=True,
    enqueue=True,
)

# Файл — DEBUG и выше, ротация 5MB, 14 дней
log.add(
    _LOG_FILE,
    format=_FILE_FMT,
    level="DEBUG",
    rotation="5 MB",
    retention="14 days",
    compression="zip",
    encoding="utf-8",
    enqueue=True,
)

# Дефолтный контекст
log = log.bind(src="bot")


# ─── Перехват стандартного logging → loguru ───────────────────────────────────
# Все логи discord.py, apscheduler и других библиотек тоже пойдут в bot.log
import logging

class _InterceptHandler(logging.Handler):
    _LEVEL_MAP = {
        logging.DEBUG:    "DEBUG",
        logging.INFO:     "INFO",
        logging.WARNING:  "WARNING",
        logging.ERROR:    "ERROR",
        logging.CRITICAL: "CRITICAL",
    }

    def emit(self, record: logging.LogRecord):
        level = self._LEVEL_MAP.get(record.levelno, "INFO")
        src   = record.name[:20]
        from loguru import logger
        logger.bind(src=src).opt(depth=6, exception=record.exc_info).log(level, record.getMessage())

# Перехватываем все библиотеки
for _name in ("discord", "discord.gateway", "discord.client",
              "apscheduler", "asyncio"):
    _lib_log = logging.getLogger(_name)
    _lib_log.handlers  = [_InterceptHandler()]
    _lib_log.propagate = False
