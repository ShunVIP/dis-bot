# main_file.py
import os
import sys
import time
import discord
from discord.ext import commands
from config import TOKEN
from core.admin_panel import start_admin_panel

# ─── Логгер ───────────────────────────────────────────────────────────────────
from utils.logger import log as _base_log
log = _base_log.bind(src="main")

# ─── Интенты ──────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.members         = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ─── Баннер при старте ────────────────────────────────────────────────────────
def _print_banner():
    w = 52
    border = "═" * w
    print(f"\n  ╔{border}╗")
    print(f"  ║{'ViPik Discord Bot':^{w}}║")
    print(f"  ║{'Запуск...':^{w}}║")
    print(f"  ╚{border}╝\n")

# ─── Загрузка модулей ─────────────────────────────────────────────────────────
async def load_slash_modules():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    folder   = os.path.join(base_dir, "fun_slesh")

    files = sorted(f for f in os.listdir(folder)
                   if f.endswith(".py")
                   and f != "__init__.py"
                   and not f.startswith("_"))

    total   = len(files)
    ok      = 0
    failed  = []

    log.info("━" * 44)
    log.info(f"Загрузка модулей ({total} файлов)...")
    log.info("━" * 44)

    t0 = time.perf_counter()
    for filename in files:
        ext = f"fun_slesh.{filename[:-3]}"
        t1  = time.perf_counter()
        try:
            await bot.load_extension(ext)
            ms = int((time.perf_counter() - t1) * 1000)
            log.bind(src="loader").info(f"✅  {filename[:-3]:<28} {ms:>4}ms")
            ok += 1
        except Exception as e:
            ms = int((time.perf_counter() - t1) * 1000)
            log.bind(src="loader").error(f"❌  {filename[:-3]:<28} {ms:>4}ms  →  {e}")
            failed.append((filename[:-3], str(e)))

    elapsed = int((time.perf_counter() - t0) * 1000)
    log.info("━" * 44)
    log.bind(src="loader").info(
        f"Итог: {ok}/{total} OK"
        + (f"  |  ❌ ошибок: {len(failed)}" if failed else "  |  все модули загружены")
        + f"  ({elapsed}ms)"
    )

    if failed:
        log.info("━" * 44)
        log.bind(src="loader").warning("Проблемные модули:")
        for name, err in failed:
            log.bind(src="loader").warning(f"  • {name}: {err}")

    log.info("━" * 44)

# ─── setup_hook ───────────────────────────────────────────────────────────────
@bot.event
async def setup_hook():
    _print_banner()
    await load_slash_modules()
    await start_admin_panel(bot, log)

    log.bind(src="scheduler").info("Запуск планировщиков...")
    try:
        from scheduled.hourly_task import setup_birthday_checker
        setup_birthday_checker(bot)
        log.bind(src="scheduler").info("✅  birthday_checker    (каждый час)")
    except Exception as e:
        log.bind(src="scheduler").error(f"❌  birthday_checker: {e}")

    try:
        from scheduled.daily_kb_task import setup_daily_kb_refresh
        setup_daily_kb_refresh()
        log.bind(src="scheduler").info("✅  daily_kb_refresh    (00:00 Europe/Berlin)")
    except Exception as e:
        log.bind(src="scheduler").error(f"❌  daily_kb_refresh: {e}")

    log.info("━" * 44)

# ─── on_ready ─────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    log.bind(src="gateway").info(f"✅  Бот: {bot.user}  (ID: {bot.user.id})")
    log.bind(src="gateway").info(f"    Серверов: {len(bot.guilds)}")

    await bot.change_presence(status=discord.Status.invisible)
    log.bind(src="gateway").info("    Статус: invisible")

    try:
        synced = await bot.tree.sync()
        log.bind(src="gateway").info(f"    Slash-команд синхронизировано: {len(synced)}")
    except Exception as e:
        log.bind(src="gateway").error(f"    Ошибка синхронизации: {e}")

    log.info("━" * 44)
    log.bind(src="gateway").info("🚀  Бот готов к работе")
    log.info("━" * 44 + "\n")

# ─── Глобальный обработчик ошибок команд ──────────────────────────────────────
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    cmd = interaction.command.name if interaction.command else "unknown"
    user = str(interaction.user)
    log.bind(src=f"cmd/{cmd}").error(f"Ошибка команды /{cmd} от {user}: {error}")

    msg = "❌ Произошла ошибка при выполнении команды."
    if isinstance(error, discord.app_commands.MissingPermissions):
        msg = "❌ Недостаточно прав для этой команды."
    elif isinstance(error, discord.app_commands.CommandOnCooldown):
        msg = f"⏳ Команда на кулдауне. Попробуй через {error.retry_after:.1f}с."

    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass

# ─── Запуск ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    log.info("Инициализация бота...")
    bot.run(TOKEN, log_handler=None)  # log_handler=None — логи discord.py идут через loguru
