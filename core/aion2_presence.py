"""Presence-only opt-in signal for Aion 2 reminders; no history or inference."""
import re
import unicodedata


def is_playing_aion2(member):
    if getattr(member, 'bot', False):
        return False
    for activity in getattr(member, 'activities', ()):
        kind = getattr(activity, 'type', None)
        if getattr(kind, 'value', kind) != 0:  # Discord ActivityType.playing, not a custom status.
            continue
        name = unicodedata.normalize('NFKC', str(getattr(activity, 'name', ''))).casefold()
        name = re.sub(r'[\W_]+', '', name)
        if name in {'aion2', 'aionii', 'aion2global', 'aion2eu', 'aion2europe', '아이온2'}:
            return True
    return False


def has_players(channel):
    # TextChannel.members already excludes members who cannot view this channel.
    return any(is_playing_aion2(member) for member in channel.members)
