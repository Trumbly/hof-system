"""Übernimmt die Zählerstände aus dem JSONField ``stammdaten.Uebergabe.zaehlerstaende``.

Bis Schritt 8 standen die Stände einer Übergabe als Liste
``[{"nummer": "Z-1", "stand": "1234.5"}, …]`` an der Übergabe. Jeder Eintrag, dessen Zähler
per Nummer gefunden wird, wird ein ``Zaehlerstand`` (abgelesen, bei Übergabe, Datum der
Übergabe). Einträge ohne passenden Zähler, mit unlesbarem Stand oder mit einem Stand, der
nicht zu den Nachbarständen passt, entfallen; die Ausgabe nennt nur ihre Anzahl.

Danach entfernt ``stammdaten/0002`` das Feld.
"""

from decimal import Decimal, InvalidOperation

from django.db import migrations


def uebernehmen(apps, schema_editor):  # type: ignore[no-untyped-def]
    Uebergabe = apps.get_model("stammdaten", "Uebergabe")
    Zaehler = apps.get_model("zaehler", "Zaehler")
    Zaehlerstand = apps.get_model("zaehler", "Zaehlerstand")
    uebernommen = entfallen = 0
    for pk, datum, eintraege in Uebergabe.objects.order_by("datum", "pk").values_list(
        "pk", "datum", "zaehlerstaende"
    ):
        for eintrag in eintraege or []:
            nummer = str(eintrag.get("nummer", "")).strip()
            try:
                stand = Decimal(str(eintrag.get("stand", "")).replace(",", ".")).quantize(
                    Decimal("0.001")
                )
            except (InvalidOperation, ValueError):
                entfallen += 1
                continue
            zaehler = Zaehler.objects.filter(nummer=nummer).first() if nummer else None
            staende = Zaehlerstand.objects.filter(zaehler=zaehler)
            if (
                zaehler is None
                or stand < 0
                or staende.filter(datum=datum).exists()
                or staende.filter(datum__lt=datum, stand__gt=stand).exists()
                or staende.filter(datum__gt=datum, stand__lt=stand).exists()
            ):
                entfallen += 1
                continue
            Zaehlerstand.objects.create(
                zaehler=zaehler,
                datum=datum,
                stand=stand,
                art="abgelesen",
                bei_uebergabe=True,
                uebergabe_id=pk,
            )
            uebernommen += 1
    if uebernommen or entfallen:
        print(f"  Zählerstände aus Übergaben: {uebernommen} übernommen, {entfallen} entfallen")


class Migration(migrations.Migration):
    dependencies = [
        ("stammdaten", "0001_initial"),
        ("zaehler", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(uebernehmen, migrations.RunPython.noop),
    ]
