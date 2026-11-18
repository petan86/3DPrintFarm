# Protokol server ↔ ESP32

Každé ESP32 používá vlastní tajný klíč v HTTP hlavičce `X-Printer-Key`.
Klíč se zobrazí jen jednou při registraci tiskárny a v databázi je uložen
pouze jeho SHA-256 otisk.

## Cyklus agenta

1. ESP32 pošle telemetrii na `POST /api/printer/telemetry`.
2. Je-li tiskárna volná, zavolá `POST /api/printer/next-job`.
3. Server atomicky rezervuje nejstarší volnou úlohu. Při prázdné frontě
   odpoví HTTP 204.
4. ESP32 stáhne soubor z vráceného `download_url` na vlastní microSD.
5. Stav úlohy posílá na `POST /api/printer/jobs/{id}/state`.

## Stavový automat

```text
queued -> assigned -> downloading -> ready -> heating -> leveling
       -> waiting_local_approval -> printing -> complete
                                      |            |
                                      v            v
                                    paused        error
```

Stav `waiting_local_approval` znamená, že tiskárna je nahřátá a vyrovnaná,
ale první vrstva ještě nezačala. Pokračování smí potvrdit obsluha fyzickým
enkodérem na LCD. Server tento krok nesmí vzdáleně obejít.

## Poznámky pro firmware

- Klíč se nesmí zapisovat do logu ani posílat přes nezabezpečenou veřejnou síť.
- Pro farmu použít oddělenou Wi-Fi/VLAN; přístup z internetu pouze přes VPN.
- Opakované volání `next-job` vrací již aktivní úlohu stejné tiskárny, takže
  restart ESP32 nevytvoří druhou rezervaci.
- Stahovaný G-code je dostupný pouze tiskárně, které server úlohu přidělil.

## Markery v počátečním G-code

FarmAgent pasivně sleduje odpovědi Marlinu. Slicer nebo server vloží do
počátečního G-code jednoznačné host-action zprávy:

```gcode
M118 A1 P0 action:notification FARM_HEATING
; příkazy pro nahřátí
M118 A1 P0 action:notification FARM_LEVELING
G28
G29
M118 A1 P0 action:notification FARM_WAITING_LOCAL_APPROVAL
M0 Potvrdte prvni vrstvu
M118 A1 P0 action:notification FARM_PRINTING
```

`M0` zastaví provádění před první tiskovou vrstvou a pokračování provede
obsluha enkodérem LCD. Zpráva `FARM_PRINTING` se odešle až po tomto potvrzení.
