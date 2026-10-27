"""Publish/rotate only explicitly marked Aion boards; never copy arbitrary pins."""
import logging

import discord

from core.aion2_timers import board_refresh_due

log = logging.getLogger(__name__)


def matches(message, bot_id, marker):
    return message.author.id == bot_id and any(
        (embed.footer.text or '').split('\n')[-1] == marker for embed in message.embeds
    )


async def fetch_board(channel, message_id, bot_id, marker):
    try:
        message = await channel.fetch_message(int(message_id))
    except discord.NotFound:
        return None
    return message if matches(message, bot_id, marker) else None


async def publish_board(channel, bot_id, state, embed, marker, now, save, *, can_pin, view=None):
    """Send -> pin -> save replacement ID/cleanup queue -> delete verified old post.

    A failed pin/save keeps the old copy. A failed delete is retried from canonical
    runtime state; recovering a recent replacement avoids a resend after a crash.
    """
    message = None
    if state.get('channel_id') == channel.id and state.get('message_id'):
        message = await fetch_board(channel, state['message_id'], bot_id, marker)
    retired = {int(i) for i in state.get('retired_ids', []) if str(i).isdigit()}
    rotate = message is not None and board_refresh_due(getattr(message, 'created_at', None), now) and can_pin
    if message is None or rotate:
        previous = message
        recovered = None
        candidates = []
        async for candidate in channel.history(limit=100):
            if matches(candidate, bot_id, marker) and (previous is None or candidate.id != previous.id):
                candidates.append(candidate)
                if recovered is None and not board_refresh_due(getattr(candidate, 'created_at', None), now):
                    recovered = candidate
        if recovered is None and previous is None and candidates and not can_pin:
            recovered = candidates[0]
        message = recovered
        if message is None:
            message = await channel.send(embed=embed,view=view,allowed_mentions=discord.AllowedMentions.none())
        else:
            await message.edit(embed=embed,view=view,allowed_mentions=discord.AllowedMentions.none())
        if previous is not None:
            retired.add(previous.id)
        if can_pin:
            retired.update(candidate.id for candidate in candidates if candidate.id != message.id)
    else:
        await message.edit(embed=embed,view=view,allowed_mentions=discord.AllowedMentions.none())
    pinned = bool(message.pinned)
    if not pinned and can_pin:
        try:
            await message.pin(reason='Постоянная таблица Aion 2: еженедельное обновление внизу канала')
            pinned = True
        except discord.HTTPException:
            log.warning('Aion2 board pin failed channel=%s message=%s',channel.id,message.id)
    created_at = getattr(message,'created_at',None) or now
    retired.discard(message.id)
    state.update(channel_id=channel.id,message_id=message.id,board_created_at=created_at.isoformat(),
                 retired_ids=sorted(retired),pin_pending=can_pin and not pinned)
    # Persist the new reference before deleting anything. No delete after a failed save.
    await save(state)
    if not pinned:
        return message
    remaining = set(retired)
    for message_id in sorted(retired):
        old = await fetch_board(channel,message_id,bot_id,marker)
        try:
            if old is not None:
                await old.delete()
        except discord.HTTPException:
            log.warning('Aion2 retired board deletion failed channel=%s message=%s',channel.id,message_id)
            continue
        remaining.discard(message_id)
    if remaining != retired:
        state['retired_ids'] = sorted(remaining)
        await save(state)
    return message
