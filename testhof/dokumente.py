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
from apps.dokumente.models import Vorlage
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

#: Aufstellung der offenen Posten in Zahlungserinnerung und Mahnstufen (Review 2, A2): je
#: Posten Soll, bezahlt und offen, darunter die Summen. ``forderung.summe`` ist derselbe
#: Betrag wie ``betrag.offen`` im Text (beides aus ``soll.services.offen``).
FORDERUNG_TABELLE = """\
<table class="forderung">
<thead><tr><th>Posten</th><th>fällig am</th><th class="zahl">Soll</th>\
<th class="zahl">bezahlt</th><th class="zahl">offen</th></tr></thead>
<tbody>
{% for p in forderung.posten %}<tr><td>{{ p.art }} {{ p.monat }}</td>\
<td>{{ p.faellig_am|datum }}</td><td class="zahl">{{ p.soll|euro }}</td>\
<td class="zahl">{{ p.bezahlt|euro }}</td><td class="zahl">{{ p.offen|euro }}</td></tr>
{% endfor %}</tbody>
<tfoot><tr><td colspan="2">Summe</td><td class="zahl">{{ forderung.summe_soll|euro }}</td>\
<td class="zahl">{{ forderung.summe_bezahlt|euro }}</td>\
<td class="zahl">{{ forderung.summe|euro }}</td></tr></tfoot>
</table>
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
{{ betrag.offen|euro }} offen:</p>
"""
        + FORDERUNG_TABELLE
        + """\
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
    # Stammdaten (Schritt 4): Vertrag, Kündigungsbestätigung, Kaution, Protokolle
    (
        "Vertrag",
        "vertrag",
        "{{ vertrag.art_titel }}-Mietvertrag {{ vertrag.nummer }}",
        """\
<h2>{{ vertrag.art_titel }}-Mietvertrag {{ vertrag.nummer }}</h2>
<p>zwischen {{ mandant.bezeichnung }} (Vermieter) und
{% for m in mieter %}{{ m.name }}, {{ m.strasse }}, {{ m.plz }} {{ m.ort }}
{% if not forloop.last %}; {% endif %}{% endfor %} (Mieter).</p>
<h3>§ 1 Mietsache</h3>
<ul>{% for e in einheiten %}<li>{{ e.nw_id }}: {{ e.titel }}{% if e.flaeche_qm %}
({{ e.flaeche_qm }} m²){% endif %}, {{ e.objekt }}</li>{% endfor %}</ul>
<h3>§ 2 Mietzeit</h3>
<p>Das Mietverhältnis beginnt am {{ vertrag.beginn|datum }}{% if vertrag.ende %} und endet am
{{ vertrag.ende|datum }}{% else %} und läuft auf unbestimmte Zeit{% endif %}.
Kündigungsfrist: {{ vertrag.kuendigungsfrist }}.</p>
<h3>§ 3 Miete</h3>
{% if kondition %}<p>Die Miete beträgt {{ kondition.miete|euro }}
je Monat{% if kondition.pauschale %} zuzüglich einer Pauschale von
{{ kondition.pauschale|euro }}{% endif %}, zahlbar
{{ kondition.zahlweise }}.</p>{% endif %}
{% if staffel|length > 1 %}<p>Staffel:</p><ul>{% for s in staffel %}<li>ab {{ s.gueltig_ab|datum }}:
{{ s.miete|euro }}</li>{% endfor %}</ul>{% endif %}
<h3>§ 4 Kaution</h3>
<p>{% if kaution.betrag %}Der Mieter leistet eine Kaution von {{ kaution.betrag|euro }}.
{% else %}Eine Kaution ist nicht vereinbart.{% endif %}</p>
{% if vertrag.sonderabreden %}<h3>§ 5 Sonderabreden</h3>
<p>{{ vertrag.sonderabreden }}</p>{% endif %}
<p style="margin-top: 3em">Ort, Datum, Vermieter ______________ Mieter ______________</p>
""",
    ),
    (
        "Kündigungsbestätigung",
        "kuendigungsbestaetigung",
        "Bestätigung Ihrer Kündigung, Vertrag {{ vertrag.nummer }}",
        """\
<p>{{ empfaenger.anrede|default:"Guten Tag" }},</p>
{% if kuendigung.textform %}<p>Ihre Kündigung des Vertrags {{ vertrag.nummer }} über
{{ einheit.bezeichnung }} ({{ einheit.nw_id }}) ist am {{ kuendigung.zugang_am|datum }} über
{{ kuendigung.zugangsweg }} bei uns eingegangen.</p>
{% else %}<p>Ihre schriftliche Kündigung des Vertrags {{ vertrag.nummer }} über
{{ einheit.bezeichnung }} ({{ einheit.nw_id }}) ist am {{ kuendigung.zugang_am|datum }} bei
uns eingegangen.</p>
{% endif %}<p>Das Mietverhältnis endet nach der vereinbarten Frist
({{ kuendigung.kuendigungsfrist }}) am {{ kuendigung.fristende|datum }}.</p>
<p>Als Termin für die Rückgabe mit Protokoll und allen Schlüsseln schlagen wir den
{{ kuendigung.rueckgabetermin|datum }} vor.</p>
<p>Mit freundlichen Grüßen<br>{{ absender.name }}</p>
""",
    ),
    # Review 2, A11: Aufhebung zum Wunschtermin vor dem Fristende.
    (
        "Aufhebungsvertrag",
        "aufhebungsvertrag",
        "Aufhebungsvertrag zum Vertrag {{ vertrag.nummer }}",
        """\
<p>{{ empfaenger.anrede|default:"Guten Tag" }},</p>
<p>Ihre Kündigung des Vertrags {{ vertrag.nummer }} über {{ einheit.bezeichnung }}
({{ einheit.nw_id }}) ist am {{ kuendigung.zugang_am|datum }} bei uns eingegangen. Nach der
vereinbarten Frist ({{ kuendigung.kuendigungsfrist }}) endet der Vertrag am
{{ aufhebung.fristende_ordentlich|datum }}.</p>
<p>Sie wünschen ein Ende zum {{ kuendigung.zum_wunsch|datum }}. Dazu schlagen wir vor, den
Vertrag einvernehmlich zum {{ aufhebung.zum|datum }} aufzuheben. Die Rückgabe mit Protokoll
und allen Schlüsseln ist am {{ aufhebung.rueckgabetermin|datum }}.</p>
<p>Die Aufhebung gilt, wenn beide Seiten diesen Aufhebungsvertrag unterschrieben haben.
Bitte senden Sie uns ein unterschriebenes Exemplar zurück.</p>
<p>Mit freundlichen Grüßen<br>{{ absender.name }}</p>
<p>Einverstanden:</p>
<p>________________________ (Vermieter) &nbsp; ________________________ (Mieter)</p>
""",
    ),
    (
        "Kautionsabrechnung",
        "kautionsabrechnung",
        "Abrechnung der Kaution, Vertrag {{ vertrag.nummer }}",
        """\
<p>{{ empfaenger.anrede|default:"Guten Tag" }},</p>
<p>für den Vertrag {{ vertrag.nummer }} rechnen wir die Kaution ab:</p>
<table>
<tr><td>Kaution</td><td>{{ abrechnung.betrag|euro }}</td></tr>
<tr><td>Einbehalt</td><td>{{ abrechnung.einbehalt|euro }}</td></tr>
<tr><td>Auszahlung</td><td>{{ abrechnung.ausgezahlt|euro }}</td></tr>
</table>
<p>Mit freundlichen Grüßen<br>{{ absender.name }}</p>
""",
    ),
    (
        "Übergabeprotokoll",
        "uebergabeprotokoll",
        "Übergabeprotokoll Vertrag {{ vertrag.nummer }}",
        """\
<p>Übergabe am {{ uebergabe.datum|datum }}:
{% for e in einheiten %}{{ e.nw_id }} {% endfor %}</p>
<p>Zählerstände: {% for z in uebergabe.zaehlerstaende %}{{ z.nummer }}: {{ z.stand }}
{% empty %}keine{% endfor %}</p>
<p>Schlüssel: {% for s in uebergabe.schluessel %}{{ s.anzahl }} × {{ s.art }}
{% empty %}keine{% endfor %}</p>
<p>Zustand: {{ uebergabe.zustand }} · Fotos: {{ uebergabe.fotos }}</p>
""",
    ),
    (
        "Rückgabeprotokoll",
        "rueckgabeprotokoll",
        "Rückgabeprotokoll Vertrag {{ vertrag.nummer }}",
        """\
<p>Rückgabe am {{ uebergabe.datum|datum }}:
{% for e in einheiten %}{{ e.nw_id }} {% endfor %}</p>
<p>Zählerstände: {% for z in uebergabe.zaehlerstaende %}{{ z.nummer }}: {{ z.stand }}
{% empty %}keine{% endfor %}</p>
<p>Schlüssel zurück: {% for s in uebergabe.schluessel %}{{ s.anzahl }} × {{ s.art }}
{% if s.fehlend %}({{ s.fehlend }} fehlen){% endif %} {% empty %}keine{% endfor %}</p>
<p>Zustand: {{ uebergabe.zustand }} · Zurückgelassen: {{ uebergabe.zurueckgelassen }}</p>
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


register.registrieren(NAME, abhaengig_von=("kern",), aufbauen=aufbauen)
