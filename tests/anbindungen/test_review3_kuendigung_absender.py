"""Review 3, Punkt 9: Kündigung durch jemanden, der nicht als Mieter am Vertrag steht.

Weicht der Absender einer Website-Kündigung ab (Name oder E-Mail-Adresse), braucht es die
Wahl „Vollmacht liegt vor“ oder „Absender ist Vertragspartei (anderer Name/E-Mail)“; ohne sie
bleibt „Kündigung anlegen“ gesperrt, im Formular und im Service. Alle Namen sind erfunden.
"""

from __future__ import annotations

import datetime

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from pghistory.models import Events

from apps.anbindungen import services
from apps.anbindungen.models import Buchungsanfrage
from apps.kern import vorgaenge
from apps.kern.models import Objekt
from apps.stammdaten.models import Kuendigung, Vertrag
from tests.anbindungen.conftest import aktiver_vertrag, vermarktete_einheit

pytestmark = pytest.mark.django_db
D = datetime.date
ZUGANG = D(2026, 11, 17)
HTMX = {"HTTP_HX_REQUEST": "true"}
DIALOG = {**HTMX, "HTTP_HX_TARGET": "dialog-inhalt"}


@pytest.fixture
def vertrag(objekt: Objekt) -> Vertrag:
    # Mieter: Erika Testmieter, testmieter@testhof.invalid
    return aktiver_vertrag(vermarktete_einheit(objekt))


def _anfrage(vertrag: Vertrag, name: str, email: str) -> Buchungsanfrage:
    anfrage, _ = services.ereignis_annehmen(
        services.WebsiteEreignis(
            ereignis_id=f"w-kuendigung-{email}",
            art="kuendigung",
            nw_id=vertrag.einheiten.get().nw_id,
            gemeldet_am=timezone.make_aware(datetime.datetime(2026, 11, 17, 10, 15)),
            vertragsnummer=vertrag.nummer,
            kontakt_name=name,
            kontakt_email=email,
        )
    )
    return anfrage


@pytest.fixture
def fremd(vertrag: Vertrag) -> Buchungsanfrage:
    """Kündigung von „Martine Mende“, Mieter ist „Erika Testmieter“ (wie Anfrage #15)."""
    return _anfrage(vertrag, "Martine Mende", "martine.mende@testhof.invalid")


def _formular(anfrage: Buchungsanfrage, **felder: str) -> dict[str, str]:
    return {
        "aktion": "kuendigung",
        "kuendigung-zugang_am": ZUGANG.isoformat(),
        "kuendigung-von": "mieter",
        "kuendigung-zugangsweg": "website",
        "kuendigung-anfrage": str(anfrage.pk),
        **{f"kuendigung-{k}": v for k, v in felder.items()},
    }


def test_service_sperrt_ohne_wahl(vertrag: Vertrag, fremd: Buchungsanfrage) -> None:
    """Review 3, Punkt 9: ``kuendigung_aus_anfrage`` legt bei abweichendem Absender ohne Wahl
    nichts an; die Meldung nennt keine Namen. Mit „Vollmacht liegt vor“ geht es, die Wahl
    steht an der Kündigung und im Titel des Vorgangs (Protokoll)."""
    with pytest.raises(services.AbsenderUngeklaert) as fehler:
        services.kuendigung_aus_anfrage(fremd, vertrag, ZUGANG)
    assert "Mende" not in str(fehler.value) and "Testmieter" not in str(fehler.value)
    assert not Kuendigung.objects.exists()
    fremd.refresh_from_db()
    assert fremd.status == "offen"

    kuendigung = services.kuendigung_aus_anfrage(
        fremd, vertrag, ZUGANG, absender=Kuendigung.Absender.VOLLMACHT
    )
    assert kuendigung.absender == "vollmacht"
    vid = Events.objects.tracks(kuendigung).order_by("-pgh_created_at")[0].pgh_context_id
    assert vorgaenge.titel_von(vid) == (
        f"Kündigung {vertrag.nummer} (Vollmacht liegt vor) aus Anfrage #{fremd.pk} erfasst"
    )


def test_passender_absender_braucht_keine_wahl(vertrag: Vertrag, angemeldet: Client) -> None:
    """Review 3, Punkt 9: Passt der Absender zum Mieter, gibt es keine Pflichtwahl."""
    anfrage = _anfrage(vertrag, "Erika Testmieter", "testmieter@testhof.invalid")
    seite = angemeldet.get(reverse("anbindungen:anfrage", args=[anfrage.pk])).content.decode()
    assert 'name="kuendigung-absender"' not in seite and "data-gesperrt-ohne" not in seite
    kuendigung = services.kuendigung_aus_anfrage(anfrage, vertrag, ZUGANG)
    assert kuendigung.absender == ""


def test_anfrageseite_knopf_gesperrt_bis_zur_wahl(
    vertrag: Vertrag, fremd: Buchungsanfrage, angemeldet: Client
) -> None:
    """Review 3, Punkt 9: Auf der Anfrageseite steht bei abweichendem Absender die
    Pflichtwahl; „Kündigung anlegen“ ist gesperrt, bis gewählt ist. Ohne Wahl kommt der
    Fehlersatz am Feld, und es entsteht keine Kündigung. Mit Wahl ist sie angelegt."""
    seite = angemeldet.get(reverse("anbindungen:anfrage", args=[fremd.pk])).content.decode()
    assert "Absender weicht ab" in seite and "Martine Mende" in seite
    assert 'name="kuendigung-absender" value="vollmacht"' in seite
    assert 'name="kuendigung-absender" value="vertragspartei"' in seite
    assert 'data-gesperrt-ohne="kuendigung-absender"' in seite

    ziel = reverse("stammdaten:vertrag", args=[vertrag.pk]) + f"?anfrage={fremd.pk}"
    antwort = angemeldet.post(ziel, _formular(fremd))
    assert antwort.status_code == 200
    form = antwort.context["offenes_formular"]
    assert form.errors["absender"] == [services.ABSENDER_UNGEKLAERT]
    assert 'id="id_kuendigung-absender-fehler"' in antwort.content.decode()
    assert not Kuendigung.objects.exists()

    angemeldet.post(ziel, _formular(fremd, absender="vertragspartei"))
    kuendigung = Kuendigung.objects.get()
    assert kuendigung.absender == Kuendigung.Absender.VERTRAGSPARTEI
    fremd.refresh_from_db()
    assert (fremd.status, fremd.kuendigung) == ("erledigt", kuendigung)


def test_kuendigungsdialog_der_vertragsseite(
    vertrag: Vertrag, fremd: Buchungsanfrage, angemeldet: Client
) -> None:
    """Review 3, Punkt 9: Der Kündigungsdialog der Vertragsseite mit ``?anfrage=`` hat
    dieselbe Pflichtwahl, gesperrten Knopf und Fehlersatz; mit Wahl ist die Kündigung
    angelegt."""
    url = reverse("stammdaten:vertrag", args=[vertrag.pk])
    seite = angemeldet.get(f"{url}?anfrage={fremd.pk}").content.decode()
    assert 'data-gesperrt-ohne="kuendigung-absender"' in seite
    dialog = angemeldet.get(f"{url}?dialog=kuendigung&anfrage={fremd.pk}", **DIALOG)
    html = dialog.content.decode()
    assert 'name="kuendigung-absender"' in html
    assert 'data-gesperrt-ohne="kuendigung-absender"' in html

    antwort = angemeldet.post(url, _formular(fremd), **DIALOG)
    assert services.ABSENDER_UNGEKLAERT in antwort.content.decode()
    assert not Kuendigung.objects.exists()

    antwort = angemeldet.post(url, _formular(fremd, absender="vollmacht"), **DIALOG)
    assert antwort.status_code == 200
    assert Kuendigung.objects.get().absender == Kuendigung.Absender.VOLLMACHT
