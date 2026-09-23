# Локальные службы L Constructor

Локальный MVP работает двумя пользовательскими службами macOS:

- `com.openai.lconstructor.web` — production web на `http://127.0.0.1:3010`;
- `com.openai.lconstructor.cad` — закрытый DXF API на `http://127.0.0.1:3001`.
- `com.openai.lconstructor.cloudflare` — публичный Cloudflare Quick Tunnel к веб‑порту.

Обе службы запускаются при входе пользователя в macOS и имеют `KeepAlive`, поэтому
`launchd` поднимает их снова после падения. Веб‑процесс обращается к CAD только через
same-origin маршрут `/api/cad/*`.

## Проверка

```bash
curl http://127.0.0.1:3010/
curl http://127.0.0.1:3001/healthz
launchctl print gui/$(id -u)/com.openai.lconstructor.web
launchctl print gui/$(id -u)/com.openai.lconstructor.cad
```

## Перезапуск

```bash
launchctl kickstart -k gui/$(id -u)/com.openai.lconstructor.web
launchctl kickstart -k gui/$(id -u)/com.openai.lconstructor.cad
```

Логи находятся в `/Users/dmitrijmitin/Library/Logs/LConstructor/`.

Публичный адрес Quick Tunnel печатается в `cloudflare.log`. Бесплатный Quick Tunnel
не закрепляет адрес: после перезапуска Cloudflare выдаёт новый `trycloudflare.com` URL.
Для постоянной ссылки нужен Cloudflare Tunnel с аккаунтом и доменом.

CAD‑служба запускается из рабочей копии в
`/Users/dmitrijmitin/Library/Application Support/LConstructor/cad-runtime`.
Это нужно из‑за macOS TCC: системный Python не может читать исходники из `Documents`.
При изменении CAD‑скриптов или шаблонов рабочую копию нужно обновить перед перезапуском.
