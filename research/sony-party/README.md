# Sony PARTY — analiza wstępna, 21.10.2026

Cel: zbadanie możliwości implementacji odbiornika PARTY dla Raspberry Pi.
Nie wyodrębniono implementacji ani nie potwierdzono odbioru audio.
Nie instalowano firmware i nie zmieniano sesji PARTY.

## Dostarczone pliki

Źródło lokalne: `/mnt/dysk4TB/UPDATA_11X014` (udział SMB Dysk4TB).

| Plik | Bajty | SHA-256 |
|---|---:|---|
| BD11-VUD.BIN | 50421760 | 30a55879ca71f61e8ccd717adb570e01abed86edc2644d246259f1e905bea1cd |
| SONY_VUP.ID | 80 | 13c052711f1f651836994bd8bb036c010b3393d8d2aaedb0c1c91abc89b69738 |

`SONY_VUP.ID` po XOR każdego bajtu z `0x2e` zawiera pola:
`bdp4g`, `11.2.014`, `1f021119.b.e.0.9`.

BIN zaczyna się od `scfsscfs`. Pod offsetem `0xbc` jest znacznik
`scfs_compression_zlib`. Pod `0xd4` jest słowo `6b8b4567`; jego znaczenie
nie zostało ustalone. Kolejne dane nie mają standardowego nagłówka zlib.
Próbki 64 KiB mają entropię około 7,993–7,997 bitów/bajt. Wynik jest zgodny
zarówno z kompresją, jak i szyfrowaniem; nie dowodzi użycia konkretnego szyfru.
Nie znaleziono nagłówka ELF ani sygnatur SquashFS. Krótkie wystąpienia liter
RSA w danych nie dowodzą obecności algorytmu. Ograniczona próba dekompresji
kandydatów nagłówków zlib z pierwszych 64 KiB nie dała poprawnego strumienia.
Brak rozpakowanego kodu uniemożliwia ocenę obecności PARTY w tym pakiecie.

Według strony Sony pakiet dotyczy BDP-BX2, BDP-S1000ES, BDP-S360, BDP-S560,
BDP-S760 i BDP-S765. CMT-MX700Ni nie jest na liście:
https://www.sony.com/electronics/support/downloads/W0007713

RustCrypto/RSA jest implementacją kryptografii RSA, a nie narzędziem
rozpakowującym SCFS. Nie potwierdzono potrzeby jego użycia:
https://github.com/RustCrypto/RSA

## Interfejs żywego urządzenia

Odczytano publiczny opis usługi z CMT-MX700Ni/MX750Ni pod 192.168.8.102.
`PartySCPD.xml` zawiera kopię uzyskaną z URL wskazanego w description.xml.

Usługa: `urn:schemas-sony-com:service:Party:1`.
Odpowiedź na `X_GetDeviceInfo`: `SingerCapability=10`, `TransportPort=3975`.
Nie ustalono na tej podstawie, czy transport jest TCP/UDP ani jak ramkowane
są dane, przesyłany dźwięk i znaczniki czasu. Nie należy zgadywać formatu.

Akcje: `X_GetDeviceInfo`, `X_GetState`, `X_Start`, `X_Entry`, `X_Leave`,
`X_Abort`, `X_Invite`, `X_Exit`. Odczytany stan: IDLE, brak uczestników.
Komendy sterujące SOAP nie implementują same w sobie odbiornika audio.

## Następny krok

Potrzebna jest specyfikacja transportu PARTY, analiza działającej implementacji
odbiornika albo zapis komunikacji hosta z prawdziwym uczestnikiem PARTY.
Równolegle można badać rozpakowywanie SCFS. Konieczne jest także sprawdzenie,
czy wybrany firmware zawiera PARTY; sama zgodność producenta nie wystarcza.
Docelowy odbiornik na Pi wymaga obsługi sesji, strumienia audio i zegara
synchronizacji. Przeniesienie binarnej funkcji między platformami nie zostało
wykazane jako możliwe.

## Próba rozpakowania z RustCrypto/RSA

Wykonano dalszą analizę tego samego obrazu, bez modyfikacji oryginałów.
Wyniki maszynowe znajdują się w `results/`.

- Pobrano oficjalne źródła SCFS dla **dokładnej rodziny BDP-S360**:
  https://oss.sony.net/Products/Linux/Video/BDP-S360.html
  Archiwum: `scfs-4_6_0_RC-gpl-src.tar.gz`, SHA-256
  `8f0bab2e4ab92a72645c9fe4bb97bf4e1b26f153cf3926a3ec1bdfc3473d03cb`.
  Udostępnione źródła zawierają wtyczkę LZO i nagłówki interfejsów;
  nie zawierają pełnego dekodera kontenera ani implementacji potrzebnej
  do odczytania tego ładunku.
- Wydzielono nagłówek `[0,0xbc)`, opis kompresji `[0xbc,0xd4)`
  i nieczytelny ładunek `[0xd4,50421760)` do
  `.local-backups/sony-fw-analysis/`. To **carving kontenera, nie pełne
  rozpakowanie ani odszyfrowanie**.
- Zidentyfikowano i sprawdzono 50 673 kandydatów nagłówka zlib w całym
  obrazie (do 64 KiB wejścia i 256 KiB wyjścia na kandydata). Nie znaleziono
  kompletnego strumienia z poprawnym końcem i sumą kontrolną.
- Dodatkowo 11 prób nagłówków z jednobajtowym XOR i 144 warianty prostej
  transformacji glibc rand() nie dały poprawnego strumienia. Hipotezę PRNG
  uzasadnia wystąpienie `0x6b8b4567` pod `0xd4`, ale nie dowodzi ono jej
  poprawności. Próby nie wyczerpują możliwych algorytmów obfuskacji.
- Zbudowano i **uruchomiono RustCrypto/rsa 0.9.8**. Skaner poprawnie wykrył
  kontrolny publiczny klucz RSA 1024-bit. Następnie na oryginalnym firmware
  sprawdził 50 063 kandydatów DER (64–16 384 bajtów), próbując importu
  PKCS#1 public/private oraz SPKI/PKCS#8. Nie znaleziono poprawnego klucza.
  Nie znaleziono też jawnych znaczników PEM ani OID rsaEncryption.
  Nie wykonano deszyfrowania: brak dopasowanego klucza i rozpoznanego schematu.
  Wynik nie wyklucza kluczy zapisanych surowo, zaszyfrowanych lub poza obrazem.
- Przeszukano wskazane komendy PARTY w ASCII oraz UTF-16LE/BE. Brak trafień
  w nieodkodowanym obrazie **nie oznacza**, że funkcji nie ma w firmware.

Oficjalny nagłówek SCFS definiuje układ identyfikatora wtyczki jako
2 bity funkcji, 14 major, 8 interfejsu i 8 minor. Wartość `0x80040100` pod
`0x24`, interpretowana w ten sposób, daje funkcję 2 (ENC), major 4,
interfejs 1, minor 0. To wskazówka do dalszego badania: znaczenie pola w tym
nagłówku nie jest potwierdzone i nie zidentyfikowano algorytmu numer 4.
Nie wolno utożsamiać tego numeru z RSA bez dodatkowych dowodów.

### Odtworzenie analizy

```sh
python3 scripts/inspect_sony_scfs.py \
  /mnt/dysk4TB/UPDATA_11X014/BD11-VUD.BIN \
  --output .local-backups/sony-fw-analysis
cargo run --release --manifest-path research/sony-party/rsa-probe/Cargo.toml \
  -- /mnt/dysk4TB/UPDATA_11X014/BD11-VUD.BIN
```

W tej sesji Rust i pliki kompilacji umieszczono wyłącznie w `/tmp`.
Źródła narzędzia i Cargo.lock pozostają w repozytorium; surowego firmware
ani klucza testowego nie dodano do śledzonych plików.

**Rezultat:** nie uzyskano pełnego rozpakowania ani komend pochodzących
z kodu firmware. Wcześniejsza lista `X_Start`, `X_Invite` itd. nadal pochodzi
z publicznego opisu usługi żywego Sony, nie z analizowanego BIN.
