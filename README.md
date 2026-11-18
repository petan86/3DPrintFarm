# 3DPrintFarm

Open-source návrh farmy až pro 40 3D tiskáren řízených Marlinem.

Plánovaná architektura:

- centrální server na Raspberry Pi,
- BTT SKR Mini E3 V3.0 s firmwarem Marlin,
- ESP32 DevKit V1 jako Wi-Fi/UART brána s vlastní microSD,
- registrace tiskáren pomocí API klíčů,
- webové nahrávání G-code metodou drag & drop a tisková fronta,
- sledování průběhu, teplot a provozního stavu,
- lokální potvrzení obsluhou před zahájením první vrstvy.

Projekt je ve fázi prototypu. Firmware zatím neflashujte do tiskárny bez
kontroly rozměrů, pohybových parametrů, zapojení a revize BIQU MicroProbe.

## Dokumentace

- [Návrh farmy](NAVRH_FARMY.md)
- [Zapojení prototypu](ZAPOJENI.md)
- [ESP32 hardware](ESP_HARDWARE.md)
- [ESP3D prototyp](ESP3D_PROTOTYP.md)
- [Stav firmwaru](STAV_FIRMWARE.md)
- [Protokol server ↔ ESP32](PROTOKOL_ESP32.md)
- [FarmAgent v ESP3D](FARM_AGENT.md)

## Licence třetích stran

Adresáře `Marlin-src`, `Marlin_2125` a `ESP3D-farm` obsahují nebo vycházejí
z upstream open-source projektů. Jejich původní licenční soubory a hlavičky
zůstávají rozhodující pro příslušný kód.
