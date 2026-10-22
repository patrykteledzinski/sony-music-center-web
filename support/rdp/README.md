# Połączenie RDP z Serverkiem

Adres przewodowy: `192.168.8.50:3389`. Adres Wi-Fi: `192.168.8.105:3389`.
Użytkownik: `set`; użyj swojego dotychczasowego hasła. Hasła nie zmieniano.

Profil `Serverek-stabilny.rdp` można zaimportować w Remminie. Ustawia adres
przewodowy, stałe 1920×1080, 24-bitowy kolor i wyłącza dynamiczną rozdzielczość.
Skalowanie obrazu po stronie klienta pozwala dopasować pulpit do okna.

W logu z 22 października 2026, 14:28:41, po udanym logowaniu wystąpił
`Can't attach to shared memory id 20 [Invalid argument]`. Pulpit GNOME
pozostał uruchomiony, ale xrdp przerwał przekazywanie obrazu. Znaleziono
zapisane skalowanie 133% i zmiany rozmiaru obrazu podczas łączenia.
Wyłączono funkcję `x11-randr-fractional-scaling` użytkownika `set`, a konfigurację
monitora `rdp0` ustawiono na skalę 1. Inne programy i sesje nie były zamykane.

Kopia wcześniejszych ustawień: `.local-backups/rdp-20261022T143319/`.
RDP odpowiada na obu adresach; pełne potwierdzenie wymaga ponownego
zalogowania z klienta i sprawdzenia obrazu. Błędy TLS w logu o 14:33:59
pochodzą z lokalnego testu samej negocjacji, nie z próby logowania użytkownika.
