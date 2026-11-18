# FarmAgent v ESP3D

FarmAgent je rozšíření ESP3D pro komunikaci s centrálním serverem farmy.

Aktuální firmware pasivně sleduje odpovědi Marlinu na UART2 a rozpoznává:

- aktuální a cílovou teplotu hotendu a podložky z reportu `M105`;
- procenta z reportu `M73 Progress`;
- standardní host-action `pause`, `resume`, `cancel` a `kill`;
- farmové markery `FARM_HEATING`, `FARM_LEVELING`,
  `FARM_WAITING_LOCAL_APPROVAL` a `FARM_PRINTING`.

Stav se posílá serveru při změně a během aktivního tisku nejvýše jednou za
pět sekund. UART data přitom dál procházejí původním zpracováním ESP3D.

Automatické převzetí a spuštění farmové úlohy zůstává vypnuté, dokud nebude
ověřen UART a microSD na fyzickém prototypu.

Poslední ověřený build pro ESP32 DevKit V1 / ESP-WROOM-32:

- RAM: 58 456 B z 327 680 B (17,8 %);
- flash: 1 417 289 B z 1 966 080 B (72,1 %).
