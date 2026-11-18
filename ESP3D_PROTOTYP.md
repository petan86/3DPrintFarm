# ESP3D prototyp – ESP32 DevKit V1

## Stav

Firmware ESP3D 3.1.0 je nakonfigurovaný a úspěšně sestavený pro desku
`ESP32 Dev Module` / ESP-WROOM-32.

- Marlin komunikace: UART2, 115200 Bd
- ESP RX: GPIO16 (připojit na TX desky SKR)
- ESP TX: GPIO17 (připojit na RX desky SKR)
- samostatná microSD přes SPI: CS 13, SCK 18, MISO 19, MOSI 23
- cílový firmware tiskárny: Marlin
- autentizace ESP3D: zapnutá
- Wi-Fi údaje: záměrně nejsou zakompilované; nastaví se při prvním spuštění

Výsledky sestavení:

- RAM: 57 740 B z 327 680 B (17,6 %)
- flash: 1 308 725 B z 1 966 080 B (66,6 %)
- aplikace: `ESP3D-farm/.pioenvs/esp32dev/firmware.bin`
- bootloader: `ESP3D-farm/.pioenvs/esp32dev/bootloader.bin`
- oddíly: `ESP3D-farm/.pioenvs/esp32dev/partitions.bin`

## První oživení

1. ESP32 nejprve nepřipojovat k SKR ani k SD modulu.
2. Připojit ESP32 pouze USB k počítači.
3. Z adresáře `ESP3D-farm` spustit:

   ```powershell
   ..\.venv\Scripts\platformio.exe run -e esp32dev -t upload
   ```

4. Po restartu nastavit Wi-Fi a změnit výchozí přihlašovací údaje ESP3D.
5. Odpojit USB napájení a až poté zapojit společnou zem a překřížený UART.
6. SD modul přidat až po ověření komunikace ESP32 ↔ Marlin.

Při současném připojení USB a 5V ze SKR je nutné zabránit zpětnému napájení.
Pro první zkoušky je bezpečnější napájet ESP32 jen přes USB.

## Poznámka ke zdrojům ESP3D

Pro kombinaci ESP32 + SPI SD byly v ESP3D 3.1.0 opraveny dvě chyby
překladu: chybná cesta k `esp_sd.h` a SDIO blok, který se překládal i pro
SPI kartu. Opravy jsou lokálně ve zdrojích a výsledný firmware jimi prošel.

Tento obraz zatím není určen k nahrání do produkční tiskárny. Nejdříve je
nutné ověřit přesnou revizi SKR Mini E3 V3.0, piny TFT konektoru, polaritu
napájení a skutečnou revizi BIQU MicroProbe.
