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
2. Kopieer je SSH-key (voor disktemp-uitlezing) naar `./ssh/id_ed25519` in dit project,
   of laat `DISK_SSH_HOST` leeg als je geen disktemp wilt meenemen.
3. Pas de environment-variabelen in `docker-compose.yml` aan naar jouw omgeving.
4. Start:

   ```bash
   docker compose up -d --build
   ```

5. Open `http://<host>:8181` voor het dashboard.

## Configuratie (environment variabelen)

| Variabele | Standaard | Omschrijving |
|---|---|---|
| `IDRAC_HOST` | `192.168.50.11` | IP van de iDRAC |
| `IDRAC_USER` | `root` | iDRAC-gebruiker |
| `IDRAC_PASS` | — | iDRAC-wachtwoord |
| `CPU_SENSOR_NAME` | `Inlet Temp` | Naam van de IPMI-sensor (`ipmitool sensor list` voor opties) |
| `DISK_SSH_HOST` | — | IP voor disktemp over SSH; leeg = uitgeschakeld |
| `DISK_SSH_USER` | `root` | SSH-gebruiker |
| `DISK_SSH_KEY` | `/config/ssh/id_ed25519` | Pad naar SSH-key in de container |
| `DISK_TEMP_CMD` | zoekt automatisch alle `drivetemp`-hwmons | Commando dat één temperatuur per regel teruggeeft (m°C of °C). Meerdere regels = meerdere disks. |
| `DISK_TEMP_AGGREGATION` | `avg` | Hoe meerdere disktemps gecombineerd worden: `avg`, `max`, of `min` |
| `INTERVAL_SECONDS` | `30` | Hoe vaak gemeten en bijgestuurd wordt |
| `IPMI_TIMEOUT` | `10` | Timeout per IPMI/SSH-call in seconden |
| `HISTORY_RETENTION_DAYS` | `30` | Hoe lang metingen bewaard blijven |

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
