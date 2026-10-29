# TrueNAS installation

Все пути, адреса и порты ниже — примеры. Свои значения задаются переменными окружения `SIDECAR_*`: в `deploy/.env` (Compose) или в разделе `environment` файла `deploy/truenas-custom-app.yaml` (Install via YAML). Необязательный `config.toml` нужен только для `rules` и `mappings`. Требования к datasets описаны в [storage.md](storage.md).


## Storage preparation

Нужны каталоги:

| Host path (пример) | Container path | Access |
| --- | --- | --- |
| `MEDIA_PATH`, например `/mnt/tank/media` | `/mnt/media` | RW, один общий bind mount |
| `STATE_PATH`, например `/mnt/tank/apps/anime-sidecar/state` | `/state` | RW |
| `CONFIG_PATH` (необязательно), например `/mnt/tank/apps/anime-sidecar/config` | `/config` | RO |

`MEDIA_PATH` — dataset, в котором лежат и загрузки, и медиатека. Конфигурацию не помещать внутрь управляемых `ix-apps` datasets других приложений. Отдельный dataset для state и config допустим. `font_root` (по умолчанию `/mnt/media/.anime-sidecar-fonts`) должен оставаться **обычной директорией внутри `MEDIA_PATH`**, не отдельным dataset.

Через TrueNAS создайте каталог state (и config, если он нужен) с правом записи для UID:GID 568:568; для директорий 0700 или более узкий подходящий ACL. Не менять рекурсивно права существующей медиатеки.


## Configuration

Настройки задаются переменными окружения; переменная всегда важнее того же ключа в `config.toml`. Списки — через запятую или JSON-массивом, если в пути есть запятая. Пути указываются так, как их видит контейнер (`/mnt/media/...`).

| Переменная | Обязательна | Значение |
| --- | --- | --- |
| `SIDECAR_DOWNLOAD_ROOTS` | да | папки категорий загрузок, например `/mnt/media/downloads/anime` |
| `SIDECAR_LIBRARY_ROOTS` | да | корневые папки Sonarr, куда можно класть дорожки |
| `SIDECAR_FONT_ROOT` | да | общая библиотека шрифтов внутри того же dataset |
| `SIDECAR_SONARR_URL` | да | адрес Sonarr из контейнера: LAN IP NAS или `host.docker.internal`, с портом |
| `SIDECAR_SONARR_API_KEY` или `SIDECAR_SONARR_API_KEY_FILE` | да | ключ из Sonarr Settings > General, значением или путём к файлу |
| `SIDECAR_WEBHOOK_TOKEN` или `SIDECAR_WEBHOOK_TOKEN_FILE` | для сервера | случайная строка от 32 символов, например `openssl rand -hex 32` |
| `SIDECAR_DRY_RUN` | нет, `true` | `false` включает запись в медиатеку |
| `SIDECAR_DEFAULT_LANGUAGE` | нет, `und` | язык дорожек без языковой метки, например `en` или `ru` |
| `SIDECAR_DATABASE` | нет | по умолчанию `/state/sidecar.sqlite3` |
| `SIDECAR_LOG_LEVEL` | нет, `INFO` | `DEBUG` добавляет в лог полный JSON-отчёт по каждой задаче |
| `SIDECAR_API_VERSION`, `SIDECAR_PORT`, `SIDECAR_MAX_FILES` | нет | `v3`, `8787`, `20000` |
| `SIDECAR_CONFIG` | нет | путь к `config.toml`, по умолчанию `/config/config.toml`, если файл есть |

`config.toml` ([пример](../deploy/config.example.toml)) кладётся в каталог, подключённый как `/config`, и содержит `rules` и `mappings`. В нём можно задать и все остальные настройки — это удобно для обычного Docker без переменных.

Секреты в `environment` видны в настройках приложения TrueNAS и в `docker inspect` — так же, как у других приложений каталога. Если это нежелательно, используйте варианты `_FILE` с файлом 0600, владелец 568, и не добавляйте эти файлы в Git.

Настройки читаются при старте: после изменения переразверните приложение (TrueNAS: Edit > Save) или перезапустите контейнер.


## Build and Custom App

Образ собирается GitHub Actions ([publish.yml](../.github/workflows/publish.yml)) и публикуется в GitHub Container Registry как `ghcr.io/dd/sidecarr-extras-import`:

| Тег | Когда обновляется |
| --- | --- |
| `0.1.0`, `0.1`, `latest` | git-тег `v0.1.0`, версия должна совпадать с `pyproject.toml` |
| `dev`, `main` | каждый push в соответствующую ветку, для тестов |
| `sha-<commit>` | каждая сборка |

Для постоянной установки указывайте конкретную версию, а не `latest` или `dev`. Если пакет в GHCR приватный, сделайте его публичным (GitHub > Packages > package settings) или добавьте учётные данные реестра в TrueNAS (Apps > Configuration > Manage Container Images / Docker Registries). Версию внутри контейнера показывает `anime-sidecar --version`.

Собрать образ локально без реестра тоже можно: `docker build -t ghcr.io/dd/sidecarr-extras-import:dev .`

**Install via YAML.** Apps > Discover Apps > меню > Install via YAML. Имя приложения: `anime-sidecar`. Скопируйте `deploy/truenas-custom-app.yaml`, замените все пути `/mnt/tank/...`, адрес Sonarr и оба секрета своими, при необходимости тег образа, порт и `TZ`, затем вставьте целиком. Install via YAML не читает `.env`, поэтому значения задаются прямо в YAML. Обновление версии — смена тега образа в настройках приложения.

**Docker Compose вне TrueNAS:**

```sh
cp deploy/.env.example deploy/.env
docker compose -f deploy/compose.yaml config --quiet
docker compose -f deploy/compose.yaml up -d
```

`up -d` скачивает образ с тегом `IMAGE_TAG`; `up -d --build` собирает его из checkout. Не запускайте одновременно Compose и TrueNAS Custom App для одной базы.

Порт webhook (`WEBHOOK_PORT`, по умолчанию 8787) должен быть свободен. Это внутренний API без пользовательского UI; не публикуйте его через внешний reverse proxy. Webhook использует bearer token по HTTP внутри доверенной сети. Если трафик проходит через недоверенную сеть, требуется HTTPS на reverse proxy.


## Sonarr webhook

Предварительно просмотрите dry-run отчёт. Затем:

1. Sonarr > Settings > Connect > Webhook.
2. URL: `http://<адрес NAS>:<WEBHOOK_PORT>/webhook/sonarr`. Адрес должен быть доступен из контейнера Sonarr: LAN IP NAS или адрес шлюза Docker-сети, через который Sonarr уже обращается к другим приложениям. Проверьте кнопкой Test.
3. Method: POST.
4. Advanced Headers: ключ `Authorization`, значение `Bearer <SIDECAR_WEBHOOK_TOKEN>`. Поля Username/Password оставьте пустыми.
5. Включить события On Import / On Upgrade / On Rename. Основной вариант — уведомление для каждого импортированного файла с `episodeFile.sourcePath`. Поддерживается также Import Complete с `episodeFiles` и общим `sourcePath`: исходные файлы тогда берутся из истории импорта Sonarr.
6. Для первой проверки ограничить connection тегом одного сериала. Приложение пишет только внутри `library_roots`.
7. Нажать Test. HTTP 200 подтверждает доступность и token, но не проверяет сопоставление медиа и Sonarr API. HTTP 202 на рабочем событии означает постановку в очередь, а не успешное завершение импорта.

**Перед включением записи** проверьте Sonarr Settings > Media Management > Import Extra Files: см. [storage.md](storage.md#sonarr-settings-that-matter).


## Dry run and enabling writes

Сначала оставить `SIDECAR_DRY_RUN=true`. Для ручной проверки подготовьте JSON события с фактическими Sonarr series ID, episodeFile ID и оригинальным sourcePath. Номера берутся из Sonarr API, не из имени видео:

```json
{
  "eventType": "Download",
  "series": {"id": 11},
  "episodeFile": {
    "id": 12345,
    "sourcePath": "/mnt/media/downloads/anime/Release/Original - 01.mkv"
  }
}
```

`11`, `12345` и путь — placeholders. Сохраните файл как `<CONFIG_PATH>/event.json` (каталог config нужно подключить). Чтобы выполнить CLI, временно остановите только anime-sidecar: CLI event и server используют один lock. Из checkout с Compose-файлом:

```sh
sudo docker compose -f deploy/compose.yaml run --rm --no-deps anime-sidecar event /config/event.json
```

CLI `event` всегда dry-run, если не указан `--apply`; `--apply` также не отменяет `SIDECAR_DRY_RUN=true`. В отчёте проверьте sources/destinations, язык/группу, пропуски OVA и конфликты шрифтов. Dry-run не создаёт директории медиатеки, но может создать служебную SQLite и lock.

После проверки конфликтов установить `SIDECAR_DRY_RUN=false`, переразвернуть anime-sidecar и повторно подать событие либо запустить `event /config/event.json --apply`. События, уже завершённые в dry-run, **не выполняются повторно автоматически**. Это предотвращает неожиданный массовый импорт при смене режима.

Проверить `stat` у созданной ссылки и исходника: одинаковые device/inode, link count >= 2. Jellyfin может потребовать Scan Media Library для обнаружения новых внешних дорожек.


## Jellyfin fonts

Если Jellyfin подключает тот же `MEDIA_PATH` (например, как `/media`), общая библиотека шрифтов уже видна ему как `/media/.anime-sidecar-fonts`. Изменение образа Jellyfin не требуется.

Различаются два механизма:

- **Server-side rendering:** добавить к Jellyfin read-only mount `<MEDIA_PATH>/.anime-sidecar-fonts:/usr/local/share/fonts/anime-sidecar:ro`. Перезапустить Jellyfin и проверить обнаружение конкретной family через `fc-match`/`fc-list`. Наличие файла в media/fonts само по себе не регистрирует его в fontconfig.
- **Web ASS fallback:** в Jellyfin Playback/Transcoding включить Fallback Fonts и указать каталог. По документации Jellyfin общая загрузка fallback ограничена **20 MB**, все файлы заранее отправляются браузеру. Не направлять fallback на всю растущую общую библиотеку без проверки размера. Лучше создать отдельный небольшой curated-каталог в том же dataset, используя только hardlinks выбранных шрифтов.

`fonts/` рядом с видео хранит оригинальные шрифты релиза, но не гарантирует, что каждый Jellyfin-клиент автоматически их подхватит. Разные версии одной family, клиентские ограничения и порядок приоритета шрифтов требуют проверки конкретного воспроизведения. Приложение не меняет внутренние family и не перекодирует шрифты.


## Operations and upgrades

В UI TrueNAS смотрите логи anime-sidecar. Имя контейнера можно получить через `docker ps`. Изнутри установленного контейнера:

```sh
anime-sidecar jobs
anime-sidecar retry 123
```

`jobs` показывает последние 100 событий и ошибки. `retry` возвращает failed job в очередь. Для полного dry-run отчёта повторите соответствующий payload через `event`. Payload не содержит API key/token, но содержит имена файлов и хранится без автоматического удаления; планируйте резервное копирование и размер state.

Перед обновлением остановить anime-sidecar, сохранить config и **всю директорию state**, включая SQLite WAL/SHM, если они остались. Обновить образ, запустить сначала с dry-run. Используются Docker bind mounts, общедоступный REST API Sonarr и собственное постоянное хранилище, а не внутренние БД сервисов или `.ix-apps`.

Ошибки `EXDEV` означают неверные datasets/mounts; `EACCES` — права/ACL. Исправлять конфигурацией, не включать fallback на копирование и не выдавать контейнеру privileged/root.

Удаление приложения не должно удалять state и media. Оно не выполняет автоматический массовый rollback: hardlinks занимают тот же inode, а удалять можно только подтверждённые неизменённые пути из журнала. Восстановление из backup базы безопаснее сброса состояния.
