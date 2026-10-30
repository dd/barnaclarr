# Verification report

## Automated tests

80 тестов проходят локально на Python 3.14 (запуск в Docker на Python 3.12 после последних изменений не повторялся). Тесты используют временные каталоги, in-memory Sonarr и HTTP-буферы; внешние сервисы из тестов не вызываются.

Покрыты:

- рекурсивные вложенные каталоги, SxxExx, абсолютная нумерация, Specials, ручные mappings;
- безопасный отказ для неоднозначных/диапазонных имён, нескольких видео на один номер;
- language/group/forced/signs/SDH, неизвестный язык, `default_language`, порядок title/language для Jellyfin;
- раскладки реальных раздач (`tests/test_layouts.py`): `Sound/<группа>`, `RUS Sound [группа]`, `Sounds/<группа> [rus]`, `Russian Subtitles [группа]/Full [ass]`, `Надписи`/`Inscriptions`, бонусные папки `Special`/`Extras`/`BD menu`, нумерация соседних файлов, импорт папкой через историю Sonarr;
- настоящие inode hardlinks, повторная обработка, dry-run без media-записи;
- занятые назначения, чужие файлы, ручная замена ранее созданной ссылки;
- Rename после удаления исходника, перенос локальных fonts, обновление релиза;
- IDX/SUB с одинаковым stem, uppercase extensions, обновление только SUB, отсутствующая пара;
- TTF, коллекция TTC с несколькими family, одинаковые шрифты, конфликт family/style, повреждённый шрифт;
- symlink source/destination, изменение исходника после планирования, EXDEV и EACCES без копирования;
- восстановление link-журнала после прерывания;
- Webhook auth, Test, HTTP 202 после сохранения, невалидные и слишком большие payloads;
- восстановление очереди и окончательный failed после лимита повторов.

`ruff check .`, `ruff format --check .` и `git diff --check` проходят. CI выполняет pytest, Ruff и Docker build.


## NAS integration

Проверялось на TrueNAS SCALE 25.10 с Sonarr 5.0.0.2701 и Jellyfin 12.2:

- Образ собран на NAS (Python 3.12, linux/amd64). Проверенный base digest: `python:3.12-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70`.
- Контейнер тестов: UID:GID 568:568, read-only root, capabilities dropped, no-new-privileges.
- Тесты файловых операций запускались на временном каталоге внутри media dataset, подключённого одним bind mount, то есть на реальном ZFS через Docker.
- Отдельная проверка `link(2)` подтвердила успех для одного mount и `EXDEV` для двух mounts/разных datasets: см. [storage.md](storage.md).
- YAML-файлы проходят `docker compose config --quiet`.
- Dry-run на реальном эпизоде из медиатеки построил план из аудио/субтитров и шрифтов без записи в медиатеку (она была подключена `:ro`). Источник выбирался по совпадению inode с импортированным Sonarr видео.
- Smoke-test HTTP-сервера в контейнере с `--network=none`: `/health` и авторизованный Webhook Test вернули HTTP 200.

Эти проверки выполнялись до переработки разбора структуры раздач, вынесения путей в `.env` и добавления `tzdata` в образ. Образ после этих изменений на NAS не пересобирался.


## Limits of verification

Production Custom App, рабочий Sonarr Webhook и подключение шрифтов Jellyfin не настраивались. Реальный импорт в медиатеку и воспроизведение на клиентах Jellyfin не выполнялись. Успешный dry-run не подтверждает совместимость каждой дорожки с каждым клиентом.

Фактический Sonarr v5 проверен через API v3; Sonarr v4 проверяется по контракту API и тестовым payloads, отдельного живого экземпляра v4 здесь нет. Изображения/бинарные шрифты из личных релизов не включены в fixtures. Тест OTF разбора опирается на fontTools и реальный dry-run с OTF, синтетические unit fixtures используют TTF/TTC.
