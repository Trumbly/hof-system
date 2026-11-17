# Anbindung der Website an das Hof-System (Schritt 10)

Stand: Schritt 10, Prozessliste W1, W10, H1.6. Diese Datei ersetzt den Pull Request im Website-Repository: Sie beschreibt die Schnittstelle des Hof-Systems und was auf der Website zu ändern ist. Umsetzen lässt Max das im Website-Repository (Astro/Node).

Grundsatz: Die NW-ID entsteht im Hof-System und ist der Schlüssel jeder Einheit auf der Website. Die Website liest Katalog und Belegung aus dem Hof-System (der HelloHousing-Nachtlauf entfällt) und meldet Buchungen und Kündigungen als Ereignis. Den Vertrag legt Max in v1 von Hand an.

## 1. Zugang

| Punkt | Regel |
|---|---|
| Netz | Das Hof-System ist nur über Tailscale erreichbar (`https://hof-system.<tailnet>.ts.net`). Der Website-Server tritt dem Tailnet bei; eine Tailscale-ACL erlaubt ihm nur `hof-system:443`. |
| Token | Kopf `Authorization: Bearer hofw_…`. Max erzeugt ihn auf dem Server mit `manage.py website_token --bezeichnung "Website-Server"`; er erscheint genau einmal, gespeichert ist nur sein Hash. Ohne gültigen Token: `401`. |
| Tokenwechsel | `manage.py website_token --behalten` erzeugt einen zweiten gültigen Token (Wechsel ohne Ausfall), danach `manage.py website_token` ohne `--behalten` oder `--sperren`. |
| IP-Freigabe | Optional `HOF_WEBSITE_IP_FREIGABE` (Komma-getrennt, Adressen oder Netze, z. B. die Tailscale-IP des Website-Servers). Andere Absender: `403`. Hinter Caddy zählt die letzte Adresse in `X-Forwarded-For`. |
| Sitzung | Keine. Die Pfade unter `/api/website/` sind von der Anmeldung mit zweitem Faktor ausgenommen, wie `/gesund` und die Bank-Zulieferung. |
| Fehler | JSON `{"fehler": "…"}`. Die Meldung nennt Feldnamen, nie Werte aus der Anfrage. |

Der Token gehört nur auf den Website-**Server** (Umgebungsvariable, z. B. `HOF_API_TOKEN`), nie in den Browser oder ins öffentliche Bundle.

## 2. Endpunkte

### `GET /api/website/katalog`

Alle buchbaren Einheiten, die das Hof-System auf der Website vermarktet: NW-ID, Typ, Fläche, Preis, frei ab und Bilder. Keine Parteien, keine Verträge, kein Wohnraum (den führt Verwaltio).

Eine Einheit steht im Katalog, wenn
- ihre Vermarktung im Hof-System „Website“ ist und sie nicht aufgelöst ist,
- sie heute den Status „frei“ hat oder ihr nächster Status „frei“ ist (etwa nach einer Rückgabe zu einem späteren Tag),
- kein Vertrag sie ab diesem Tag bindet (Entwurf, aktiv, gekündigt) und
- keine angenommene Buchungsanfrage auf ihr liegt.

Eine Kündigung allein ändert nichts: Die Einheit bleibt vermietet, bis die Rückgabe erfasst ist. Mit der Rückgabe steht sie ohne Handgriff wieder im Katalog, frei ab dem Rückgabetag.

| Feld | Inhalt |
|---|---|
| `stand` | Tag, für den der Katalog gilt (JJJJ-MM-TT) |
| `anzahl` | Zahl der Einheiten |
| `einheiten[].nw_id` | NW-ID, z. B. `NW-G-0004`; Schlüssel auf der Website |
| `typ`, `typ_titel` | `garage`, `stellplatz`, `lager`, `halle`, `garten`, `aussenflaeche`, `sonstiges` und der deutsche Titel |
| `objekt` | Kürzel des Objekts, z. B. `HOF` |
| `bezeichnung` | Lage vor Ort, z. B. „Garage 4“ (ohne Namen) |
| `flaeche_qm` | Text mit zwei Nachkommastellen oder `null` |
| `preis_monat`, `waehrung` | Zielmiete je Monat am Tag `frei_ab` als Text, oder `null` („Preis auf Anfrage“) |
| `frei_ab` | ab wann buchbar; heute, wenn die Einheit jetzt frei ist |
| `slug` | Website-Slug aus dem Hof-System (leer, solange nicht gepflegt) |
| `bilder[]` | `url`, `beschreibung` (Alternativtext), `reihenfolge` |

Beispiel aus dem Testhof (Seed 42, Stichtag 01.11.2026, gekürzt auf zwei von zehn Einheiten; das zweite Bild ist zur Veranschaulichung ergänzt, der Testhof legt keine Bilder an):

```json
{
  "stand": "2026-11-01",
  "anzahl": 10,
  "einheiten": [
    {
      "nw_id": "NW-G-0004",
      "typ": "garage",
      "typ_titel": "Garage",
      "objekt": "HOF",
      "bezeichnung": "Garage 4",
      "flaeche_qm": "21.00",
      "preis_monat": "86.81",
      "waehrung": "EUR",
      "frei_ab": "2026-11-01",
      "slug": "",
      "bilder": []
    },
    {
      "nw_id": "NW-G-0012",
      "typ": "garage",
      "typ_titel": "Garage",
      "objekt": "HOF",
      "bezeichnung": "Garage 12",
      "flaeche_qm": "19.98",
      "preis_monat": "86.58",
      "waehrung": "EUR",
      "frei_ab": "2026-11-01",
      "slug": "",
      "bilder": [
        {
          "url": "https://hof-system.beispiel.ts.net/api/website/bilder/7",
          "beschreibung": "Tor von außen",
          "reihenfolge": 1
        }
      ]
    }
  ]
}
```

Caching: Die Antwort trägt `ETag` und `Cache-Control: private, no-cache`. Die Website speichert Katalog und ETag und fragt beim nächsten Mal mit `If-None-Match: <etag>`; ist nichts geändert, kommt `304` ohne Inhalt. Der ETag ändert sich spätestens am nächsten Tag (`stand`, `frei_ab`).

### `GET /api/website/bilder/<id>`

Ein Vermarktungsbild (JPEG, PNG oder WebP), nur mit Token. Bilder ändern sich nie; ein neues Bild bekommt eine neue ID (`Cache-Control: private, max-age=31536000, immutable`). Die Website lädt sie einmal, legt sie bei sich ab und liefert sie selbst aus: Der Browser der Besucher spricht nie mit dem Hof-System. Ein entferntes Bild liefert `404` und fehlt im nächsten Katalog.

Bilder pflegt Max im Hof-System unter „Anbindungen → Katalog → Bilder“. Sie zeigen die Einheit, keine Personen und keine Kennzeichen.

### `POST /api/website/ereignis`

Meldet eine Buchung oder Kündigung. Das Hof-System legt daraus eine Buchungsanfrage an der Einheit an und den Arbeitsposten „Buchungsanfrage prüfen“ (fällig nach zwei Tagen, mit Benachrichtigung). Max entscheidet: angenommen, abgelehnt, Vertrag angelegt (Buchung) oder Kündigung erfasst.

Beispiel-Payload (erfundene Daten):

```json
{
  "ereignis_id": "web-2026-000123",
  "art": "buchung",
  "nw_id": "NW-G-0004",
  "zeitpunkt": "2026-11-17T10:15:00+01:00",
  "wunsch_ab": "2026-12-01",
  "kontakt": {
    "name": "Hanna Erfunden",
    "email": "hanna.erfunden@testhof.invalid",
    "telefon": "0151 23456789"
  },
  "nachricht": "Ich brauche die Garage ab Dezember."
}
```

Kündigung: `"art": "kuendigung"`, dazu `"vertragsnummer": "V-0010"`, wenn die Person sie angibt (das Hof-System verknüpft dann den Vertrag der Einheit).

| Feld | Pflicht | Regel |
|---|---|---|
| `ereignis_id` | ja | eindeutig je Ereignis auf der Website, bis 100 Zeichen aus `A–Z a–z 0–9 . _ : -` |
| `art` | ja | `buchung` oder `kuendigung` |
| `nw_id` | ja | NW-ID aus dem Katalog |
| `zeitpunkt` | ja | ISO 8601, am besten mit Zeitzone (ohne gilt Europe/Berlin) |
| `wunsch_ab` | nein | JJJJ-MM-TT |
| `vertragsnummer` | nein | bis 20 Zeichen |
| `kontakt.name`, `.email`, `.telefon` | nein | bis 200/200/50 Zeichen; E-Mail muss gültig sein |
| `nachricht` | nein | bis 5000 Zeichen |

Antworten:

| Status | Bedeutung | Website tut |
|---|---|---|
| `201` | neu angelegt: `{"ereignis_id": "web-2026-000123", "anfrage": 1, "neu": true, "status": "offen"}` | Ereignis als zugestellt markieren |
| `200` | dieselbe Meldung kam schon (`"neu": false`) | als zugestellt markieren |
| `400` | Feld fehlt oder ungültig | nicht wiederholen, protokollieren, Max informieren |
| `401`, `403` | Token oder Adresse falsch | anhalten, Max informieren |
| `409` | dieselbe `ereignis_id` mit anderem Inhalt | Fehler auf der Website; neue ID vergeben |
| `422` | Einheit unbekannt, aufgelöst oder Wohnraum | nicht wiederholen; erscheint auch im ID-Abgleich |
| `5xx`, Netzfehler | Hof-System nicht erreichbar | später mit **derselben** `ereignis_id` wiederholen |

Idempotent: Dieselbe `ereignis_id` mit gleichem Inhalt ergibt immer dieselbe Anfrage. Die Website darf also jede Meldung so oft senden, bis sie `200` oder `201` bekommt.

Datenschutz: Name, E-Mail, Telefon und Nachricht sind im Hof-System Personenfelder. Sie erscheinen nur auf der Seite der Anfrage, nie in Logs, Arbeitslisten, Benachrichtigungen oder Berichten.

### Endpunkt auf der Website: `GET /api/hof/einheiten` (neu, für den ID-Abgleich)

Der Lauf `id_abgleich` (täglich 5:30) fragt die Website, welche Einheiten sie führt:

```
GET {HOF_WEBSITE_URL}/api/hof/einheiten
Authorization: Bearer {HOF_WEBSITE_TOKEN}

{"einheiten": [{"nw_id": "NW-G-0004", "slug": "garage-4"}, {"nw_id": "NW-S-0008", "slug": ""}]}
```

Die Website liefert alle Einheiten, die sie zeigt oder in ihrer Datenbank führt, nur mit NW-ID und Slug. Jede Abweichung (fehlt, unbekannt, andere Schreibweise, nicht vermarktet, Slug weicht ab) wird im Hof-System eine „ID-Abweichung“ in der Arbeitsliste und schließt sich, sobald sie behoben ist.

## 3. Wie die Website den Katalog liest

1. Serverseitig, nie im Browser: ein Abruf beim Build (statische Seiten) und danach periodisch, z. B. alle 10 Minuten, mit `If-None-Match`.
2. Ergebnis in der eigenen Datenbank oder Datei ablegen (letzter guter Stand mit Zeitpunkt). Seiten lesen nur diesen Stand.
3. Fehlerverhalten:
   - Netzfehler, Zeitüberschreitung (10 s) oder `5xx`: letzten guten Stand weiter zeigen; nach 24 Stunden ohne Erfolg Hinweis „Verfügbarkeit bitte anfragen“ statt „frei ab“ und eine Meldung an Max.
   - `401`/`403`: letzten Stand behalten, sofort Max melden (Token gewechselt oder Adresse nicht freigegeben).
   - Nie den Katalog leeren, weil ein Abruf scheitert.
4. Einheiten, die nicht mehr im Katalog stehen, sind belegt: Buchungsformular aus, Seite darf bleiben („derzeit vermietet“).
5. `preis_monat` `null`: „Preis auf Anfrage“.

Ereignisse: Die Website speichert jede Buchung und Kündigung zuerst bei sich (Ausgang mit `ereignis_id`), sendet sie dann und wiederholt bei Netzfehlern mit Abstand (1, 5, 30 Minuten, dann stündlich). So geht keine Buchung verloren, wenn das Hof-System gerade nicht erreichbar ist.

## 4. Checkliste für das Website-Repository

- [ ] Umgebungsvariablen auf dem Website-Server: `HOF_API_URL` (`https://hof-system.<tailnet>.ts.net`), `HOF_API_TOKEN` (aus `manage.py website_token`), `HOF_ABGLEICH_TOKEN` (eigener Token, den das Hof-System für `/api/hof/einheiten` sendet; derselbe Wert steht dort als `HOF_WEBSITE_TOKEN`).
- [ ] Website-Server dem Tailnet hinzufügen; ACL nur auf `hof-system:443`.
- [ ] Katalog-Client: `GET /api/website/katalog` mit Token und `If-None-Match`, Zeitlimit 10 s, Ablage des letzten guten Stands, Fehlerverhalten wie in Abschnitt 3.
- [ ] Datenmodell: NW-ID als Schlüssel jeder Einheit (statt der Einträge aus dem Verwaltungsbereich). Typ, Fläche, Preis, frei ab und Bilder kommen aus dem Katalog; Texte und Lageplan bleiben auf der Website.
- [ ] Bilder: aus `bilder[].url` laden, lokal ablegen und selbst ausliefern; nach ID zwischenspeichern.
- [ ] Belegung: Einheiten ohne Katalogeintrag als belegt zeigen, Buchung dort sperren.
- [ ] Buchungsformular und Kündigungsformular senden `POST /api/website/ereignis` mit eindeutiger `ereignis_id`, über einen Ausgang mit Wiederholung (Abschnitt 3).
- [ ] Neuer Endpunkt `GET /api/hof/einheiten` mit Prüfung von `HOF_ABGLEICH_TOKEN`, liefert NW-ID und Slug aller Einheiten.
- [ ] Nachtlauf zu HelloHousing (Belegung und Buchungen) abschalten, sobald der Katalog läuft.
- [ ] Slugs: im Hof-System je Einheit `website_slug` pflegen (heute leer) oder Slugs auf der Website aus der NW-ID bilden.
- [ ] Tests auf der Website mit einem Fake der Hof-Schnittstelle; keine echten Daten in Tests.

## 5. Offen für Max

- Netzweg: Website-Server im Tailnet (empfohlen) oder ein eigener öffentlicher Zugang nur für `/api/website/` mit IP-Freigabe. Der zweite Weg braucht eine Änderung am Caddyfile und am Netz.
- Preis: `preis_monat` ist die Zielmiete, wie im Hof-System gepflegt. Ob die Website netto oder brutto zeigt, hängt an der Frage zur Umsatzsteuer (Bauplan, offene Punkte).
- Welche Einheiten „Vermarktung: Website“ haben, pflegst du im Hof-System an der Einheit.
