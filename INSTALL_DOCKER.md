# Установка бота «Обед в столовой» на Ubuntu (Docker)

Руководство для системного администратора. Время установки — 15–20 минут.

## 1. Кратко о программе

| | |
|---|---|
| Запуск | один контейнер, `docker compose` |
| Данные | SQLite-файл `data/lunch.db` на диске сервера (каталог `./data` монтируется в контейнер как `/data`) |
| Сеть | только **исходящий HTTPS (443)**: `api.telegram.org`, при сборке ещё `pypi.org` и Docker Hub. Входящие порты, домен и белый IP **не нужны** |
| Ресурсы | ~60 МБ ОЗУ, образ ~230 МБ |

**Важно:** один токен — один запущенный экземпляр. Если бот уже работает где-то ещё (например, на Windows-сервере),
остановите его там. Иначе Telegram будет выдавать `Conflict: terminated by other getUpdates request`.

## 2. Что получить у владельца бота

1. **Токен бота** вида `7123456789:AAH...`. Не публикуйте его.
2. **Доступ к коду**: репозиторий `github.com/Zayn1ddinov/lunch-bot` приватный. Нужен один из вариантов:
   - ваш GitHub-аккаунт в *Settings → Collaborators*;
   - **deploy key** только для чтения. Сгенерируйте ключ на сервере (`ssh-keygen -t ed25519`), а владелец добавит публичный ключ
     в *Settings → Deploy keys*. Клонировать тогда через `git@github.com:Zayn1ddinov/lunch-bot.git`;
   - или архив (*Code → Download ZIP*), который нужно скопировать на сервер.
3. Владелец в @BotFather должен выполнить `/setprivacy` → бот → **Disable**. Без этого бот не видит «+» в группе.

## 3. Docker

Если Docker уже стоит, проверьте версии и переходите к шагу 4:
```bash
docker --version
docker compose version     # нужен плагин compose v2 (команда с пробелом)
```

Установка на чистую Ubuntu (официальный скрипт Docker):
```bash
curl -fsSL https://get.docker.com | sudo sh
sudo systemctl enable --now docker
```

## 4. Установка

```bash
sudo git clone https://github.com/Zayn1ddinov/lunch-bot.git /opt/lunch-bot
cd /opt/lunch-bot

sudo cp .env.example .env
sudo nano .env
sudo chmod 600 .env          # в файле токен
```

В `.env` на первом шаге достаточно токена:
```
BOT_TOKEN=7123456789:AAH...
GROUP_CHAT_ID=
KITCHEN_CHAT_ID=
ADMIN_IDS=
TIMEZONE=Asia/Tashkent
```
Строки `DB_PATH` и `POLL_TIME` можно оставить как есть: путь к базе в Docker задаётся в `docker-compose.yml`,
а время опросов потом меняется в панели бота.

Сборка и запуск:
```bash
sudo docker compose up -d --build
sudo docker compose logs -f          # выход: Ctrl+C, бот продолжит работать
```
Нормальный запуск выглядит так:
```
WARNING GROUP_CHAT_ID / KITCHEN_CHAT_ID не заданы — опросы не запускаются ...
INFO Start polling
INFO Run polling for bot @имя_бота ...
```
Предупреждение про `GROUP_CHAT_ID` на этом шаге — норма.

## 5. ID групп и администраторов

Это делает владелец бота или администратор группы в Telegram:

1. Добавить бота в **группу сотрудников** и назначить **администратором** (право закреплять сообщения).
   Написать в группе `/chatid`. Число вида `-100…` записать в `GROUP_CHAT_ID`.
2. Добавить бота в **группу кухни** и написать `/chatid`. Число записать в `KITCHEN_CHAT_ID`.
3. Написать боту **в личку** `/chatid`. «ваш user_id» записать в `ADMIN_IDS` (несколько ID — через запятую).
   Администраторы группы сотрудников получают доступ к панели автоматически.

Применить изменения `.env`:
```bash
sudo docker compose up -d --force-recreate
```
Предупреждения про `GROUP_CHAT_ID` в логе больше быть не должно.

## 6. Автозапуск

Уже настроен: в `docker-compose.yml` стоит `restart: unless-stopped`, а Docker включён в автозагрузку командой `systemctl enable docker`.
После перезагрузки сервера и при падении бота контейнер поднимется сам.

## 7. Проверка

1. В личке с ботом: `/admin`. Должна открыться панель.
2. **▶️ Открыть опрос**. В группе сотрудников появится опрос.
3. Проголосуйте **«Буду обедать»**, затем в панели нажмите **📤 Отправить на кухню**. Заявка придёт в группу кухни.
4. Каждый администратор должен один раз нажать **Start** у бота в личке, иначе бот не сможет присылать ему скриншоты оплат.

## 8. Повседневные команды

Все команды выполняются из `/opt/lunch-bot`:

| Действие | Команда |
|---|---|
| Статус | `sudo docker compose ps` |
| Логи (живые) | `sudo docker compose logs -f` |
| Логи за сутки | `sudo docker compose logs --since 24h` |
| Перезапуск | `sudo docker compose restart` |
| Остановить / запустить | `sudo docker compose stop` / `sudo docker compose up -d` |

Логи хранит Docker, их размер ограничен: 5 файлов по 10 МБ.

## 9. Резервное копирование (обязательно)

В `data/lunch.db` хранятся все обеды, оплаты и долги сотрудников.

Копия вручную (бот при этом может работать):
```bash
sudo docker compose exec -T bot python backup.py /data/backups
```
Копия появится в `/opt/lunch-bot/data/backups/lunch_ГГГГ-ММ-ДД.db`, хранятся последние 30.

Ежедневно в 23:00 — `sudo crontab -e` и добавить строку:
```
0 23 * * * cd /opt/lunch-bot && docker compose exec -T bot python backup.py /data/backups >> /var/log/lunch-bot-backup.log 2>&1
```
Желательно дополнительно копировать `data/backups/` на другой сервер или в сетевое хранилище.

Восстановление:
```bash
sudo docker compose stop
sudo cp data/backups/lunch_2026-10-30.db data/lunch.db
sudo docker compose up -d
```

## 10. Обновление

```bash
cd /opt/lunch-bot
sudo git pull
sudo docker compose up -d --build
```
Данные (`data/`) и настройки (`.env`) при обновлении не затрагиваются.

## 11. Перенос с другого сервера (например, с Windows)

1. Остановите бота на старом сервере.
2. Скопируйте с него `lunch.db` в `/opt/lunch-bot/data/lunch.db`, а токен и ID — в `.env`.
3. `sudo docker compose up -d`.

## 12. Если что-то не работает

| Признак | Причина и решение |
|---|---|
| `KeyError: 'BOT_TOKEN'` | В `.env` нет `BOT_TOKEN` или файл лежит не в `/opt/lunch-bot` |
| `Unauthorized` / `Not Found` | Неверный токен: скопируйте заново из @BotFather, без пробелов и кавычек |
| `Conflict: terminated by other getUpdates request` | Бот запущен ещё где-то (другой сервер, Windows, второй контейнер) |
| `Cannot connect to host api.telegram.org` | Нет исходящего доступа к `api.telegram.org:443`: проверьте фаервол/DNS. Проверка: `curl -I https://api.telegram.org` |
| Сборка: `429 Too Many Requests` от Docker Hub | Лимит анонимных скачиваний: `docker login` или повторить позже |
| Сборка: ошибка SSL при `pip install` | Сервер ходит в интернет через корпоративный прокси. Передайте прокси при сборке: `sudo docker compose build --build-arg HTTPS_PROXY=http://прокси:порт` |
| Контейнер постоянно перезапускается | Посмотрите причину: `sudo docker compose logs --tail 50` |
| Бот не реагирует на «+» в группе | @BotFather → `/setprivacy` → Disable, затем удалить бота из группы и добавить снова |
| Опрос не закрепляется | Бот не администратор группы |
| Админу не приходят скриншоты оплат | Админ не нажимал Start у бота в личке |
| Опрос не в то время | Время меняется в `/admin` → Настройки; часовой пояс — `TZ` в `docker-compose.yml` и `TIMEZONE` в `.env` |

## Файлы

| Путь | Что это |
|---|---|
| `/opt/lunch-bot/.env` | токен и ID групп (права 600) |
| `/opt/lunch-bot/data/lunch.db` | **все данные** — делайте копии |
| `/opt/lunch-bot/data/backups/` | резервные копии |
| `docker-compose.yml`, `Dockerfile` | описание контейнера |
