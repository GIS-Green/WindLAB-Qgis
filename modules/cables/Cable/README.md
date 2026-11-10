# Kable SN – MST i optymalizacja układu (moduł obok AEP)

## Instalacja
1. Skopiuj folder `Cable` obok folderu `AEP` we wtyczce (ta sama struktura: `gui/`, `workers/`).
2. Okno uruchamiasz tak samo jak AEP: `from Cable.gui.cable_dialog import main; main()`
   (dopisz akcję w menu wtyczki analogicznie do AEP).
3. Obliczenia idą do zewnętrznego Pythona (jak w AEP: `AEP_PYTHON` albo `CABLE_PYTHON`).
   Wymagane moduły: `numpy`, `scipy` (>= 1.9), `shapely` (>= 2.0), `matplotlib`, `reportlab`, `openpyxl`.
   Przycisk „Sprawdź środowisko" pokazuje, czego brakuje, i podaje gotowe polecenie `pip install`.
   Nowy względem AEP jest zwykle tylko `shapely` (i ewentualnie `openpyxl`).

## Przebieg pracy
1. **Zakładka 1 – Układ i MST.** Warstwa turbin (atrybut USERLABEL), warstwa GPO (punkty; atrybut z nazwą i opcjonalnie
   z liczbą pól SN), opcjonalnie warstwa poligonów no-go. MST omija strefy po wierzchołkach, przy kilku GPO powstaje las
   rozpinający. Zapisuje `cable_graph.json` (wymagany przez zakładkę 2).
2. **Zakładka 2 – Optymalizacja.** Moc i straty z `aep_results.json` + `aep_results.npz` (macierz G) albo ręcznie
   (moc znamionowa + LLF). Biblioteka kabli edytowalna, ceny domyślne ze strony windfarmoptimizer.com.
3. **Raport PDF** (przycisk na dole): mapy poglądowe, wykresy, BoQ, tabele odcinków.

Wyniki trafiają do katalogu wyjściowego (domyślnie ten sam co AEP).

## Pliki wynikowe
`cable_graph.json`, `cable_mst_results.json`, `cable_mst_edges.geojson`, `cable_terminals.geojson`,
`cable_opt_results.json`, `cable_opt_edges.geojson`, `cable_opt_segments.csv`, `cable_opt_bom.xlsx`,
`cable_scheme.svg`, `Cable_<projekt>_<data>.pdf`. Wszystkie współrzędne w EPSG:2180.

## Założenia i ograniczenia (wersja 0.1)
- Jeden model i jeden tryb pracy turbiny na farmę (jak w AEP); wszystkie turbiny mają ten sam prąd.
- cosφ to parametr użytkownika (domyślnie 0,95), nie wartość z karty turbiny.
- Koszt = wyłącznie zakup kabli (3 żyły × cena żyły × długość). Bez wykopów, montażu, mufowania i pól SN.
  Kara za rozgałęzienie, cena energii (kapitalizacja strat) i rezerwa długości wchodzą tylko, gdy je wpiszesz.
- Dopuszczalne prądy kabli (I_max) są przepisane ze strony i NIE były weryfikowane z kartą katalogową.
- Spadek napięcia jest sprawdzany po wyborze topologii; przekroczenie jest naprawiane podnoszeniem przekrojów
  na najgorszej ścieżce (heurystyka). Gdy się nie da, wynik zawiera jawne ostrzeżenie.
- Dla farm powyżej 20 turbin graf kandydatów to Delaunay + najbliżsi sąsiedzi, więc optimum dotyczy tego grafu.
- Trasy mogą biec po granicy (buforu) strefy no-go; ustaw bufor, jeśli kabel ma być od niej odsunięty.
- Nie sprawdzono zwarciowej wytrzymałości przekrojów, prądu ładowania pojemnościowego ani redundancji (N-1).
