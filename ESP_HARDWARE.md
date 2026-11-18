# Hardware ESP uzlu tiskárny

## Vybraný modul

Referenční modul pro prototyp:

`ESP32 DevKit V1 s modulem ESP-WROOM-32`

- původní ESP32, dual-core Xtensa LX6 až 240 MHz;
- typicky 4 MB flash (ověřit na konkrétní desce);
- bez PSRAM;
- integrovaná 2,4GHz PCB anténa;
- USB-UART převodník a micro-USB podle konkrétní revize;
- ESP3D 3.x;
- samostatný microSD adaptér s logikou 3,3 V.

## Napájení

ESP uzel se nikdy nebude napájet z 3,3V výstupu SKR. Pro prototyp jej lze
napájet z regulované 5V větve SKR Mini E3 V3.0 přes 5V/VIN vstup vývojové
desky, pokud měřením ověříme dostatečnou proudovou rezervu se současně
připojeným LCD a MicroProbe. Připojení má mít vlastní pojistku nebo polyfuse a
u ESP alespoň 470uF nízkoimpedanční kondenzátor doplněný 100nF keramikou.

Pro sériovou farmu je robustnější použít samostatný 24V→5V měnič alespoň 1 A,
případně oficiálně podporovaný BTT DCDC5V modul. SKR a ESP vždy sdílejí zem.
Při servisním připojení USB se nesmí 5V větev zpětně napájet z počítače; použije
se datový USB kabel bez 5V, odpojovací jumper nebo vhodné oddělení napájení.

## UART k Marlinu

Marlin je připravený na UART2 při 115200 Bd. Pro první prototyp se na ESP
použije hardwarový UART2 s tímto předběžným mapováním:

```text
ESP GPIO17 (TX2) -> SKR RX
ESP GPIO16 (RX2) <- SKR TX
ESP GND          -- SKR GND
```

Elektrické zapojení:

```text
ESP TX  -> SKR RX
ESP RX  <- SKR TX
ESP GND -- SKR GND
```

Obě strany používají logiku 3,3 V. GPIO16/17 nekolidují s navrženou VSPI
sběrnicí SD. Zapojení se před připojením ověří podle potisku konkrétní desky.

## MicroSD

Požadavky na adaptér:

- přímá logika 3,3 V;
- bez pomalého obousměrného převodníku úrovní;
- krátké vedení SPI;
- kvalitní konektor a blokovací kondenzátory;
- detekce vložené karty je doporučená;
- karta High Endurance, 8–32 GB, FAT32.

Předběžné mapování standardní VSPI sběrnice:

```text
SD_CS   = GPIO13
SD_SCK  = GPIO18
SD_MOSI = GPIO23
SD_MISO = GPIO19
SD_CD   = GPIO34 (volitelné, pouze vstup)
UART_TX = GPIO17
UART_RX = GPIO16
```

GPIO0, GPIO2, GPIO5, GPIO12 a GPIO15 se pro prototyp nepoužijí, aby periférie
neovlivnila bootovací strapping. GPIO6 až GPIO11 patří interní flash a nesmí se
použít. Konečné piny se potvrdí podle vybraného SD adaptéru.

## Anténa

ESP-WROOM-32 používá integrovanou PCB anténu. Konec desky s anténou musí být
mimo kovový rozvaděč, nesmí být zakrytý
mědí vlastní nosné DPS a má být co nejdále od zdroje a motorových či topných
kabelů. U každé tiskárny se při instalaci změří RSSI a zaznamená do serveru.
Pokud bude signál v reálné farmě nedostatečný, produkční revize přejde na
ESP32-S3-WROOM-1U s externí anténou.

## Stav

Hardwarová platforma prototypu je vybraná. Zdroj ESP3D zatím není uložený v projektu,
protože stažení externího repozitáře nebylo při posledním pokusu povoleno.

Podrobné propojení je v `ZAPOJENI.md`.

