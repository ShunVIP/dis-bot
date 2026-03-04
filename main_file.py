import os
import discord
from discord.ext import commands
from config import TOKEN

# Логгер
import utils.logger

# Интенты
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Бот запущен как {bot.user} (ID: {bot.user.id})")
    # 👇 Сразу выставляем статус офлайн
    await bot.change_presence(status=discord.Status.invisible)
    try:
        synced = await bot.tree.sync()
        print(f"🔃 Синхронизировано {len(synced)} slash-команд")
    except Exception as e:
        print(f"❌ Ошибка синхронизации: {e}")

# ── Автозагрузка модулей ───────────────────────────
async def load_slash_modules():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    folder = os.path.join(base_dir, "fun_slesh")

    for filename in os.listdir(folder):
        if filename == "__init__.py" or filename.startswith("_"):
            continue
        if not filename.endswith(".py"):
            continue

        ext_name = f"fun_slesh.{filename[:-3]}"
        try:
            await bot.load_extension(ext_name)
            print(f"[OK] loaded {ext_name}")
        except Exception as e:
            print(f"[FAIL] {ext_name}: {e}")

@bot.event
async def setup_hook():
    await load_slash_modules()
    from scheduled.hourly_task import setup_birthday_checker
    setup_birthday_checker(bot)
     # ✅ ежедневный сбор базы WWM в 00:00 (Europe/Berlin)
    from scheduled.daily_kb_task import setup_daily_kb_refresh
    setup_daily_kb_refresh()

if __name__ == "__main__":
    bot.run(TOKEN)
