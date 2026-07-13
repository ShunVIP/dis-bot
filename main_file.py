# main_file.py
import os
import sys
import time
import types
import discord
from discord.ext import commands
from discord.app_commands import Command, CommandAlreadyRegistered, CommandLimitReached, ContextMenu, Group
from discord.app_commands.tree import _retrieve_guild_ids
from discord.utils import MISSING
from config import TOKEN
from core.admin_panel import start_admin_panel

if not TOKEN:
    raise ValueError("Discord bot token is missing. Set tok or DISCORD_BOT_TOKEN in KGTD.env")

# ─── Логгер ───────────────────────────────────────────────────────────────────
from utils.logger import log as _base_log
log = _base_log.bind(src="main")

# ─── Интенты ──────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.members         = True
intents.message_content = True
intents.presences       = True

bot = commands.Bot(command_prefix="!", intents=intents)

PUBLIC_MENU_COMMANDS = {"команды", "админ"}
SKIP_EXTENSION_FILES = {
    "parody_channel_settings.py",
    "parody_engine_wakelock.py",
}


def enable_menu_catalog_command_tree():
    """Allow collecting a large local command catalog before syncing only menu roots."""
    original_add_command = bot.tree.add_command

    def add_command_with_catalog_limit(self, command, /, *, guild=MISSING, guilds=MISSING, override=False):
        try:
            return original_add_command(command, guild=guild, guilds=guilds, override=override)
        except CommandLimitReached:
            if isinstance(command, ContextMenu):
                raise
            if not isinstance(command, (Command, Group)):
                raise

            guild_ids = _retrieve_guild_ids(command, guild, guilds)
            root = command.root_parent or command
            name = root.name

            if guild_ids is None:
                if name in self._global_commands and not override:
                    raise CommandAlreadyRegistered(name, None)
                self._global_commands[name] = root
                return

            for guild_id in guild_ids:
                commands_map = self._guild_commands.setdefault(guild_id, {})
                if name in commands_map and not override:
                    raise CommandAlreadyRegistered(name, guild_id)
                commands_map[name] = root

    bot.tree.add_command = types.MethodType(add_command_with_catalog_limit, bot.tree)
    bot.menu_catalog_command_tree_enabled = True


def collapse_slash_commands_to_menu():
    """Keep only /команды and /админ visible, but preserve all commands for menu UI."""
    all_commands = list(bot.tree.get_commands())
    bot.menu_catalog_commands = all_commands

    hidden = []
    kept = []
    for cmd in all_commands:
        if cmd.name in PUBLIC_MENU_COMMANDS:
            kept.append(cmd.name)
            continue

        removed = bot.tree.remove_command(
            cmd.name,
            type=getattr(cmd, "type", discord.AppCommandType.chat_input),
        )
        if removed is not None:
            hidden.append(cmd.name)

    bot.menu_hidden_command_names = set(hidden)
    bot.menu_commands_hidden_from_slash = True
    log.bind(src="loader").info(
        f"Slash-меню: видимые={sorted(kept)} | скрыто из /: {len(hidden)}"
    )

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
                   and f not in SKIP_EXTENSION_FILES
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
    enable_menu_catalog_command_tree()
    await load_slash_modules()
    collapse_slash_commands_to_menu()
    await start_admin_panel(bot, log)

    log.bind(src="scheduler").info("Запуск планировщиков...")
    try:
        from scheduled.hourly_task import setup_birthday_checker
        setup_birthday_checker(bot)
        log.bind(src="scheduler").info("✅  birthday_checker    (каждый час)")
    except Exception as e:
        log.bind(src="scheduler").error(f"❌  birthday_checker: {e}")

    try:
        from core.runtime_policy import is_wwm_kb_refresh_allowed
        if is_wwm_kb_refresh_allowed():
            from scheduled.daily_kb_task import setup_daily_kb_refresh
            setup_daily_kb_refresh()
            log.bind(src="scheduler").info("✅  WWM KB refresh      (local/heavy runtime)")
        else:
            log.bind(src="scheduler").info("⏭️  WWM KB refresh      disabled on VPS; build locally")
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

    if not getattr(bot, "admin_settings_seeded", False):
        try:
            from core.settings_migration import seed_admin_settings_from_legacy
            seed_admin_settings_from_legacy(log, guild_ids=[int(guild.id) for guild in bot.guilds])
            bot.admin_settings_seeded = True
            daily_cog = bot.get_cog("Daily")
            if daily_cog and hasattr(daily_cog, "refresh_tax_schedule"):
                daily_cog.refresh_tax_schedule()
        except Exception as e:
            log.bind(src="settings").error(f"Ошибка миграции настроек в админ-панель: {e}")

    try:
        from fun_slesh.menu import ensure_admin_panel_entry
        ok = await ensure_admin_panel_entry(bot)
        if not ok:
            log.bind(src="menu").warning("Не удалось создать или обновить постоянную кнопку админ-панели")
    except Exception as e:
        log.bind(src="menu").error(f"Ошибка постоянной кнопки админ-панели: {e}")

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
