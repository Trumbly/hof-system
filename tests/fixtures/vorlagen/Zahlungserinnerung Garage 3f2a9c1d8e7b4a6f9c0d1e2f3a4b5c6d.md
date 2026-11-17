# Zahlungserinnerung Garage

Art: Zahlungserinnerung
Betreff: Zahlungserinnerung Vertrag {{ vertrag.nummer }}
Status: Entwurf
Zuletzt bearbeitet: 3. November 2026

{{ empfaenger.anrede|default:"Guten Tag" }},

für den Vertrag {{ vertrag.nummer }} über die Einheit {{ einheit.bezeichnung }} ist bis heute ein Betrag von **{{ betrag.offen|euro }}** offen.

{% if forderung.posten %}Offen sind die Monate {% for p in forderung.posten %}{{ p.monat }}{% if not forloop.last %}, {% endif %}{% endfor %}.{% endif %}

Bitte überweisen Sie den Betrag innerhalb von 14 Tagen. Falls Sie inzwischen gezahlt haben, betrachten Sie dieses Schreiben als gegenstandslos.

Mit freundlichen Grüßen
{{ absender.name }}
