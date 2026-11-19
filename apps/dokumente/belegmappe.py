"""Belegmappe je Objekt und Leistungsjahr (H6.9), nur lesend.

Die Mappe ist ein PDF: vorn Deckblatt und Inhaltsverzeichnis (WeasyPrint), dahinter die PDFs
der Belege (zusammengefügt mit pypdf). Vor dem Erzeugen vergleicht sie je Kostenart die
Summe der Belege mit dem Kassenbuch:

- Kassenbuch: alle Buchungen des Objekts mit diesem Leistungsjahr und einer Kategorie der Art
  „Ausgabe“, mit Vorzeichen (eine Stornobuchung hebt ihr Original auf), als Kosten positiv.
- Belege: bestätigte Belege mit diesem Objekt und Leistungsjahr.

Jede Abweichung steht als Hinweis vorn in der Mappe: eine Kostenart mit anderer Summe,
Buchungen ohne Beleg, Belege ohne Buchung. Die Mappe entsteht trotzdem.
"""

from __future__ import annotations

import io
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Count
from django.template.loader import render_to_string
from django.utils import timezone
from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError

from apps.dokumente import pdf
from apps.dokumente.models import Beleg, Dokument
from apps.kassenbuch import auswertung
from apps.kassenbuch.models import Buchung, Kategorie
from apps.kern.models import Objekt

logger = logging.getLogger(__name__)

NULL = Decimal("0.00")
HUNDERT = Decimal("100")


@dataclass
class Zeile:
    nummer: int
    beleg: Beleg
    #: Seite des Belegs in der fertigen Mappe (0: noch nicht bekannt)
    seite: int = 0

    @property
    def umlagefaehig(self) -> Decimal:
        betrag = self.beleg.betrag or NULL
        anteil = self.beleg.umlagefaehiger_anteil or NULL
        return (betrag * anteil / HUNDERT).quantize(Decimal("0.01"))


@dataclass(frozen=True)
class Kostenart:
    kategorie_id: int
    name: str
    belege: Decimal
    kassenbuch: Decimal
    umlagefaehig: Decimal

    @property
    def differenz(self) -> Decimal:
        return self.belege - self.kassenbuch

    @property
    def stimmt(self) -> bool:
        return self.differenz == 0


@dataclass
class Mappe:
    objekt: Objekt
    jahr: int
    zeilen: list[Zeile] = field(default_factory=list)
    kostenarten: list[Kostenart] = field(default_factory=list)
    buchungen_ohne_beleg: list[int] = field(default_factory=list)
    belege_ohne_buchung: list[int] = field(default_factory=list)
    nicht_lesbar: list[int] = field(default_factory=list)

    @property
    def summe_belege(self) -> Decimal:
        return sum((k.belege for k in self.kostenarten), NULL)

    @property
    def summe_kassenbuch(self) -> Decimal:
        return sum((k.kassenbuch for k in self.kostenarten), NULL)

    @property
    def summe_umlagefaehig(self) -> Decimal:
        return sum((k.umlagefaehig for k in self.kostenarten), NULL)

    @property
    def stimmt(self) -> bool:
        """Summen je Kostenart gleich dem Kassenbuch, jede Buchung mit Beleg und umgekehrt."""
        return (
            all(k.stimmt for k in self.kostenarten)
            and not self.buchungen_ohne_beleg
            and not self.belege_ohne_buchung
        )

    def hinweise(self) -> list[str]:
        """Abweichungen zum Kassenbuch, mit IDs (ohne Namen)."""
        texte = [
            f"Kostenart „{k.name}“: Belege {_euro(k.belege)}, Kassenbuch {_euro(k.kassenbuch)}, "
            f"Differenz {_euro(k.differenz)}."
            for k in self.kostenarten
            if not k.stimmt
        ]
        if self.buchungen_ohne_beleg:
            ids = ", ".join(f"#{i}" for i in self.buchungen_ohne_beleg)
            texte.append(f"Buchungen ohne Beleg: {ids}.")
        if self.belege_ohne_buchung:
            ids = ", ".join(f"#{i}" for i in self.belege_ohne_buchung)
            texte.append(f"Belege ohne Buchung im Kassenbuch: {ids}.")
        if self.nicht_lesbar:
            ids = ", ".join(f"#{i}" for i in self.nicht_lesbar)
            texte.append(f"Diese Dokumente ließen sich nicht anhängen: {ids}.")
        return texte

    @property
    def dateiname(self) -> str:
        return f"belegmappe-{self.objekt.kuerzel}-{self.jahr}.pdf"


def _euro(betrag: Decimal) -> str:
    text = f"{betrag:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{text} €"


def daten(objekt: Objekt, jahr: int) -> Mappe:
    """Belege, Summen je Kostenart und Abweichungen zum Kassenbuch."""
    mappe = Mappe(objekt=objekt, jahr=jahr)
    belege = list(
        Beleg.objects.filter(status=Beleg.Status.BESTAETIGT, objekt=objekt, leistungsjahr=jahr)
        .select_related("dokument", "lieferant", "kategorie", "buchung")
        .order_by("kategorie__art", "kategorie__bezeichnung", "dokument__datum", "pk")
    )
    mappe.zeilen = [Zeile(nummer=i, beleg=b) for i, b in enumerate(belege, start=1)]

    buchungen = Buchung.objects.filter(
        objekt=objekt, leistungsjahr=jahr, kategorie__art=Kategorie.Art.AUSGABE
    )
    kassenbuch = {
        z["kategorie_id"]: -(z["summe"] or NULL)
        for z in buchungen.values("kategorie_id").annotate(summe=auswertung.vorzeichen_summe())
    }
    je_kategorie: dict[int, Decimal] = defaultdict(lambda: NULL)
    umlage: dict[int, Decimal] = defaultdict(lambda: NULL)
    for zeile in mappe.zeilen:
        if zeile.beleg.kategorie_id is None:  # bestätigt heißt: Kostenart gesetzt
            continue
        je_kategorie[zeile.beleg.kategorie_id] += zeile.beleg.betrag or NULL
        umlage[zeile.beleg.kategorie_id] += zeile.umlagefaehig
    namen = dict(
        Kategorie.objects.filter(pk__in=set(kassenbuch) | set(je_kategorie)).values_list(
            "pk", "bezeichnung"
        )
    )
    mappe.kostenarten = [
        Kostenart(
            kategorie_id=pk,
            name=namen.get(pk, f"#{pk}"),
            belege=je_kategorie.get(pk, NULL),
            kassenbuch=kassenbuch.get(pk, NULL),
            umlagefaehig=umlage.get(pk, NULL),
        )
        for pk in sorted(set(kassenbuch) | set(je_kategorie), key=lambda p: namen.get(p, ""))
        if je_kategorie.get(pk, NULL) or kassenbuch.get(pk, NULL)
    ]

    belegte_umsaetze = {b.buchung.umsatz_id for b in belege if b.buchung is not None}
    gueltig = buchungen.filter(storno_von__isnull=True, storniert_durch__isnull=True)
    mappe.buchungen_ohne_beleg = list(
        gueltig.exclude(umsatz_id__in=belegte_umsaetze).order_by("pk").values_list("pk", flat=True)
    )
    mappe.belege_ohne_buchung = [b.pk for b in belege if b.buchung_id is None]
    return mappe


def vorgabe(jahr: int) -> Objekt | None:
    """Vorbelegung der Seite (Review 2, B11): das Objekt mit den meisten Belegen im Jahr,
    sonst das erste aktive."""
    am_meisten = (
        Beleg.objects.filter(status=Beleg.Status.BESTAETIGT, leistungsjahr=jahr)
        .values("objekt_id")
        .annotate(anzahl=Count("pk"))
        .order_by("-anzahl", "objekt_id")
        .first()
    )
    if am_meisten and am_meisten["objekt_id"]:
        return Objekt.objects.filter(pk=am_meisten["objekt_id"]).first()
    return Objekt.objects.filter(aktiv=True).order_by("kuerzel").first()


def buchungen(ids: list[int]) -> list[Buchung]:
    """Die Buchungen zu ``Mappe.buchungen_ohne_beleg`` mit Umsatz (Datum, Gegenpartei)."""
    return list(
        Buchung.objects.filter(pk__in=ids)
        .select_related("umsatz", "kategorie")
        .order_by("datum", "pk")
    )


def belege_fuer(buchung: Buchung, mappe: Mappe) -> list[Beleg]:
    """Belege, die zur Buchung passen können: bestätigt, noch ohne Buchung; zuerst die
    der Mappe, dann nach Abstand im Betrag und im Datum."""
    kandidaten = (
        Beleg.objects.filter(status=Beleg.Status.BESTAETIGT, buchung__isnull=True)
        .select_related("dokument", "lieferant", "kategorie", "objekt")
        .order_by("-dokument__datum", "-pk")[:200]
    )
    in_mappe = {z.beleg.pk for z in mappe.zeilen}

    def abstand(beleg: Beleg) -> tuple[int, Decimal, int]:
        betrag = abs((beleg.betrag or NULL) - buchung.betrag)
        tage = abs((beleg.dokument.datum - buchung.datum).days)
        return (0 if beleg.pk in in_mappe else 1, betrag, tage)

    return sorted(kandidaten, key=abstand)[:10]


def _seiten(inhalt: bytes) -> int:
    return len(PdfReader(io.BytesIO(inhalt)).pages)


def _deckblatt(mappe: Mappe) -> bytes:
    html = render_to_string(
        "dokumente/pdf/belegmappe.html",
        {"mappe": mappe, "hinweise": mappe.hinweise(), "erstellt": timezone.localdate()},
    )
    return pdf.erzeugen(html)


def _dokument_pdf(dokument: Dokument) -> bytes | None:
    try:
        with dokument.datei.open("rb") as datei:
            inhalt: bytes = datei.read()
        _seiten(inhalt)
    except (OSError, ValueError, PdfReadError):
        logger.warning("Belegmappe: Dokument #%s nicht lesbar", dokument.pk)
        return None
    return inhalt


def erzeugen(mappe: Mappe) -> bytes:
    """Setzt die Mappe als PDF zusammen: Deckblatt mit Inhaltsverzeichnis, dann die Belege.

    Ein Dokument mit mehreren Belegen (Sammelrechnung) hängt nur einmal an. Die Seitenzahlen
    im Inhaltsverzeichnis stimmen mit der fertigen Mappe überein.

    Raises:
        pdf.PdfNichtVerfuegbar: WeasyPrint ohne Systembibliotheken.
    """
    anhaenge: dict[int, bytes] = {}
    for zeile in mappe.zeilen:
        dokument = zeile.beleg.dokument
        if dokument.pk in anhaenge or dokument.pk in mappe.nicht_lesbar:
            continue
        inhalt = _dokument_pdf(dokument)
        if inhalt is None:
            mappe.nicht_lesbar.append(dokument.pk)
        else:
            anhaenge[dokument.pk] = inhalt

    deckblatt = _deckblatt(mappe)
    for _ in range(3):  # Seitenzahlen eintragen, bis das Deckblatt gleich lang bleibt
        vorn = _seiten(deckblatt)
        seite_von: dict[int, int] = {}
        naechste = vorn + 1
        for dokument_id, inhalt in anhaenge.items():
            seite_von[dokument_id] = naechste
            naechste += _seiten(inhalt)
        for zeile in mappe.zeilen:
            zeile.seite = seite_von.get(zeile.beleg.dokument_id, 0)
        deckblatt = _deckblatt(mappe)
        if _seiten(deckblatt) == vorn:
            break

    schreiber = PdfWriter()
    schreiber.append(io.BytesIO(deckblatt), outline_item="Inhaltsverzeichnis", import_outline=False)
    for zeile in mappe.zeilen:
        if zeile.seite and zeile.beleg.dokument_id in anhaenge:
            inhalt = anhaenge.pop(zeile.beleg.dokument_id)
            schreiber.append(
                io.BytesIO(inhalt), outline_item=f"Beleg {zeile.nummer}", import_outline=False
            )
    schreiber.add_metadata({"/Title": f"Belegmappe {mappe.objekt.kuerzel} {mappe.jahr}"})
    ausgabe = io.BytesIO()
    schreiber.write(ausgabe)
    return ausgabe.getvalue()
