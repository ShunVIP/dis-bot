# WWM_KB

Как устроен контур **Where Winds Meet Knowledge Base**: сбор → RAW → postprocess → проверка.

## 1) Где хранится база

SQLite файл:
- **`datebase/wwm.db`**

## 2) RAW слой

### Таблица `raw_records`
Содержит сырые записи по источникам. Ключевые поля:
- `source` — `fandom` / `game8`
- `method` — метод получения (`mw_api`, `html_archives`, и т.п.)
- `entity_type` — тип записи (на текущем этапе общий)
- `external_id` — внешний id или URL
- `title`, `url`
- `payload_json` — «сырой» JSON/текст
- `content_hash` — хэш полезной нагрузки (удобно для дедупа/отладки)
- `fetched_at` — время получения (UTC ISO)

RAW-слой **не правим руками** и не «улучшаем» — он нужен для воспроизводимости и будущей переобработки.

## 3) Normalized слой (postprocess v1)

### Таблицы
- `entities` — каноничные сущности (v1: без сложной сшивки между источниками)
- `entity_sources` — привязки сущности к данным источников (Fandom/Game8) + payload
- `aliases` — алиасы для поиска (ключи нормализованы)
- `refresh_runs` — статусы прогонов postprocess

### Зачем нужен normalized
- быстрый поиск по алиасам/названиям
- удобная основа под slash-команды
- подготовка к ML (классификация/сшивка/рекомендации)

## 4) Ежедневное обновление

Запуск планируется на:
- **00:00 Europe/Berlin**

(Планировщик — через APScheduler в модуле scheduled-задач.)

## 5) Ручной цикл разработки/проверки

### 5.1 Собрать RAW
```bat
python run_wwm_refresh_once.py
```

### 5.2 Проверить RAW
```bat
python inspect_wwm_db.py
```

### 5.3 Пересобрать normalized (если меняли логику postprocess или чистку title)
1) Сброс normalized (RAW не трогаем):
```bat
python reset_normalized_layer.py
```

2) Построение normalized v1:
```bat
python postprocess_v1.py
```

### 5.4 Проверить entities
```bat
python inspect_entities.py
```

## 6) Частые проблемы и решения

### `sqlite3.OperationalError: unable to open database file`
Причина: относительный путь, запуск из другой папки.  
Решение: в скриптах использовать абсолютный путь через `PROJECT_ROOT` + `datebase/wwm.db`.

### Title у Game8 “грязный” (хвосты `| ... | Game8`)
Решение: чистить title на этапе postprocess (canonical_title), и/или улучшать извлечение `title` в collector.

## 7) Дальнейшие улучшения (план)
- RU-слой (вариант 2): хранить `*_ru` поля, перевод пакетно при postprocess
- Глоссарий «не переводим» (вариант 4): список англ. терминов игры, которые оставляем как есть
- Поиск и slash-команды: `/wwm search`, затем типизированные команды
- Сшивка сущностей (Fandom + Game8) в v2
- ML: классификация страниц/типов сущностей, рекомендации, семантический поиск
