# Stav firmware Marlin

## Ověřený základ

Konfigurace byla 18. listopadu 2026 testovacím způsobem zkompilována s
oficiálním Marlinem 2.1.2.5 pro prostředí `STM32G0B1RE_btt`.

Výsledek:

- kompilace: úspěšná;
- RAM: 17 484 B / 147 456 B (11,9 %);
- flash: 189 720 B / 524 288 B (36,2 %);
- výstup: `Marlin-src/.pio/build/STM32G0B1RE_btt/firmware.bin`.

Ověřené volby:

- `BOARD_BTT_SKR_MINI_E3_V3_0`;
- TMC2209 pro X, Y, Z a E0;
- UART2 při 115200 Bd pro externí ESP3D;
- USB CDC jako druhý port `-1`;
- `CR10_STOCKDISPLAY` pro LCD 12864;
- EEPROM a onboard SD;
- `M73`, host action/prompt, stavová hlášení a emergency parser.

## Důležité: zatím neflashovat

Build pouze potvrzuje vzájemnou kompatibilitu desky, displeje a zapnutých
funkcí. Konfigurace stále obsahuje obecné a nepotvrzené parametry tiskárny.
Před nahráním je nutné ověřit:

- model a mechanické rozměry tiskárny;
- kroky/mm, směry motorů a endstopů;
- proudy TMC2209;
- typ hotendového a bed termistoru;
- bezpečné maximální teploty;
- typ sondy, její offset a leveling režim;
- přesný kabel LCD;
- fyzické piny UARTu ESP32-S3;
- funkci všech topných ochran.

Marlin při buildu také upozorňuje, že pokud není použit sensorless homing,
musí být na desce odstraněné DIAG propojky. Toto se musí ověřit fyzicky.

## Custom tiskárna a sonda

Tiskárna je vlastní konstrukce a používá sondu BIGTREETECH BIQU MicroProbe.
Proto se nesmí převzít mechanický profil Ender-3, přestože řídicí deska je
řady E3.

Před nastavením sondy je nutné potvrdit, zda jde o MicroProbe V1 nebo V2:

- V1 spíná při aktivaci logickou úroveň HIGH;
- V2 spíná při aktivaci logickou úroveň LOW.

Záměna této volby je bezpečnostní riziko při Z homingu. Marlin 2.1.2.5 navíc
ještě nemá pojmenované volby `BIQU_MICROPROBE_V1` a
`BIQU_MICROPROBE_V2`. Pro produkční firmware je proto vhodné přejít na aktuální
stabilní řadu Marlinu s přímou podporou MicroProbe a znovu provést celý build a
test sondy. Do potvrzení verze sondy zůstává probing vypnutý.

## Zjištění k LCD

`REPRAP_DISCOUNT_FULL_GRAPHIC_SMART_CONTROLLER` se s pinovou definicí SKR Mini
E3 V3.0 v Marlinu 2.1.2.5 nezkompiluje. Marlin pro běžný kompatibilní LCD 12864
na této desce vyžaduje `CR10_STOCKDISPLAY`. Displej se dvěma samostatnými
porty EXP1/EXP2 může vyžadovat kabelovou redukci na jediný desetipinový EXP
konektor desky.

## Samostatný soubor parametrů

Parametry custom tiskárny jsou soustředěné v `FarmMachineConfig.h`. Tento
soubor je zahrnutý z `Configuration.h` a obsahuje tiskový prostor, pohybové
limity, směry os, termistory, teplotní limity, verzi sondy a její offset.
Dokud je `FARM_MACHINE_CONFIG_VERIFIED` nastaveno na `0`, build vypisuje
bezpečnostní varování. Po zavedení souboru proběhl kontrolní build úspěšně.

