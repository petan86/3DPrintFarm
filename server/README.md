# Server pro Raspberry Pi

První API prototyp spravuje tiskárny, telemetrii a frontu G-code souborů.
Používá FastAPI a SQLite; API klíče tiskáren ukládá jen jako SHA-256 otisky.

## Lokální spuštění

```powershell
python -m venv .venv-server
.\.venv-server\Scripts\pip.exe install -r server\requirements.txt
$env:FARM_ADMIN_KEY = "vygenerujte-dlouhy-nahodny-klic"
$env:FARM_DATA_DIR = ".\server\data"
.\.venv-server\Scripts\uvicorn.exe server.farm_server.main:app --reload
```

Dokumentace API bude na `http://127.0.0.1:8000/docs`.

## Základní protokol

- Administrace posílá hlavičku `X-Admin-Key`.
- Každá tiskárna dostane při registraci vlastní jednorázově zobrazený klíč.
- ESP32 posílá svůj klíč v hlavičce `X-Printer-Key`.
- Stav `waiting_local_approval` označuje čekání před první vrstvou.
- Podrobný cyklus agenta je v `../PROTOKOL_ESP32.md`.

Data v `server/data` jsou provozní a nepatří do Git repozitáře.
