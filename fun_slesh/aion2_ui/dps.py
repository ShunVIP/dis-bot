"""Private DPS cards: public reports or clearly labelled manual LagoFast numbers."""
import discord

from core import aion2_dps_service as service
from .personal import PrivateView, Pages
from .presentation import fields_pages, text


def report_pages(report):
    fields=[('Результат боя',f"Длительность: **{report['duration']:.1f} сек**\nУрон: **{report['damage']:,.0f}**\nDPS: **{report['dps']:,.1f}**")]
    for actor in report.get('actors',[]):
        value=f"Урон {actor['damage']:,.0f} · DPS **{actor['dps']:,.1f}**"
        for skill in actor['skills']:
            share=skill['damage']/actor['damage']*100 if actor['damage'] else 0
            value+=f"\n{text(skill['name'])}: {skill['damage']:,.0f} ({share:.1f}%)"
        fields.append((actor['name'],value))
    if report.get('partial'):
        fields.append(('Ограничение отчёта','Сумма урона навыков отличается от итогового урона. Разбор по участникам может быть неполным.'))
    if report.get('url'):
        intro='Публичный отчёт A2Tools. Имена сохраняют маскировку источника. Это не подтверждение твоего участия в бою.'
    else:
        intro='Ручной ввод из LagoFast или другого метра: не автоматическое подключение и не проверенный игровой лог.'
    intro+='\nDPS рассчитан как урон / указанная длительность, не по активному времени атак.'
    return fields_pages('DPS · '+report['boss'],fields,intro=intro)


class PublicReportModal(discord.ui.Modal,title='Публичный отчёт A2Tools'):
    url=discord.ui.TextInput(label='https://a2tools.app/logs/…',max_length=300)

    def __init__(self,user_id,guild_id):
        super().__init__()
        self.user_id,self.guild_id=user_id,guild_id

    async def on_submit(self,interaction):
        if interaction.user.id!=self.user_id or interaction.guild_id!=self.guild_id:return
        await interaction.response.defer(ephemeral=True,thinking=True)
        try:
            report=await service.public_report(str(self.url))
            view=Pages(self.user_id,self.guild_id,report_pages(report))
            view.add_item(discord.ui.Button(label='Исходный отчёт',url=report['url'],row=1))
            await interaction.followup.send(embed=view.embed(),view=view,ephemeral=True,allowed_mentions=discord.AllowedMentions.none())
        except (ValueError,RuntimeError) as exc:await interaction.followup.send(str(exc),ephemeral=True)


class ManualReportModal(discord.ui.Modal,title='LagoFast · ручной результат боя'):
    damage=discord.ui.TextInput(label='Общий урон: число, 1234k или 1.2m',max_length=30)
    duration=discord.ui.TextInput(label='Длительность: секунды или ММ:СС',max_length=20)
    boss=discord.ui.TextInput(label='Босс / название боя',max_length=100,required=False)

    def __init__(self,user_id,guild_id):
        super().__init__()
        self.user_id,self.guild_id=user_id,guild_id

    async def on_submit(self,interaction):
        if interaction.user.id!=self.user_id or interaction.guild_id!=self.guild_id:return
        try:
            report=service.manual_report(str(self.damage),str(self.duration),str(self.boss))
            view=Pages(self.user_id,self.guild_id,report_pages(report))
            await interaction.response.send_message(embed=view.embed(),view=view,ephemeral=True)
        except ValueError as exc:await interaction.response.send_message(str(exc),ephemeral=True)


class DpsView(PrivateView):
    @discord.ui.button(label='Разобрать A2Tools',style=discord.ButtonStyle.primary)
    async def public(self,interaction,button):
        await interaction.response.send_modal(PublicReportModal(self.user_id,self.guild_id))

    @discord.ui.button(label='Ввести результат LagoFast')
    async def manual(self,interaction,button):
        await interaction.response.send_modal(ManualReportModal(self.user_id,self.guild_id))

    @discord.ui.button(label='Как измерить DPS',row=1)
    async def help(self,interaction,button):
        await interaction.response.send_message('Без стороннего ПО: Ctrl+X → анализ боя. Для одного босса выбери одну цель и Start; для нескольких целей — Start перед боем и Stop после.\nLagoFast: перенеси сюда общий урон и длительность из результата. Бот не читает игру, процессы или закрытый API LagoFast.\nДля автоматического разбора сейчас поддержана только публичная ссылка A2Tools. Частные отчёты и пароли не нужны.',ephemeral=True)


async def open_dps(interaction):
    await interaction.response.send_message('**Разбор DPS Aion 2**\nПубличный A2Tools — автоматический разбор. LagoFast — пока ручной ввод урона и времени; прямое подключение не подтверждено. Отчёты не записываются в базу, никто не получает рассылок.',view=DpsView(interaction.user.id,interaction.guild_id),ephemeral=True)
