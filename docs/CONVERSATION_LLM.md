# Локальная разговорная LLM ViPik

## Цель

Разговорный бот использует бесплатную open-weight модель на приватном ПК. VPS отвечает за Discord, согласия, короткую память и derived gamer profiles. Платные API и отправка личных диалогов третьим сторонам не нужны.

Базовая модель: `Qwen/Qwen3-8B` (Apache-2.0, русский входит в официальный multilingual набор). Для адаптации используется QLoRA: 4-bit NF4 base model + LoRA на all-linear layers. Полное обучение модели с нуля не применяется.

## Границы данных

- Обычные сообщения сервера не являются LLM training dataset.
- Runtime сохраняет только состоявшиеся диалоги с ботом и feedback.
- Персональная память включается пользователем командой `/болтовня персонализация`.
- Обучение требует отдельного `training_opt_in`.
- В dataset попадает пример только после 👍 от того же пользователя, который разговаривал с ботом.
- Mentions, Discord snowflake ID и URL заменяются нейтральными маркерами.
- `/болтовня забыть_меня подтвердить:true` удаляет turns, feedback, consent и derived gamer profile.
- Markov-пародии и общий `messages.db` не смешиваются с LLM.

## Игровые профили

`core/gamer_profile_store.py` строит профиль из:

- завершённых Discord activity sessions;
- локального Steam owned-games cache;
- явно указанных пользователем жанров.

Поддерживаются MMO, Souls-like, shooter, MOBA, strategy, RPG, survival, sandbox, fighting и racing. Профиль используется в prompt только при разрешённой памяти; вручную выбранные жанры считаются явной пользовательской настройкой.

Каждый одобренный training example получает список `cohorts`. Это позволяет измерять баланс MMO/Souls/shooter/MOBA и позднее сравнивать общий adapter с жанровыми adapter-экспериментами, не создавая отдельную модель при нескольких примерах.

## Подготовка dataset на основном ПК

Сначала создать согласованный snapshot, не обучаться на live database:

```powershell
python scripts/snapshot_training_data.py datebase/social.db=data/training/social.snapshot.db
python scripts/build_conversation_dataset.py --database data/training/social.snapshot.db --output data/training/vipik-conversation.jsonl
python scripts/train_conversation_lora.py --dataset data/training/vipik-conversation.jsonl --dry-run
```

Training script по умолчанию требует минимум 50 одобренных примеров. До этого порога лучше использовать base model + gamer context/RAG: малый dataset скорее испортит речь, чем улучшит её.

## QLoRA на основном ПК

PyTorch с подходящей CUDA устанавливается отдельно. Затем:

```powershell
python -m pip install -r requirements-llm-train.txt
python scripts/train_conversation_lora.py --dataset data/training/vipik-conversation.jsonl --output-dir models/conversation-qwen3-8b-lora
```

Результат — небольшой PEFT adapter. Ollama поддерживает Safetensors adapter через `ADAPTER` в Modelfile, но base model должен совпадать с использованной при fine-tuning моделью. Установка модели и первый training run выполняются только после отдельного подтверждения владельца ПК.

## Production gate

До активации нового адаптера:

1. не менее 50 opt-in положительных примеров;
2. отдельный holdout из минимум 10 примеров;
3. ручное сравнение base vs adapter по русскому языку, полезности, юмору и токсичности;
4. отсутствие утечки ID/mentions/URL в JSONL;
5. rollback на base model одной переменной `LOCAL_CHAT_MODEL`.
