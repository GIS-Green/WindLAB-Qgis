# WindLab – QGIS plugin

Wind farm analyses in QGIS: annual energy production (AEP), wake, shadow flicker, noise (ISO 9613-2),
layout optimisation, cable routing and turbine transport (swept path).

This repository contains the **QGIS plugin** – the user interface, map layers and the connection
to the WindLab calculation server. All calculations and PDF reports run on the WindLab server
(`https://windlab.greengis.pl`); project data are sent only for the duration of a calculation and are
not stored on the server.

## Requirements

* QGIS 3.28 or newer, including QGIS 4 (Qt6)
* internet connection
* free WindLab activation key – register at <https://windlab.greengis.pl/register>

Demo limits: up to 4 turbines per calculation, turbine transport analysis for one site per key.

## Installation

* QGIS → *Plugins → Manage and Install Plugins → Install from ZIP* (release ZIP), or
* copy the repository folder as `WindLab` into the QGIS plugins directory
  (`…/QGIS3/profiles/default/python/plugins/WindLab`).

Then click the WindLab toolbar button and enter your activation key.

---

## WindLab – wtyczka QGIS (PL)

Analizy farm wiatrowych w QGIS: produktywność (AEP), wake, migotanie cienia, hałas (ISO 9613-2),
optymalizacja układu, trasy kablowe i transport turbin. Repozytorium zawiera wtyczkę (GUI i połączenie
z serwerem); obliczenia i raporty wykonuje serwer WindLab. Wymagany bezpłatny klucz aktywacyjny:
<https://windlab.greengis.pl/register>.

## License

GNU General Public License v2 or later – see [LICENSE](LICENSE). © GreenGIS
