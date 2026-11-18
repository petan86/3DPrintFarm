# Návrh 3D tiskové farmy

## 1. Cíl

Systém obslouží až 40 tiskáren z jednoho webového rozhraní na Raspberry Pi.
Každá tiskárna používá Marlin na BTT SKR Mini E3 V3.0 a samostatný modul
ESP32-S3 se slotem pro SD kartu. ESP32 komunikuje s Marlinem přes UART a se
serverem přes Wi-Fi.

Hlavní vlastnosti:

- registrace tiskárny pomocí jednorázového API klíče;
- drag & drop souborů `.gcode` do webu;
- centrální i samostatná fronta pro každou tiskárnu;
- automatické přidělení úlohy podle stavu, materiálu a schopností tiskárny;
- stav Online / Offline / Čeká / Nahrává / Nahřívá / Tiskne / Pozastaveno /
  Kontrola první vrstvy / Dokončeno / Chyba;
- průběh, zbývající čas, teplota hotendu a podložky;
- lokální pokračování tisku při výpadku Wi-Fi nebo serveru;
- název zařízení a informace o vloženém materiálu.

## 2. Doporučená architektura

```text
Webový prohlížeč
       |
   HTTPS / WebSocket
       |
Raspberry Pi
  - web + REST API
  - plánovač úloh
  - PostgreSQL
  - MQTT broker
  - úložiště G-code
       |
  Wi-Fi (MQTT + HTTPS)
       |
ESP32-S3 + SD  [1 kus na tiskárnu]
  - agent farmy
  - lokální fronta a G-code
  - UART host pro Marlin
       |
 UART 3,3 V, TX/RX/GND
       |
BTT SKR Mini E3 V3.0 + Marlin
```

Raspberry Pi nesmí posílat jednotlivé G-code řádky přes Wi-Fi během tisku.
Nejdříve odešle celý soubor na SD kartu ESP32, ESP ověří velikost a SHA-256 a
pak jej lokálně streamuje do Marlinu. Wi-Fi tak není součástí časově kritické
cesty. ESP ukládá pozici potvrzeného řádku a pravidelně publikuje telemetrii.

Alternativa je nahrát soubor přes `M28` na SD kartu SKR a spustit jej pomocí
`M23` + `M24`. Je jednodušší z pohledu Marlinu, ale sériový upload velkých
souborů je pomalý a práce se soubory přes textový protokol je méně robustní.
Pro první verzi doporučuji lokální streaming z ESP SD; druhou variantu lze
později přidat jako volitelný režim.

## 3. Komunikace ESP32 ↔ server

### Registrace

1. V administraci vznikne tiskárna a jednorázový párovací klíč s krátkou
   platností.
2. ESP po prvním spuštění otevře konfigurační Wi-Fi portál.
3. Uživatel zadá URL serveru, Wi-Fi a párovací klíč.
4. Server vrátí `printer_id` a dlouhodobý přístupový token.
5. ESP uloží tajné údaje do NVS. Párovací klíč už nelze znovu použít.

Produkční zařízení má používat unikátní token, ne jeden společný API klíč pro
všech 40 tiskáren. Token lze na serveru jednotlivě zneplatnit.

### MQTT témata

```text
farm/printers/{id}/state       ESP -> server, retained
farm/printers/{id}/telemetry   ESP -> server
farm/printers/{id}/events      ESP -> server
farm/printers/{id}/command     server -> ESP
farm/printers/{id}/command-ack ESP -> server
```

Velké `.gcode` soubory se nepřenášejí přes MQTT. ESP dostane krátkodobou HTTPS
URL, stáhne soubor po blocích na SD a ověří SHA-256. Každý příkaz obsahuje
unikátní `command_id`; opakované doručení nesmí spustit tisk podruhé.

### Telemetrie

Při tisku každé 2 sekundy, v klidu každých 10 sekund:

```json
{
  "printer_id": "prn_01J...",
  "seq": 1842,
  "state": "PRINTING",
  "job_id": "job_01J...",
  "progress": 42.6,
  "elapsed_s": 2810,
  "remaining_s": 3785,
  "hotend": { "actual": 211.3, "target": 215.0 },
  "bed": { "actual": 59.8, "target": 60.0 },
  "wifi_rssi": -57,
  "sd_free_bytes": 6941573120,
  "marlin_ok": true
}
```

Teploty se získají z `M105`, identita a schopnosti z `M115`. ESP musí
respektovat odpovědi `ok`, `busy`, `Resend:` a chybové hlášky Marlinu.

## 4. Stavový automat úlohy

```text
QUEUED -> ASSIGNED -> DOWNLOADING -> READY -> PREHEATING -> HOMING
       -> LEVELING -> WAITING_LOCAL_APPROVAL -> PRINTING_FIRST_LAYER
       -> PRINTING -> COMPLETED
                              |-> PAUSED
                              |-> FAILED
                              |-> CANCELLED
```

Server je autoritou pro frontu, ESP je autoritou pro právě probíhající tisk.
Po obnovení spojení server stav nesmí slepě přepsat; nejprve provede
rekonciliaci podle `job_id`, pořadového čísla události a stavu Marlinu.

Procenta při streamování vypočítá ESP jako podíl potvrzených bajtů G-code.
Je to stabilnější než počet řádků. `M73` lze současně posílat do Marlinu, aby
se průběh zobrazil i na displeji tiskárny.

### Povinné potvrzení před první vrstvou

Každá úloha má před běžným tiskem tento řízený postup:

1. předehřát podložku a hotend na hodnoty z G-code;
2. provést homing (`G28`);
3. vytvořit nebo načíst mesh podle zvoleného leveling systému;
4. dokončit všechny naplánované pohyby (`M400`) a zaparkovat hlavu do bezpečné
   kontrolní polohy mimo tiskovou plochu;
5. zobrazit na displeji „Potvrďte zahájení tisku“ a čekat na fyzické potvrzení
   uživatele;
6. po potvrzení provést případnou čisticí linku a teprve potom vytisknout první
   vrstvu.

Výchozí politika nepovolí potvrzení z webu. Obsluha musí být skutečně u
tiskárny a zkontrolovat připravenost stroje, čistotu podložky, správně
zavedený materiál a výsledek levelingu. Web pouze zobrazí
`WAITING_LOCAL_APPROVAL`, číslo tiskárny a dobu čekání. Zrušení úlohy z webu
zůstane možné.

Čekání nebude neomezené: server upozorní obsluhu a po konfigurovatelném limitu
(například 10 minut) úlohu bezpečně ukončí a vypne topení. Tím se omezí
vytékání materiálu z trysky i dlouhé ponechání stroje bez dozoru.

Kvůli vytékání filamentu je vhodné před potvrzením držet podložku na cílové
teplotě, ale hotend jen na nastavitelné pohotovostní teplotě (například
150–170 °C podle materiálu). Po lokálním potvrzení se hotend dohřeje na cílovou
tiskovou teplotu, provede se čisticí linka a začne první vrstva. Pokud proces
vyžaduje leveling s plně nahřátou tryskou, profil může tuto optimalizaci
vypnout.

Server při přijetí G-code vloží jednoznačný řídicí marker mezi dokončení
startovací sekvence (nahřátí, homing a leveling) a začátek čisticí linky nebo
první vrstvy. Vhodné místo se určí podle řízené startovací šablony farmy, ne
odhadem z výšky Z. Upravený pracovní soubor dostane nový SHA-256; originál
zůstane beze změny uložený pro audit.

Nejjednodušší prototyp markeru pro displej s enkodérem je blok ve stylu:

```gcode
;FARMA_BEFORE_FIRST_LAYER
M400
M117 Potvrdte zahajeni tisku
M0 Potvrdte zahajeni tisku
```

Produkční varianta doplní bezpečné zaparkování a přesun na začátek čisticí
linky podle konkrétního modelu tiskárny. Nelze použít jednu pevnou souřadnici
pro všechna zařízení.
Marlin `M0` čeká na lokální stisk ovladače; ESP během čekání neposílá další
G-code, udržuje telemetrii a stav serveru nastaví na
`WAITING_LOCAL_APPROVAL`. Po potvrzení a odpovědi `ok` streamer pokračuje.

Pro pohodlnější integraci se zapnou `HOST_ACTION_COMMANDS`,
`HOST_PROMPT_SUPPORT` a `HOST_STATUS_NOTIFICATIONS`, ale lokální displej a jeho
potvrzení zůstávají autoritou. Musí se ověřit, že vybraný typ displeje podporuje
interaktivní `M0` prompt.

Automatický leveling vyžaduje sondu (například BLTouch/CR Touch). Přesná
sekvence závisí na režimu Marlinu:

- běžný bilineární leveling: po `G28` spustit `G29`;
- uložená mesh: po `G28` ji znovu aktivovat pomocí `M420 S1`;
- UBL: použít profilovou sekvenci UBL pro danou tiskárnu.

Zda se má mesh měřit před každým tiskem, nebo jen načíst uložená a občas znovu
změřit, bude nastavitelné v profilu tiskárny.

## 5. Fronta a výběr tiskárny

Každá úloha obsahuje:

- požadovaný materiál a barvu;
- minimální tiskový prostor;
- profil trysky;
- prioritu;
- volitelně konkrétní tiskárnu;
- checksum a metadata získaná analýzou G-code.

Plánovač přidělí pouze tiskárnu ve stavu `IDLE`, která odpovídá materiálu,
trysce a rozměrům. První verze může používat FIFO s prioritou; později lze
přidat optimalizaci podle odhadované délky tisku.

Před zařazením server G-code pouze analyzuje, neupravuje jej. Kontroluje
zejména typ souboru, velikost, požadované teploty, rozměry pohybu a zakázané
příkazy. Originál a výsledek analýzy uchová odděleně.

## 6. Webové rozhraní

Dashboard má mřížku až 40 karet. Každá karta ukáže název, barevný stav,
aktuální úlohu, průběh, teploty, materiál, dobu tisku a poslední kontakt.

Horní drop zóna přijímá jeden nebo více `.gcode` souborů. Po nahrání se otevře
krátký formulář: počet kopií, materiál, barva, priorita a volitelná tiskárna.
Po potvrzení vzniknou úlohy ve frontě. Aktualizace dashboardu probíhá přes
WebSocket nebo Server-Sent Events bez obnovování stránky.

## 7. Datový model serveru

Minimální tabulky:

- `printers`: identita, název, stav, schopnosti, firmware, poslední kontakt;
- `printer_tokens`: hash tokenu, platnost, revokace;
- `materials`: typ, výrobce, barva, zbývající hmotnost;
- `printer_material`: právě vložená cívka a čas změny;
- `gcode_files`: cesta, SHA-256, velikost, metadata a vlastník;
- `jobs`: stav, priorita, tiskárna, průběh, čas a chyba;
- `job_events`: neměnný auditní záznam změn;
- `telemetry`: časová data s omezenou retenční dobou.

Pro 40 tiskáren stačí jeden Raspberry Pi 5 s PostgreSQL, MQTT brokerem a
aplikací v kontejnerech. G-code se ukládá na SSD, ne na systémovou microSD.

## 8. Marlin – nutné změny konfigurace

Dodané soubory jsou výchozí konfigurace Marlinu 2.1.2.5, nikoli konfigurace
pro uvedenou BTT desku. Aktuálně obsahují mimo jiné:

```cpp
#define MOTHERBOARD BOARD_RAMPS_14_EFB
#define X_DRIVER_TYPE A4988
//#define EEPROM_SETTINGS
//#define SDSUPPORT
//#define SERIAL_PORT_2 -1
```

Pro SKR Mini E3 V3.0 bude základem přibližně:

```cpp
#define MOTHERBOARD BOARD_BTT_SKR_MINI_E3_V3_0
#define X_DRIVER_TYPE  TMC2209
#define Y_DRIVER_TYPE  TMC2209
#define Z_DRIVER_TYPE  TMC2209
#define E0_DRIVER_TYPE TMC2209
#define EEPROM_SETTINGS
#define EEPROM_AUTO_INIT
#define SDSUPPORT
```

Konkrétní `SERIAL_PORT`, `SERIAL_PORT_2` nebo `SERIAL_PORT_3` nelze bezpečně
zvolit bez potvrzení displeje a konektoru, do kterého bude ESP zapojeno.
TMC2209 už používají interní hardwarový UART desky; ten se nesmí zaměnit za
hostitelský UART pro ESP.

Pro host komunikaci se hodí zapnout také:

```cpp
#define EMERGENCY_PARSER
#define ADVANCED_OK
#define HOST_ACTION_COMMANDS
#define HOST_PROMPT_SUPPORT
#define SET_PROGRESS_MANUALLY
#define SET_PROGRESS_PERCENT
#define M73_REPORT
```

Před kompilací se musí doplnit skutečný model tiskárny: rozměry, termistory,
kroky, směry motorů, endstopy, sonda, maximální teploty a typ displeje. Tyto
hodnoty nelze převzít z obecného souboru bez ověření na stroji.

## 9. Položka „Farma“ v menu Marlinu

Použitý displej je grafický LCD 12864 s otočným enkodérem. Pro BTT SKR Mini E3
V3.0 je nakonfigurovaný jako `CR10_STOCKDISPLAY`, protože pinová definice této
desky v Marlinu 2.1.2.5 nepodporuje obecnou volbu
`REPRAP_DISCOUNT_FULL_GRAPHIC_SMART_CONTROLLER`. Tento typ umožňuje zobrazit
lokální výzvu `M0` a pokračovat stiskem enkodéru, takže splňuje požadavek na
fyzické potvrzení před první vrstvou. Požaduje displej nebo kabelovou redukci
kompatibilní s jediným desetipinovým EXP konektorem SKR Mini E3 V3.0.

Pouhá změna `Configuration.h` a `Configuration_adv.h` nestačí pro dynamické
menu s názvem zařízení a materiálem. Jsou potřeba zdrojové soubory celé verze
Marlinu a malý vlastní modul.

Doporučené rozdělení odpovědnosti:

- název tiskárny: uložený v EEPROM Marlinu a synchronizovaný z ESP;
- vložený materiál: primárně server, lokální kopie v ESP;
- menu `Farma`: jen zobrazení názvu, materiálu, připojení a IP; případně akce
  „Tiskárna připravena“ a „Materiál vyměněn“;
- změny inventáře a plánování zůstávají na serveru, aby nevznikly dva různé
  zdroje pravdy.

Pro komunikaci lze do Marlinu přidat vlastní příkazy, například `M790` pro
čtení stavu farmy a `M791` pro nastavení krátkých hodnot. Vlastní příkazy musí
mít přesně definovanou maximální délku, kontrolu vstupu a EEPROM verzi.

## 10. Firmware ESP32-S3

Referenční hardware prvního prototypu je `ESP32 DevKit V1` s modulem
`ESP-WROOM-32` a samostatným kvalitním microSD adaptérem pro logiku 3,3 V.
Použije UART2 na GPIO16/17 a VSPI pro SD na GPIO13/18/19/23. Prototyp lze po
ověření proudové rezervy napájet z 5V větve SKR; produkční varianta preferuje
samostatný 24V→5V měnič. ESP a SKR sdílejí zem a 3,3V UART signály. Deska musí
být umístěná tak, aby její PCB anténa nebyla uvnitř kovového rozvaděče.

### Základ: ESP3D 3.x

Firmware ESP modulu je vhodné postavit jako udržovaný fork projektu ESP3D,
nikoli začínat od prázdného projektu. ESP3D 3.x již podporuje ESP32-S3,
Marlin, RAW UART, Wi-Fi provisioning, nativní SD (SPI/SDIO), WebSocket,
WebDAV, správu souborů a OTA aktualizace.

Přímo lze využít zejména:

- hlavní sériový klient pro spojení s Marlinem;
- webové nastavení Wi-Fi a diagnostický terminál;
- nativní SD a operace `ESP740` / `ESP750`;
- binární přenos souborů přes `/wsdata` s protokolem `esp3d-v1`;
- lokální stream souboru ze SD pomocí `ESP700` a jeho řízení přes `ESP701`;
- parser odpovědí a směrování zpráv mezi síťovými klienty a UARTem;
- aktualizaci firmware a záchranné konfigurační rozhraní.

Do forku je potřeba doplnit samostatný modul `FarmAgent`:

- odchozí spojení na centrální server (ideálně MQTT přes TLS);
- párování jednorázovým klíčem a unikátní identitu zařízení;
- stahování přidělené úlohy, SHA-256 a atomické uložení na SD;
- stavový automat úlohy a trvalý journal po restartu;
- sběr `M105`, `M115`, průběhu a chyb Marlinu;
- idempotentní příkazy start / pauza / pokračovat / zrušit;
- synchronizaci názvu tiskárny a vloženého materiálu;
- digitálně podepsané OTA aktualizace pro celou farmu.

Vestavěná autentizace ESP3D používá uživatele/heslo a podle dokumentace není
určená jako plnohodnotné zabezpečení pro veřejnou síť. Centrální systém proto
nesmí vystavit webové, Telnet ani WebDAV porty jednotlivých ESP do internetu.
Zařízení mají vytvářet pouze odchozí šifrované spojení k Raspberry Pi; lokální
ESP3D WebUI zůstane servisním rozhraním ve vyhrazené VLAN.

ESP3D už nepodporuje pomalý upload na SD Marlinu pomocí `M28`/`M29`. To
potvrzuje volbu ukládat G-code na SD připojenou k ESP a odtud jej lokálně
streamovat do UARTu.

Moduly:

- Wi-Fi provisioning a bezpečné uložení přihlašovacích údajů;
- HTTPS klient pro stahování souborů;
- MQTT klient s automatickým obnovením spojení;
- SD úložiště s atomickým přejmenováním až po ověření souboru;
- Marlin UART parser a fronta příkazů;
- streamer G-code s podporou `ok`, `Resend:` a timeoutů;
- stavový automat tisku;
- watchdog a trvalý journal poslední úlohy;
- OTA aktualizace podepsaného firmware.

ESP a SKR mají společnou zem a komunikují logikou 3,3 V. Napájení ESP je
vhodné řešit samostatným kvalitním měničem s rezervou; neodebírat špičkový
proud Wi-Fi bez ověření z libovolného 3,3V pinu desky. TX se zapojuje na RX a
RX na TX.

## 11. Doporučený postup implementace

1. Potvrdit přesný hardware tiskárny, revizi desky, displej a volný UART.
2. Sestavit čistý Marlin 2.1.2.5 pro jedinou tiskárnu a ověřit bezpečnostní
   funkce, topení, pohyb, SD a UART.
3. Vytvořit ESP prototyp: `M115`, `M105`, fronta příkazů a lokální tisk krátkého
   testovacího G-code.
4. Implementovat registraci, MQTT a stahování souboru s checksumem.
5. Vytvořit serverové MVP pro jednu tiskárnu, následně simulovat 40 zařízení.
6. Přidat plánovač, materiály, audit a obnovu po výpadku.
7. Až potom doplnit vlastní menu `Farma` do Marlinu.

## 12. Údaje nutné před úpravou firmware

- Je deska opravdu **BTT SKR Mini E3 V3.0**, nebo jiná revize?
- Tiskárna je custom; je nutné dodat její tiskový prostor a mechanické limity.
- Typ displeje a konektor, kterým je připojen.
- Sonda podložky je BIGTREETECH BIQU MicroProbe; je nutné potvrdit V1 nebo V2,
  zapojení a změřit offset vůči trysce.
- Přesný typ ESP32-S3 modulu se SD a jeho schéma/pinout.
- Který fyzický konektor SKR má být použit pro UART ESP.
- Zda musí tiskárna umět tisknout i přímo ze své onboard SD bez ESP.

