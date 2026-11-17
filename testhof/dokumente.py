"""Testhof-Modul der Dokumente: Briefkopf und einige freigegebene Beispielvorlagen.

Die Texte enthalten nur Platzhalter, nie Namen, Anschriften oder Beträge (CLAUDE.md,
Abschnitt 1; Test ``tests/dokumente/test_vorlagen_ohne_namen.py``). Freigegeben sind sie
vom Testbenutzer zum festen Zeitpunkt des Testhofs, damit gleicher Seed gleiche Daten
ergibt. Schreiben legt der Testhof keine an.

Geschrieben wird über ``apps.dokumente.services``.
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.dokumente import services
from apps.dokumente.models import Schreiben, Vorlage
from testhof import register
from testhof.aufbau import Kontext

NAME = "dokumente"

BRIEFKOPF = """\
<div class="kopf">
  <p style="font-size: 16pt; font-weight: bold; margin: 0">{{ absender.name }}</p>
  <p style="margin: 0">{{ absender.anschrift|linebreaksbr }}</p>
</div>
<div class="ruecksendeangabe">{{ absender.name }} · {{ absender.anschrift|einzeilig }}</div>
<div class="fuss">
  {{ absender.name }} · {{ absender.email }}
  {% if absender.telefon %} · {{ absender.telefon }}{% endif %}
  {% if absender.bank %}<br>Bankverbindung: {{ absender.bank }}{% endif %}
  {% if absender.steuernummer %}<br>Steuernummer: {{ absender.steuernummer }}{% endif %}
</div>
"""

#: (Name, Art, Betreff, Text)
VORLAGEN: tuple[tuple[str, str, str, str], ...] = (
    ("Briefkopf Hof", "briefkopf", "", BRIEFKOPF),
    (
        "Mitteilung an Nutzer",
        "mitteilung",
        "Mitteilung zu {{ einheit.bezeichnung }}",
        """\
<p>{{ empfaenger.anrede|default:"Guten Tag" }},</p>
<p>wir möchten Sie zu Ihrer Einheit {{ einheit.kuerzel }} im Objekt {{ objekt.bezeichnung }}
informieren.</p>
<p>Mit freundlichen Grüßen<br>{{ absender.name }}</p>
""",
    ),
    (
        "Aushang Objekt",
        "aushang",
        "Aushang: {{ objekt.bezeichnung }}",
        """\
<p>{{ empfaenger.anrede }},</p>
<p>für das Objekt {{ objekt.bezeichnung }} ({{ objekt.kuerzel }}) gilt ab sofort Folgendes.</p>
<p>Mit freundlichen Grüßen<br>{{ absender.name }}</p>
""",
    ),
    (
        "Zahlungserinnerung",
        "zahlungserinnerung",
        "Zahlungserinnerung Vertrag {{ vertrag.nummer }}",
        """\
<p>{{ empfaenger.anrede|default:"Guten Tag" }},</p>
<p>für den Vertrag {{ vertrag.nummer }} ({{ einheit.bezeichnung }}) ist ein Betrag von
{{ betrag.offen|euro }} offen.</p>
{% if forderung.posten %}<table>
{% for p in forderung.posten %}
<tr><td>{{ p.monat }}</td><td>{{ p.betrag|euro }}</td></tr>
{% endfor %}
</table>{% endif %}
<p>Bitte überweisen Sie den Betrag in den nächsten Tagen.</p>
<p>Mit freundlichen Grüßen<br>{{ absender.name }}</p>
""",
    ),
    (
        "Kündigung durch den Hof",
        "kuendigung",
        "Kündigung des Vertrags {{ vertrag.nummer }}",
        """\
<p>{{ empfaenger.anrede|default:"Guten Tag" }},</p>
<p>hiermit kündigen wir den Vertrag {{ vertrag.nummer }} über {{ einheit.bezeichnung }}
im Objekt {{ objekt.bezeichnung }} fristgerecht zum {{ vertrag.ende|datum }}.</p>
<p>Bitte vereinbaren Sie mit uns einen Termin für die Rückgabe.</p>
<p>Mit freundlichen Grüßen<br>{{ absender.name }}</p>
""",
    ),
)


@dataclass(frozen=True)
class DokumenteDaten:
    vorlagen: dict[str, Vorlage]  # nach Art, z. B. ``vorlagen["briefkopf"]``


def aufbauen(kontext: Kontext) -> DokumenteDaten:
    benutzer = kontext.ablage["kern"].benutzer
    vorlagen = {}
    for name, art, betreff, text in VORLAGEN:
        vorlage = services.vorlage_anlegen(name, art, text, betreff=betreff, herkunft="testhof")
        vorlagen[art] = services.vorlage_freigeben(vorlage, benutzer, am=kontext.zeitpunkt)
    return DokumenteDaten(vorlagen=vorlagen)


def leeren() -> None:
    """Löscht Schreiben und Vorlagen (nur erfundene Daten)."""
    Schreiben.objects.all().delete()
    Vorlage.objects.all().delete()


register.registrieren(NAME, abhaengig_von=("kern",), aufbauen=aufbauen, leeren=leeren)
