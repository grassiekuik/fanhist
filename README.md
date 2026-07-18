# fanhist

Een kleine, zelf-gehoste iDRAC fan controller — geïnspireerd op [Hush](https://github.com/natankeddem/hush),
maar simpeler opgezet en met ingebouwde geschiedenis.

## ⚠️ Waarschuwing

Dit is een v1, gebouwd en getest tegen één specifieke omgeving (Dell R720, iDRAC 7). Een bug
of verkeerde configuratie kan ertoe leiden dat de fans niet (voldoende) opschalen bij hitte.
Gebruik op eigen risico, houd in de gaten of de curve doet wat je verwacht, en overweeg een
externe temperatuur-alert (bijv. in Home Assistant of Grafana) als extra vangnet.

## Wat het doet

- Leest CPU/Inlet-temperatuur rechtstreeks via `ipmitool` (lokaal IPMI, geen Redfish/TLS nodig —
  dat bleek op oudere iDRAC-generaties zoals iDRAC 7 onbetrouwbaar traag)
- Leest optioneel een disktemperatuur op via SSH (bijv. TrueNAS `drivetemp`/hwmon)
- Berekent het fanpercentage via een instelbare curve (temperatuur → %)
- Zet de fans via IPMI raw-commands (`0x30 0x30 ...`)
- Logt elke meting naar SQLite en toont een grafiek + curve-editor op een klein dashboard

## Snel starten

1. Zorg dat IPMI over LAN aanstaat op je iDRAC (iDRAC Settings → Network → IPMI Settings).
2. Start:

   ```bash
   docker compose up -d --build
   ```

3. Open `http://<host>:8181` voor het dashboard.
4. Scroll naar "Instellingen" en vul je iDRAC-host, gebruiker, wachtwoord en sensornaam in,
   klik "Verbinding testen" om te checken of het werkt, en klik daarna "Instellingen opslaan".
5. (Optioneel, voor disktemperatuur) Klik in hetzelfde paneel op "Sleutel (opnieuw) genereren".
   De publieke sleutel verschijnt meteen — plak die in `authorized_keys` van je NAS/host (of via
   de TrueNAS UI onder Credentials → Users → SSH Public Key). Vul daarna de SSH-host/gebruiker
   in, klik "Diskverbinding testen", en sla op.

Alle instellingen (inclusief de iDRAC-gegevens en de SSH-sleutel) worden bewaard in de SQLite-
database onder `./data` — dus ze overleven een herstart of rebuild van de container.

## Instellingen

Alles is te configureren vanuit het dashboard (paneel "Instellingen"), geen environment-
variabelen of herstart nodig:

- **iDRAC**: host/IP, gebruiker, wachtwoord, sensornaam (`ipmitool sensor list` voor opties)
- **Disktemperatuur (optioneel)**: SSH-host, SSH-gebruiker, hoe meerdere disks gecombineerd
  worden (gemiddelde/max/min), en het commando dat de temperaturen uitleest. De SSH-sleutel
  wordt in de container gegenereerd via de knop "Sleutel (opnieuw) genereren" — er hoeft dus
  niets handmatig gekopieerd te worden naar de container zelf.
- **Algemeen**: meetinterval, IPMI-timeout, hoe lang geschiedenis bewaard blijft

Alleen `DB_PATH` (waar de SQLite-database staat) is nog een environment-variabele, voor als je
die ergens anders wilt zetten dan het standaard `/data/fanhist.db`.

## Curve aanpassen

Open het dashboard, scroll naar "Fan curve", pas punten aan of voeg toe, en klik "Opslaan".
De curve wordt lineair geïnterpoleerd tussen de punten; onder het laagste punt geldt het
laagste percentage, boven het hoogste punt het hoogste.

## Bekende beperkingen (v1)

- Geen authenticatie op het dashboard — niet naar het publieke internet exposen.
- Eén iDRAC per container; voor meerdere hosts draai je meerdere instanties.
- `DISK_TEMP_CMD` gaat uit van een Linux-hwmon-achtige uitvoer; pas aan voor andere OS'en.

## Licentie

MIT — zie [LICENSE](LICENSE).
