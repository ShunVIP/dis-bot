# Game Profile and ML Roadmap

This document describes the planned game-profile system for the future site/app.

## Core Idea

Users connect Discord OAuth, then connect/confirm game accounts. The platform builds a large player profile from official APIs and approved data sources, then trains:

- a per-game model, starting with League of Legends;
- a cross-game model that studies behavior across multiple games;
- a player-type classifier that explains how the person plays, not just whether they win.

## Discord Connection Reality

Discord can expose linked third-party accounts only when the user authorizes our app with the `connections` OAuth2 scope.

Important:
- The bot cannot silently read Riot/LoL connections for every server member.
- The user must log in through our site/app and grant Discord OAuth scopes.
- Discord connections may include `riotgames` and `leagueoflegends`.
- Discord connection data should be treated as a discovery/verification hint, not the only source of truth.

Suggested OAuth scopes:

```text
identify guilds guilds.members.read connections
```

Use cases:
- detect that the Discord user has Riot/LoL connected;
- prefill account candidates;
- ask the user to confirm which Riot account/summoner is theirs;
- then fetch proper data from Riot APIs.

## Data Sources

### Preferred: Official Riot API

Use Riot API as the primary source.

Expected env:

```text
RIOT_API_KEY=
RIOT_PLATFORM_REGION=ru
RIOT_REGIONAL_ROUTING=europe
```

Main data groups:
- Riot account: game name, tag line, PUUID;
- LoL summoner profile;
- ranked entries;
- match history;
- match timeline;
- champion mastery;
- challenges and seasonal stats where available.

### Third-party Sites

Examples:
- League of Graphs;
- Blitz.gg;
- OP.GG;
- Mobalytics;
- U.GG.

Rule:
- Do not scrape blindly.
- Prefer official API, partner API, public export, or explicit permission.
- If a site has no approved API, store only links and user-facing references, not scraped datasets.

Third-party sources can still be useful as outbound profile links:
- League of Graphs profile URL;
- OP.GG profile URL;
- Blitz profile URL;
- Mobalytics profile URL.

## Database Sketch

Suggested tables:

### `game_accounts`

- `id`
- `discord_user_id`
- `game`
- `provider`
- `region`
- `external_id`
- `display_name`
- `verified`
- `created_at`
- `updated_at`

For LoL:
- `provider = riot`
- `external_id = puuid`
- `display_name = RiotName#TAG`

### `lol_profile_snapshots`

- `id`
- `discord_user_id`
- `puuid`
- `region`
- `snapshot_json`
- `created_at`

Store normalized summary data, not just raw API dumps.

### `lol_match_features`

- `match_id`
- `puuid`
- `queue_id`
- `champion_id`
- `role`
- `win`
- `kills`
- `deaths`
- `assists`
- `cs_per_min`
- `gold_per_min`
- `vision_score`
- `damage_share`
- `objective_participation`
- `teamfight_participation`
- `created_at`

### `player_model_profiles`

- `discord_user_id`
- `game`
- `model_version`
- `features_json`
- `labels_json`
- `explanation`
- `updated_at`

## LoL Player Type Model

First version can be rules + clustering before deep ML.

Feature examples:
- aggression: kill participation, damage share, early fights;
- safety: deaths, shutdowns given, risky positioning proxy;
- macro: objective participation, rotations, tower/objective presence;
- farming: CS/min, gold/min, lane economy;
- vision: wards, control wards, vision score;
- champion pool: diversity, comfort picks, role stability;
- consistency: variance across last N games;
- tilt risk: loss streak behavior, death spikes after early loss;
- team orientation: assists, peel, utility champs, objective focus.

Possible labels:
- aggressive carry;
- stable farmer;
- macro/objective player;
- roaming playmaker;
- coinflip fighter;
- utility/team player;
- scaling player;
- vision/control player;
- unstable/tilt-prone;
- flexible generalist.

## Cross-game Player Type Model

Normalize game-specific features into shared axes:

- aggression;
- patience;
- mechanical intensity;
- planning/macro;
- cooperation;
- risk tolerance;
- consistency;
- adaptability;
- grind tendency;
- leadership.

Then create a global player card:

```text
Primary type: aggressive planner
Secondary type: team-oriented grinder
Risk: tilt after repeated losses
Best role: shotcaller / initiator
Weak point: overcommits when ahead
```

## Privacy and Consent

Required:
- explicit opt-in;
- show what data is collected;
- allow unlinking Riot account;
- allow deleting stored game profile;
- do not publish private analysis by default;
- public cards must be user-approved or admin-configured.

## Implementation Phases

## Implemented Bot MVP

Added locally:

- `core.game_profiles`: tables for linked game accounts, LoL snapshots, match features and model profiles.
- `core.riot_client`: Riot Account/Summoner/League/Mastery/Match client.
- `core.lol_player_model`: first rule-based LoL player-type classifier.
- `fun_slesh.lol_profile`: Discord cog with `lol привязать`, `lol обновить`, `lol профиль`, `lol отвязать`.
- `fun_slesh.menu`: new `Игры` category with a nested League of Legends section and Riot ID modal.

This is not the final site/app OAuth flow yet. It is the working bot-side base that the future web/app should reuse.

### Phase 1: LoL Linking

- Add Discord OAuth `connections` to site/app.
- Add manual Riot account linking.
- Store Riot PUUID and region.
- Show profile card in site/app and Discord.

### Phase 2: Riot Data Collector

- Pull ranked, mastery, match history and timelines.
- Cache API responses carefully.
- Add refresh jobs and rate-limit handling.
- Store normalized features.

### Phase 3: Rule-based Profile

- Build deterministic first version of player type.
- Explain every label with visible stats.
- Add Discord menu path: `Игры -> League of Legends`.

### Phase 4: ML v1

- Train clustering/classifier over normalized LoL features.
- Keep model versioned.
- Compare rule-based labels vs ML labels.

### Phase 5: Cross-game Model

- Add Steam/game integrations where APIs allow.
- Normalize features into shared axes.
- Build global player type.

## Open Questions

- Which regions should be enabled first: RU/EUW/EUNE/KR/NA?
- Should profiles be public on the Discord server by default or private?
- Should admins see all linked player profiles, or only users who opt into guild visibility?
- Should the model explain with jokes/roasts, serious analytics, or both modes?
