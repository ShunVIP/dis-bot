"""Discord-native, mobile-friendly views of public NC character data."""
import html
import re
from urllib.parse import urlsplit

import discord


LABELS = {
    'stat': 'Характеристики', 'equipment': 'Экипировка', 'skin': 'Облик',
    'skill': 'Навыки', 'title': 'Титулы', 'ranking': 'Рейтинги',
    'daevanion': 'Daevanion', 'petwing': 'Питомец и крылья',
    'pet': 'Питомец', 'wing': 'Крылья', 'wingSkin': 'Облик крыльев',
    'name': 'Название', 'value': 'Значение', 'level': 'Уровень',
    'skillLevel': 'Уровень навыка', 'needLevel': 'Требуемый уровень',
    'enchantLevel': 'Заточка', 'exceedLevel': 'Прорыв', 'grade': 'Качество',
    'category': 'Тип', 'categoryName': 'Тип предмета', 'slotPosName': 'Слот',
    'rank': 'Место', 'ranking': 'Место', 'score': 'Очки', 'point': 'Очки',
    'rankName': 'Ранг', 'rankingName': 'Рейтинг', 'seasonName': 'Сезон',
    'ownedCount': 'Получено', 'totalCount': 'Всего', 'ownedPercent': 'Собрано, %',
    'openNodeCount': 'Открыто узлов', 'totalNodeCount': 'Всего узлов',
    'openPercent': 'Прогресс, %', 'open': 'Открыто', 'acquired': 'Изучен',
    'equip': 'Выбран', 'equipCategory': 'Категория титула', 'desc': 'Описание',
    'mainStats': 'Основные характеристики', 'subStats': 'Дополнительные характеристики',
    'magicStoneStat': 'Магические камни', 'subSkills': 'Бонусы навыков',
    'sources': 'Источники', 'costumes': 'Облики', 'equipLevel': 'Уровень для ношения',
    'maxEnchantLevel': 'Максимальная заточка', 'soulBindRate': 'Привязка души, %',
    'magicStoneSlotCount': 'Ячейки магических камней', 'godStoneSlotCount': 'Ячейки божественных камней',
    'storable': 'Можно хранить', 'tradable': 'Можно продать', 'tradablePersonal': 'Личный обмен',
    'enchantable': 'Можно точить', 'decomposable': 'Можно разобрать',
    'openSkillEffectList': 'Открытые бонусы навыков', 'openStatEffectList': 'Открытые бонусы характеристик',
    'effectList': 'Эффекты', 'equipStatList': 'Бонусы выбранного титула',
    'statList': 'Характеристики', 'statSecondList': 'Эффекты характеристики',
    'Attack': 'Атака', 'Defense': 'Защита', 'Accuracy': 'Точность',
    'Critical Hit': 'Критический удар', 'Evasion': 'Уклонение', 'Block': 'Блок',
    'Might': 'Сила', 'Agility': 'Ловкость', 'Wisdom': 'Мудрость',
    'Intelligence': 'Интеллект', 'Vitality': 'Выносливость', 'Will': 'Воля',
    'HP': 'Здоровье', 'MP': 'Мана', 'ItemLevel': 'Уровень предметов',
    'MainHand': 'Основное оружие', 'OffHand': 'Левая рука', 'Head': 'Голова',
    'Torso': 'Нагрудник', 'Shoulder': 'Плечи', 'Pants': 'Поножи', 'Gloves': 'Перчатки',
    'Shoes': 'Обувь', 'Necklace': 'Ожерелье', 'Earrings': 'Серьги', 'Ring': 'Кольцо',
    'Bracelet': 'Браслет', 'Belt': 'Пояс', 'Rune': 'Руна', 'Amulet': 'Амулет',
    'Active': 'Активный', 'Passive': 'Пассивный',
    'Common': 'Обычное', 'Rare': 'Редкое', 'Unique': 'Уникальное',
    'Legend': 'Эпическое', 'Epic': 'Эпическое', 'Special': 'Особое', 'Mythic': 'Мифическое',
}
COLORS = {'Common': 0x95a5a6, 'Rare': 0x3498db, 'Unique': 0xf1c40f,
          'Legend': 0x9b59b6, 'Epic': 0x9b59b6, 'Special': 0xe67e22, 'Mythic': 0xe74c3c}
HIDDEN = {'id', 'icon', 'profileImage', 'characterId', 'serverId', 'nodeId', 'boardId',
          'slotPos', 'row', 'col', 'type', 'gradeName', 'levelValue'}


def text(value, limit=900):
    if value is None or value == '':
        return '—'
    if isinstance(value, bool):
        return 'Да' if value else 'Нет'
    value = html.unescape(re.sub(r'<[^>]*>', '', str(value)))
    return discord.utils.escape_mentions(discord.utils.escape_markdown(value))[:limit]


def label(key):
    return LABELS.get(str(key), re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', str(key)).replace('_', ' '))


def picture(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme == 'https' and parsed.hostname in {'assets.playnccdn.com', 'profileimg.plaync.com'}
                and not parsed.username and not parsed.password and len(value) <= 2048):
            return value
    except ValueError:
        pass
    return None


def readable(value, depth=0):
    """Fallback for new API fields: nested human-readable text, never JSON."""
    if depth > 6:
        return 'Подробности — в полной выгрузке.'
    if isinstance(value, list):
        return '\n'.join(readable(row, depth+1) for row in value) or 'Нет данных.'
    if isinstance(value, dict):
        if 'desc' in value:
            return text(value['desc'])
        if 'value' in value and ('name' in value or 'type' in value):
            result = f"{text(label(value.get('name') or value.get('type')))}: **{text(value['value'])}**"
            if value.get('extra') not in (None, '', '0', '0%'):
                result += f" · усиление {text(value['extra'])}"
            if value.get('statSecondList'):
                result += '\n' + readable(value['statSecondList'], depth+1)
            return result
        lines = []
        for key, entry in value.items():
            if key in HIDDEN or key.endswith('Id') or entry is None:
                continue
            lines.append(f"{text(label(key))}: {readable(entry, depth+1)}")
        return '\n'.join(lines) or 'Нет данных.'
    return text(value)


def fields_pages(title, fields, icon=None, color=0x3498db, intro=None):
    """Bound embeds well below Discord's 6000-character aggregate limit."""
    pages = []
    expanded = []
    for name, value in fields:
        value = str(value or '—')
        for pos in range(0, len(value), 900):
            expanded.append((name if pos == 0 else name+' (продолжение)', value[pos:pos+900]))
    for offset in range(0, max(1, len(expanded)), 4):
        embed = discord.Embed(title=text(title, 256), color=color, description=text(intro, 800) if intro else None)
        if picture(icon):
            embed.set_thumbnail(url=picture(icon))
        for name, value in expanded[offset:offset+4]:
            embed.add_field(name=text(name, 256), value=value, inline=False)
        if not expanded and not intro:
            embed.description = 'В этом разделе NC пока не вернул данных.'
        pages.append(embed)
    return pages


def row_pages(row, heading, *, kind=None):
    if not isinstance(row, dict):
        return fields_pages(heading, [('Данные', readable(row))])
    name = row.get('name') or heading
    title = f"{heading} · {name}" if name != heading else heading
    fields = []
    properties = []
    for key, value in row.items():
        if key in HIDDEN or key in {'name', 'nodeList'} or key.endswith('Id') or value is None:
            continue
        if key in {'grade', 'category', 'categoryName', 'slotPosName'}:
            value = text(label(value))
        elif key in {'equip', 'acquired', 'open'}:
            value = 'Да' if value else 'Нет'
        else:
            value = readable(value)
        if isinstance(row[key], (dict, list)) or key == 'desc':
            fields.append((label(key), value))
        else:
            properties.append(f"{text(label(key))}: **{value}**")
    if properties:
        fields.insert(0, ('Свойства', '\n'.join(properties)))
    if kind == 'board':
        nodes = row.get('nodeList') or []
        fields.insert(0, ('Узлы', f"Открыто {sum(bool(n.get('open')) for n in nodes)} из {len(nodes)}"))
        for node in nodes:
            if node.get('type') == 'None' and not node.get('effectList'):
                continue
            node_name = node.get('name') or f"Узел {node.get('row', '?')}:{node.get('col', '?')}"
            fields.append((('✅ ' if node.get('open') else '🔒 ')+node_name,
                           readable(node.get('effectList')) if node.get('effectList') else 'Эффекты не указаны.'))
    return fields_pages(title, fields, row.get('icon'), COLORS.get(row.get('grade'), 0x3498db))


def section_pages(key, data, title=None):
    heading = title or label(key)
    if not data:
        return fields_pages(heading, [])
    pages = []
    if key == 'stat':
        rows = data.get('statList') or []
        return fields_pages(heading, [(label(row.get('name') or row.get('type', 'Параметр')), readable(row)) for row in rows])
    if key == 'equipment':
        for collection, name in [('equipmentList', 'Экипировка'), ('skinList', 'Облик')]:
            for row in data.get(collection) or []:
                pages.extend(row_pages(row, name))
    elif key == 'petwing':
        for name in ('pet', 'wing', 'wingSkin'):
            if data.get(name):
                pages.extend(row_pages(data[name], label(name)))
    elif key in {'skill', 'title', 'ranking', 'daevanion'}:
        collection = {'skill':'skillList', 'title':'titleList', 'ranking':'rankingList', 'daevanion':'boardList'}[key]
        summary = [(label(k), readable(v)) for k, v in data.items() if k != collection and v is not None]
        if summary:
            pages.extend(fields_pages(heading, summary))
        for row in data.get(collection) or []:
            pages.extend(row_pages(row, heading))
    elif key in {'item', 'board'}:
        pages = row_pages(data, heading, kind=key)
    else:
        pages = fields_pages(heading, [('Данные', readable(data))])
    return pages or fields_pages(heading, [])
