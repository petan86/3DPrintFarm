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

Dashboard bude na `http://127.0.0.1:8000/`.

## Docker na Raspberry Pi

Z kořene repozitáře:

```bash
cp server/.env.example .env
sed -i "s/^FARM_ADMIN_KEY=.*/FARM_ADMIN_KEY=$(openssl rand -hex 32)/" .env
# Kontejner běží jako UID 10001 a musí mít právo zapisovat do server/data.
mkdir -p server/data && sudo chown -R 10001:10001 server/data
docker compose up -d --build
docker compose ps
```

Data a G-code zůstávají na hostiteli v `server/data`. Kontejner běží pod
neprivilegovaným uživatelem a po restartu Raspberry Pi se automaticky spustí.
Pokud kontejner hned po startu padá a dashboard se nenačte, zkontrolujte
vlastníka `server/data` (`ls -ln server`).

## Automatická aktualizace z GitHub releasů

Raspberry Pi může samo nasazovat nové verze. Časovač systemd každých
15 minut zjistí nejnovější publikovaný release na GitHubu a pokud je jiný než
nasazená verze, přepne repozitář na jeho tag, znovu sestaví kontejner
a počká, až bude `healthy`. Když nová verze nenaběhne, vrátí se automaticky
na předchozí.

Instalace (jednou, z kořene repozitáře):

```bash
sudo server/deploy/install-auto-update.sh
sudo systemctl start printfarm-update.service   # okamžitá kontrola
journalctl -u printfarm-update.service -n 50    # log aktualizací
```

Vydání nové verze (na PC, po pushnutí změn do `main`):

```bash
gh release create v0.2.0 --generate-notes
```

V dashboardu v **Nastavení → Verze a aktualizace** je vidět nainstalovaná
verze, nejnovější release na GitHubu a výsledek poslední aktualizace.
Tlačítko **Nainstalovat** spustí aktualizaci hned: server zapíše
`server/data/updater/request` a jednotka `printfarm-update.path` spustí
stejný skript jako časovač.

Pre-releasy a koncepty se nenasazují. Při použití hotspotu přidejte do `.env`
řádek `COMPOSE_FILE=compose.yaml:compose.hotspot.yaml`, aby aktualizace
používala stejnou konfiguraci. Vypnutí:
`sudo systemctl disable --now printfarm-update.timer`.

## Přihlášení, heslo a host

Do dashboardu se přihlašuje heslem administrátora. Na začátku jím je
`FARM_ADMIN_KEY` ze souboru `.env`. V **Nastavení → Přístup** lze nastavit
vlastní heslo (ukládá se jako PBKDF2 hash v databázi). Od té chvíle klíč
z `.env` neplatí. Zapomenuté heslo se smaže a znovu začne platit klíč z `.env`:

```bash
docker compose exec farm-server python -m server.farm_server.reset_admin_password
```

Host se přihlásí bez hesla a může jen prohlížet farmu, úlohy, filamenty
a historii. Server mu povolí jen čtecí požadavky. Přihlášení hosta lze
v **Nastavení → Přístup** vypnout.

## Wi-Fi hotspot pro ESP32

Hotspot se nastavuje v dashboardu v sekci **Nastavení**. Ve výchozím stavu
(`FARM_HOTSPOT_APPLY=0`) se SSID a heslo jen uloží do databáze, takže formulář
funguje i v lokálním náhledu na PC a síť hostitele se nemění.

Na Raspberry Pi OS Bookworm (NetworkManager) lze nastavení aplikovat přes
`nmcli`. Kontejner k tomu potřebuje D-Bus hostitele a oprávnění v polkitu:

```bash
# Uživatel hostitele se stejným UID jako uživatel v kontejneru (10001).
sudo useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin printfarm
sudo cp server/deploy/50-printfarm-hotspot.rules /etc/polkit-1/rules.d/
sudo systemctl restart polkit

docker compose -f compose.yaml -f compose.hotspot.yaml up -d --build
```

Override `compose.hotspot.yaml` zapne `FARM_HOTSPOT_APPLY=1` a připojí
`/run/dbus/system_bus_socket`. Server pak spravuje připojení
`PrintFarm-Hotspot` (SSID ve výchozím stavu `PrintFarm`, režim AP, 2,4 GHz, WPA2-PSK, sdílená IPv4 – ESP32 dostanou
adresu z DHCP a server najdou na `10.42.0.1`). Rozhraní lze změnit proměnnou
`FARM_HOTSPOT_INTERFACE` (výchozí `wlan0`).

Pozor: zapnutí hotspotu na `wlan0` odpojí Raspberry Pi od klientské Wi-Fi.
Dashboard proto otevírejte přes Ethernet, nebo použijte druhý Wi-Fi adaptér
(např. `FARM_HOTSPOT_INTERFACE=wlan1`).

Při spuštění bez Dockeru stačí služba s oprávněním k NetworkManageru
(stejné polkit pravidlo pro jejího uživatele) a `FARM_HOTSPOT_APPLY=1`.

## Základní protokol

- Administrace posílá hlavičku `X-Admin-Key`.
- Každá tiskárna dostane při registraci vlastní jednorázově zobrazený klíč.
- ESP32 posílá svůj klíč v hlavičce `X-Printer-Key`.
- Stav `waiting_local_approval` označuje čekání před první vrstvou.
- Podrobný cyklus agenta je v `../PROTOKOL_ESP32.md`.

Data v `server/data` jsou provozní a nepatří do Git repozitáře.
