# -*- coding: utf-8 -*-
"""
Wersja demo WindLab (obliczenia na serwerze).

Ograniczenia – serwer i tak je egzekwuje (limit turbin), GUI tylko je pokazuje:
  - maks. MAX_TURBINES turbin w obliczeniach,
  - brak pobierania arkuszy NMT – wysokości wyłącznie z usługi GUGiK online,
  - transport bez analizy terenu (NMT),
  - CORINE na serwerze.
"""
DEMO = True
MAX_TURBINES = 4
# adres serwera obliczeń – wpisywany na stałe przy wdrożeniu (np. "https://api.twojadomena.pl");
# pusty = etap testów: adres podaje się w oknie „Aktywacja WindLab”
DEFAULT_SERVER_URL = "https://windlab.greengis.pl"

NOTE = "niedostępne w wersji demo"
