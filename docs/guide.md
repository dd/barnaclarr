# Руководство пользователя

Пошаговая инструкция: что делает Anime Sidecar Manager, как поставить его в TrueNAS, подключить к Sonarr и проверить. Справочные подробности — в [installation.md](installation.md), требования к дискам — в [storage.md](storage.md).


## Что делает модуль

Многие раздачи, особенно аниме, содержат кроме видео отдельные аудиодорожки (дубляж и озвучки на разных языках, комментарии), субтитры и шрифты к ним, разложенные по папкам. Sonarr импортирует в медиатеку только видео, а Jellyfin видит дополнительные дорожки, только если они лежат рядом с видео и называются так же.

Модуль не привязан к конкретному языку или трекеру: язык и название дорожки он берёт из структуры раздачи. Ниже пример раздачи с русскими озвучками; так же обрабатываются английские, немецкие и любые другие.

После каждого импорта Sonarr модуль находит в раздаче дорожки этой серии, кладёт их рядом с видео под правильными именами и собирает шрифты. Файлы не копируются: создаются hardlinks, поэтому место на диске не расходуется, а раздача продолжает раздаваться.

**Было в раздаче:**

```
[Erai-raws] Hataraku Saibou [1080p]/
├── Sound/
│   ├── AniLibria/[Erai-raws] Hataraku Saibou - 01 [1080p].mka
│   └── StudioBand/[Erai-raws] Hataraku Saibou - 01 [1080p].mka
├── Subs/
│   ├── laci/
│   │   ├── Fonts/...
│   │   ├── Надписи/[Erai-raws] Hataraku Saibou - 01 [1080p].ass
│   │   └── [Erai-raws] Hataraku Saibou - 01 [1080p].ass
│   └── SovetRomantica/
│       ├── Fonts/...
│       ├── Надписи/[Erai-raws] Hataraku Saibou - 01 [1080p].ass
│       └── [Erai-raws] Hataraku Saibou - 01 [1080p].ass
└── [Erai-raws] Hataraku Saibou - 01 [1080p].mkv
```

**Стало в медиатеке** (видео импортировал и переименовал Sonarr, остальное — модуль):

```
Cells at Work! (2018)/Season 01/
├── S01E01 - Pneumococcus - [JA] WEBRip-1080p.mkv
├── S01E01 - Pneumococcus - [JA] WEBRip-1080p.AniLibria.ru.mka
├── S01E01 - Pneumococcus - [JA] WEBRip-1080p.StudioBand.ru.mka
├── S01E01 - Pneumococcus - [JA] WEBRip-1080p.laci.ru.ass
├── S01E01 - Pneumococcus - [JA] WEBRip-1080p.laci Signs.ru.ass
├── S01E01 - Pneumococcus - [JA] WEBRip-1080p.SovetRomantica.ru.ass
├── S01E01 - Pneumococcus - [JA] WEBRip-1080p.SovetRomantica Signs.ru.ass
└── fonts/
    ├── LC-Chalk-Regular-ff878827e7c0bc22.ttf
    └── ...
```

В Jellyfin в меню аудио появятся «AniLibria — Russian» и «StudioBand — Russian», в субтитрах — «laci», «laci Signs» и так далее (язык здесь взят из `SIDECAR_DEFAULT_LANGUAGE`, потому что в папках раздачи он не указан). Шрифты дополнительно попадают в общую папку `.anime-sidecar-fonts`, которую можно подключить к Jellyfin.

Модуль ничего не скачивает, не переименовывает видео и не меняет файлы раздачи.


## Что нужно заранее

- **TrueNAS SCALE** с приложениями Sonarr, торрент-клиентом (например, qBittorrent) и Jellyfin.
- **Загрузки и медиатека в одном dataset.** Hardlink возможен только внутри одной файловой системы. Например, dataset `media` с папками `downloads/` и `anime/`. Подробнее и как проверить — в [storage.md](storage.md).
- **Sonarr импортирует hardlinks:** Settings → Media Management → Use Hardlinks instead of Copy.
- **Пути совпадают в Sonarr и торрент-клиенте:** оба видят один и тот же dataset под одним и тем же путём, например `/mnt/media`.


## Быстрый старт в TrueNAS

Ниже `/mnt/tank/media` — твой dataset с загрузками и медиатекой, `/mnt/tank/apps/anime-sidecar` — место для служебных файлов модуля. Подставь свои пути.

### 1. Папка для служебной базы

Модуль хранит очередь событий и журнал созданных ссылок в SQLite. В shell TrueNAS:

```sh
sudo mkdir -p /mnt/tank/apps/anime-sidecar/state
sudo chown -R 568:568 /mnt/tank/apps/anime-sidecar
sudo chmod 700 /mnt/tank/apps/anime-sidecar/state
```

### 2. Токен для вебхука

Случайная строка, которой Sonarr будет подписывать запросы:

```sh
openssl rand -hex 32
```

Сохрани её: она понадобится дважды, в приложении и в Sonarr.

### 3. Установка

Apps → Discover Apps → ⋮ → **Install via YAML**. Имя приложения: `anime-sidecar`. Возьми [deploy/truenas-custom-app.yaml](../deploy/truenas-custom-app.yaml) и замени в нём:

| Что | Пример | Пояснение |
| --- | --- | --- |
| `/mnt/tank/media` в `volumes` | `/mnt/tank/media` | твой dataset, внутри контейнера он будет `/mnt/media` |
| `/mnt/tank/apps/anime-sidecar/state` | | папка из шага 1 |
| `SIDECAR_DOWNLOAD_ROOTS` | `/mnt/media/downloads/anime` | папки категорий торрент-клиента, через запятую; путь внутри контейнера |
| `SIDECAR_LIBRARY_ROOTS` | `/mnt/media/anime` | корневые папки сериалов в Sonarr, через запятую |
| `SIDECAR_SONARR_URL` | `http://host.docker.internal:8989` | порт, на котором приложение Sonarr опубликовано в TrueNAS (8989 у Sonarr по умолчанию); если не подключается — LAN IP NAS |
| `SIDECAR_SONARR_API_KEY` | | Sonarr → Settings → General → API Key |
| `SIDECAR_WEBHOOK_TOKEN` | | строка из шага 2 |
| `SIDECAR_DEFAULT_LANGUAGE` | `en`, `ru`, `de`… | язык дорожек, у которых в папках раздачи не указан язык; по умолчанию `und` (не определён) |
| `TZ` | `Europe/Berlin` | часовой пояс для времени в логах |

`SIDECAR_DRY_RUN` оставь `"true"`: в этом режиме модуль только пишет в лог, что сделал бы, и ничего не создаёт.

Все переменные описаны в разделе [Configuration](installation.md#configuration).

### 4. Проверка запуска

Приложение должно перейти в состояние Running. В shell TrueNAS:

```sh
curl -s http://localhost:8787/health
```

Ответ `{"status": "ok"}` значит, что модуль работает. Если приложение не стартует, открой его логи: при ошибке в настройках там будет сообщение с именем переменной.


## Настройка Sonarr

### Вебхук

Sonarr → Settings → Connect → + → **Webhook**:

- **Name:** `anime-sidecar`
- **Triggers:** On File Import, On File Upgrade, On Rename. On Import Complete не включай: те же события пришли бы дважды.
- **Tags:** для первой проверки — тег одного сериала. Потом тег можно убрать.
- **Webhook URL:** `http://<IP NAS>:8787/webhook/sonarr`. Адрес должен быть доступен из контейнера Sonarr; обычно подходит LAN IP NAS или адрес шлюза Docker-сети, через который Sonarr ходит к торрент-клиенту.
- **Method:** POST
- **Username / Password:** пусто.
- **Headers** (видно после Show Advanced): ключ `Authorization`, значение `Bearer <токен из шага 2>`.

Нажми **Test**: должна появиться зелёная галочка. Сохрани.

### Import Extra Files

Settings → Media Management → Import Extra Files. Если Sonarr тоже импортирует аудио и субтитры, он может занять то же имя файла, и модуль остановится с конфликтом. Убери из списка расширений всё, чем занимается модуль:

```
srt, ass, ssa, sub, idx, mka, mp3, ac3, flac, m4a, aac
```

Оставь, например, `nfo, jpg, jpeg, png`. Список общий для всех сериалов: если дорожки нужны и обычным сериалам, добавь их корневые папки в `SIDECAR_LIBRARY_ROOTS`, а папки их загрузок — в `SIDECAR_DOWNLOAD_ROOTS`.


## Проверка

### 1. Пробный импорт в dry-run

Импортируй в тестовый сериал новую серию — дождись автоматической загрузки или сделай ручной импорт (см. [сценарии](#раздача-которую-sonarr-не-нашёл-сам)).

В логах приложения (Apps → anime-sidecar → Logs) для каждой серии появится строка `INFO {"actions": [...], "notices": [...], "dry_run": true}`:

- `actions` — что будет создано: `source` (файл в раздаче) и `destination` (куда и под каким именем).
- `notices` — что пропущено и почему.

Список последних задач:

```sh
sudo docker exec $(sudo docker ps --format '{{.Names}}' | grep sidecar) anime-sidecar jobs
```

У задачи должен быть `"status": "done"`.

### 2. Включение записи

Если план выглядит правильно: Apps → anime-sidecar → Edit, поставь `SIDECAR_DRY_RUN: "false"` и сохрани.

События, которые прошли в dry-run, сами не повторяются. Чтобы применить их, верни выполненные задачи в очередь:

```sh
sudo docker exec $(sudo docker ps --format '{{.Names}}' | grep sidecar) python -c "import sqlite3; db = sqlite3.connect('/state/sidecar.sqlite3'); print(db.execute(\"UPDATE jobs SET status='queued', attempts=0, next_attempt=0 WHERE status='done'\").rowcount, 'requeued'); db.commit()"
```

Это безопасно: модуль не создаёт дубликаты и не трогает чужие файлы.

### 3. Результат

- Рядом с серией появились дорожки и папка `fonts/`.
- Это hardlinks: у дорожки и исходника в раздаче одинаковый inode (первое число), а ссылок не меньше двух (второе число):

  ```sh
  stat -c '%i %h %n' "<дорожка рядом с серией>" "<исходник в раздаче>"
  ```

- Jellyfin: Dashboard → Libraries → Scan All Libraries, затем открой серию и проверь аудио и субтитры в плеере.

Когда всё устроит, убери тег с вебхука в Sonarr — модуль начнёт работать для всех сериалов.


## Как называются дорожки

Имя собирается из папок раздачи: обозначения языка (`ENG`, `English`, `[rus]`, `German`…) становятся языком дорожки, служебные слова (`Sound`, `Audio`, `Dub`, `Subs`, `Озвучка`, `Full`, `ass`, `5.1`, `DTS`...) отбрасываются, остаётся название группы. Языки распознаются по кодам ISO и названиям на английском и русском.

| Путь в раздаче | Суффикс рядом с серией |
| --- | --- |
| `Audio/English Dub/…mka` | `.en.mka` |
| `German Dub/…mka` | `.de.mka` |
| `Sounds/Netflix [eng]/…mka` | `.Netflix.en.mka` |
| `ENG Subs/Crunchyroll ENG/…ass` | `.Crunchyroll.en.ass` |
| `Subs/Commie [eng]/…ass` | `.Commie.en.ass` |
| `RuSound/Suzaku&SPAWN2/…mka` | `.Suzaku&SPAWN2.ru.mka` (слитные `RuSound`, `EngSubs` тоже понимаются) |
| `[Subtitle]/AleX_MytH & Hollow/…ass` | `.AleX_MytH & Hollow.<язык>.ass` |
| `Subs/GJM/Signs & Songs/…ass` | `.GJM Signs.<язык>.ass` |
| `Sound/AniLibria/…mka` | `.AniLibria.<язык>.mka` (язык из `SIDECAR_DEFAULT_LANGUAGE`) |
| `RUS Sound/JAM Club/…mka` | `.JAM Club.ru.mka` |
| `RUS Sound [Студийная банда]/…mka` | `.Студийная банда.ru.mka` |
| `Russian Subtitles [Reanimedia]/Full [ass]/…ass` | `.Reanimedia.ru.ass` |
| `Subs/SovetRomantica/Надписи/…ass` | `.SovetRomantica Signs.<язык>.ass` |
| `RUS Subs/…ass` | `.ru.ass` |

Надписи распознаются по словам `Signs`, `Inscriptions`, `Надписи`. `<язык>` — значение `SIDECAR_DEFAULT_LANGUAGE`, если в пути язык не указан.

Папки бонусов (`Extras`, `Special`, `SPs`, `Omake`, `BD menu`, `Scan`, `BDCG`, `NCOP`, `OST`...) пропускаются. Если у двух дорожек получилось одинаковое имя, ко второй добавляется номер.

Название и язык можно переопределить правилами, см. [ниже](#свои-названия-и-языки).


## Типичные сценарии

### Обычный импорт

Sonarr сам находит раздачу, скачивает и импортирует серию — модуль срабатывает по вебхуку и раскладывает дорожки. Ничего делать не нужно.

### Раздача, которую Sonarr не нашёл сам

Sonarr ищет по названиям из своих баз, поэтому раздачи, где сериал назван иначе — локализованным названием, на другом языке или с другим годом, — часто не находит. Тогда:

1. **Prowlarr:** найди раздачу вручную и нажми Grab. Отправь её в категорию торрент-клиента, папка которой есть в `SIDECAR_DOWNLOAD_ROOTS`, иначе модуль её не увидит.
2. **Sonarr:** когда раздача скачалась, нажми Manual Import на странице сериала или в Wanted → Missing (либо открой её в Activity → Queue, если она появилась в очереди), укажи папку раздачи, проверь, что серии сопоставились с нужными эпизодами, и импортируй.
3. Sonarr отправит тот же вебхук, что и при автоматическом импорте, и модуль подтянет дорожки.

Имена файлов в раздаче могут быть любыми (`… - S01E04 [1080p].mkv`, `ReLIFE 04 [BDRip].mkv`): модуль связывает дорожки с исходным видео, а не с названием серии в Sonarr.

### Замена на лучшую версию

Когда Sonarr заменяет серию лучшим релизом (On File Upgrade), модуль подключает дорожки новой раздачи и убирает свои старые ссылки. Если в новой раздаче дорожек нет, старые остаются, а в логе появляется `Old sidecars retained`: дорожки от другого релиза могут быть рассинхронизированы, поэтому решение остаётся за тобой.

### Переименование

Если Sonarr переименовал или переместил серию (On Rename), модуль переносит её дорожки и шрифты под новое имя. Раздача для этого уже не нужна.

### Свои названия и языки

Для правил нужен `config.toml`. Создай папку `config` рядом с `state`, положи туда файл и раскомментируй в YAML строку с `/config`:

```toml
# Название группы, которое иначе получилось бы неудачным.
[[rules]]
pattern = '(?i)funi(mation)?'
group = "Funimation"
language = "en"

# Папка без языковой метки, где лежит японская дорожка.
[[rules]]
pattern = '(?i)Audio 5\.1'
language = "ja"
```

`pattern` — регулярное выражение по пути внутри раздачи; более поздние правила важнее. Пример с комментариями — [config.example.toml](../deploy/config.example.toml).

### OVA и нестандартная нумерация

Если в логе `Unrecognized sidecar, add a mapping: <путь>`, модуль не смог понять, к какой серии относится файл (например, `Show - 13 OVA.ass`). Укажи это вручную в `config.toml`:

```toml
[mappings]
"Release/Subs/Show - 13 OVA.ass" = [1234]
```

Ключ — путь от папки категории загрузок, значение — ID эпизода в Sonarr (не номер серии). ID видно в Sonarr API: `/api/v3/episode?seriesId=<ID сериала>` с заголовком `X-Api-Key`.

### Шрифты в Jellyfin

Модуль собирает шрифты в `.anime-sidecar-fonts`, но Jellyfin сам их не использует. Как подключить — в разделе [Jellyfin fonts](installation.md#jellyfin-fonts). Учти лимит 20 МБ для fallback-шрифтов в веб-клиенте: шрифты одной раздачи бывают больше.


## Частые проблемы

**Sonarr: `401 Unauthorized` при Test.** Модуль доступен, но заголовок не совпал. Проверь, что в Headers ключ `Authorization`, значение `Bearer <токен>` через один пробел, токен совпадает с `SIDECAR_WEBHOOK_TOKEN`, а Username и Password пустые.

**Sonarr: не может подключиться при Test.** Адрес недоступен из контейнера Sonarr. Попробуй LAN IP NAS вместо `172.16.x.x` или наоборот; проверь, что порт 8787 опубликован в YAML.

**Приложение не стартует.** Открой логи: сообщение называет проблему, например `library_roots is required (SIDECAR_LIBRARY_ROOTS or config.toml)` или `Webhook token must contain at least 32 characters`.

**`PermissionError` / `Permission denied`.** Файлы и папки модуля должны принадлежать UID 568. Если секреты или `config.toml` лежат в файлах, исправь владельца изнутри root-shell, чтобы раскрылась маска:

```sh
sudo sh -c 'chown 568:568 /mnt/tank/apps/anime-sidecar/config/*'
```

**Дорожки не появились, в логе `"dry_run": true`.** Включён тестовый режим, см. [Включение записи](#2-включение-записи).

**Задача в `jobs` со статусом `failed`.** В поле `error` и в логе приложения (`job=… failed: …`) записаны тип ошибки и её текст, например `UnsafePath: Outside allowed roots: /mnt/media/...`. Модуль повторяет задачу до шести раз с растущей паузой, после этого она остаётся `failed`. Частые причины:

| Ошибка | Что значит |
| --- | --- |
| `FileExistsError` | на месте дорожки уже лежит чужой файл — чаще всего его создал Sonarr через Import Extra Files; убери расширения из списка и удали дубликат |
| `PermissionError` | у UID 568 нет прав на запись в папку сериала или шрифтов |
| `ValueError` (`Cross-filesystem hardlink forbidden`) или `OSError` (`EXDEV`) | загрузки и медиатека в разных datasets или подключены разными mount'ами, см. [storage.md](storage.md) |
| `UnsafePath: Outside allowed roots` | раздача скачана в папку, которой нет в `SIDECAR_DOWNLOAD_ROOTS` (другая категория торрент-клиента), или сериал лежит вне `SIDECAR_LIBRARY_ROOTS` — добавь папку в нужную переменную |
| `UnsafePath: Loose files require a dedicated release directory` | видео лежит прямо в папке категории, без своей папки раздачи |
| `URLError` / `HTTPError` | модуль не достучался до Sonarr: проверь `SIDECAR_SONARR_URL` и API-ключ |

После исправления верни задачу в очередь:

```sh
sudo docker exec $(sudo docker ps --format '{{.Names}}' | grep sidecar) anime-sidecar retry <id>
```

**Уведомления в отчёте:**

| Уведомление | Что делать |
| --- | --- |
| `Unrecognized sidecar, add a mapping` | файл не удалось связать с серией — добавь [mapping](#ova-и-нестандартная-нумерация) |
| `Ambiguous video association` | в раздаче несколько видео подходят к одной дорожке — добавь mapping |
| `Global font family/style conflict, local only` | в разных раздачах разные версии одного шрифта; новая версия лежит только в `fonts/` рядом с серией. Обычно ничего делать не нужно |
| `Invalid font` | файл шрифта повреждён и пропущен |
| `Old sidecars retained` | новый релиз сопоставился не полностью, старые дорожки оставлены — проверь вручную |

**Язык дорожки `und`.** В папках раздачи нет языковой метки, и не задан `SIDECAR_DEFAULT_LANGUAGE`.

**Jellyfin не показывает дорожки.** Запусти сканирование библиотеки. Проверь, что имя дорожки начинается ровно с имени видео (`S01E01 - … .mkv` → `S01E01 - … .AniLibria.ru.mka`).
