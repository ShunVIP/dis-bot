from loguru import logger as log
log.add("bot.log", rotation="1 MB", compression="zip", enqueue=True)