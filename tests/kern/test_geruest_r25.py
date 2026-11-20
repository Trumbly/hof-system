"""Grundgerüst für alle Seiten (Review 2, C1, C4; Review 1, Punkt 13) und Konto (B15).

R2-5: base.html ist das Gerüst, base_neu.html ein Alias. Seit PR G (Apple-Design) tragen alle
Seiten einen Seitenkopf, hof.css ist weg. Jedes Dateifeld hat eigene Knöpfe.
"""

from __future__ import annotations

from pathlib import Path

from allauth.mfa.recovery_codes.internal.auth import RecoveryCodes
from django import forms
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, RequestFactory
from django.urls import reverse

from apps.kern import navigation
from apps.kern.models import Benutzer

WURZEL = Path(__file__).resolve().parents[2]


# --- Gerüst --------------------------------------------------------------------------


def test_alte_seite_im_neuen_geruest(angemeldet: Client) -> None:
    """Review 2, C1: Jede Seite steht im Gerüst (Seitenleiste, Leiste unten, Dialog, Toasts);
    die alte Kopfnavigation mit Scrollleiste ist weg."""
    html = angemeldet.get(reverse("stammdaten:einheiten")).content.decode()
    assert '<body class="neu"' in html
    assert 'class="leiste"' in html and 'class="unten"' in html
    assert 'id="dialog"' in html and 'id="toasts"' in html
    assert 'class="nav-schalter"' not in html and 'class="nav"' not in html
    assert 'class="inhalt-neu"' in html
    for css in ("werte.css", "bausteine.css", "apple/basis.css"):
        assert f"css/{css}" in html
    assert "css/hof.css" not in html
    assert "js/bausteine.js" in html


def test_base_neu_ist_alias(angemeldet: Client) -> None:
    """Review 2, C1: base_neu.html erweitert nur base.html (Seiten der anderen PRs laufen
    weiter)."""
    vorlage = (WURZEL / "templates" / "base_neu.html").read_text()
    assert vorlage.startswith('{% extends "base.html" %}')
    assert "{% block" not in vorlage
    html = angemeldet.get(reverse("kern:stil")).content.decode()
    assert 'class="leiste"' in html and 'class="seitenkopf"' in html


def test_kein_altes_stylesheet_und_klammern_stimmen() -> None:
    """Review 2, C1 und Apple-Design (PR G): hof.css (alte Seiten in der Ebene „alt“) gibt es
    nicht mehr, alle Seiten tragen einen Seitenkopf. In jeder Datei stimmen die Klammern
    (früher schluckten drei fehlende Klammern ganze Abschnitte)."""
    assert not (WURZEL / "static" / "css" / "hof.css").exists()
    for pfad in (WURZEL / "static" / "css").rglob("*.css"):
        css = pfad.read_text()
        assert css.count("{") == css.count("}"), pfad.name
        assert "@layer" not in css, pfad.name
        for weg in (".nav-schalter", ".nav-knopf", "\n.kopf {", "\n.inhalt {"):
            assert weg not in css, (pfad.name, weg)


def test_navigation_system_mit_konto_und_verwaltung_als_admin(db: None) -> None:
    """Review 2, B15 und Review 1, Punkt 13: Konto und Verwaltung stehen unter System; die
    Verwaltung trägt die Marke „Admin“ und erscheint nur für die Verwaltung."""
    anfrage = RequestFactory().get("/")
    anfrage.user = Benutzer(is_staff=True)
    anfrage.resolver_match = None  # type: ignore[assignment]
    gruppen = dict(navigation.gruppen(anfrage))
    system = {link.titel: link for link in gruppen["System"]}
    assert system["Konto"].url == reverse("kern:konto")
    assert system["Verwaltung"].marke == "Admin"
    assert "Übergaben" in [link.titel for link in gruppen["Verträge"]]
    anfrage.user = Benutzer(is_staff=False)
    assert "Verwaltung" not in [link.titel for link in dict(navigation.gruppen(anfrage))["System"]]


def test_mehr_oeffnet_alle_gruppen_aufklappbar(angemeldet: Client) -> None:
    """Review 1, Punkt 13: „Mehr“ am Handy zeigt alle Gruppen als Aufklapper."""
    html = angemeldet.get(reverse("kern:startseite")).content.decode()
    mehr = html.split('id="mehr"', 1)[1].split("</dialog>", 1)[0]
    gruppen = [g.split("</summary>", 1)[0] for g in mehr.split("<summary>")[1:]]
    assert gruppen == ["Heute", "Verträge", "Geld", "Hof", "System"]
    assert 'data-dialog-oeffnen="mehr"' in html


def test_ziele_44_px_global() -> None:
    """Review 2, C4: Knöpfe und Haken mindestens 44 px am Handy, auch im alten Markup
    (die Oberflächentests messen es auf jeder Seite nach)."""
    css = (WURZEL / "static" / "css" / "bausteine.css").read_text()
    teil = css.split("/* --- Ziele mindestens 44 px", 1)[1]
    assert "@media (max-width: 1023px), (pointer: coarse)" in teil
    assert ".neu button," in teil and "min-height: var(--ziel)" in teil
    assert '.neu label:has(> input[type="checkbox"])' in teil


# --- Dateiknöpfe (C4) ----------------------------------------------------------------


class _Mehrere(forms.ClearableFileInput):
    allow_multiple_selected = True


class _DateiForm(forms.Form):
    foto = forms.FileField(
        widget=forms.ClearableFileInput(attrs={"accept": "image/*", "capture": "environment"})
    )
    scan = forms.FileField(required=False)
    fotos = forms.FileField(required=False, widget=_Mehrere(attrs={"accept": "image/*"}))


def test_jedes_dateifeld_hat_eigene_knoepfe() -> None:
    """Review 2, C4: Jedes Dateifeld eines Formulars (FileInput, ClearableFileInput) wird zu
    den eigenen Knöpfen; kein Feld zeigt „Choose file“. Bilder: „Foto aufnehmen“ mit
    capture=environment."""
    form = _DateiForm()
    foto = str(form["foto"])
    assert "Foto aufnehmen" in foto and "Datei wählen" in foto
    assert 'capture="environment"' in foto
    assert foto.count('class="datei-eingabe"') == 2
    assert 'id="id_foto"' in foto
    assert "required" not in foto  # zwei Eingaben für ein Feld: geprüft wird am Server
    scan = str(form["scan"])
    assert "Foto aufnehmen" not in scan and "capture" not in scan and "Datei wählen" in scan
    assert str(form["fotos"]).count(" multiple") == 2
    for html in (foto, scan):
        assert 'type="file"' in html
        assert '<input type="file" name' not in html  # kein nacktes Feld


def test_dateifeld_mit_gespeicherter_datei() -> None:
    """Review 2, C4: Ein Feld mit gespeicherter Datei zeigt sie auf Deutsch und darunter die
    Knöpfe (statt „Currently … Change“)."""

    class Datei:
        url = "/medien/scan.pdf"

        def __str__(self) -> str:
            return "scan.pdf"

    form = _DateiForm(initial={"scan": Datei()})
    html = str(form["scan"])
    assert "Gespeichert:" in html and "entfernen" in html and "Datei wählen" in html
    assert "Currently" not in html and "Change" not in html


def test_dateifeld_gilt_auch_im_feld_baustein() -> None:
    """Review 2, C4: {% feld %} und {% dateiknoepfe %} geben dieselben Knöpfe aus."""
    from django.template import Context, Template

    form = _DateiForm()
    html = Template("{% load bausteine %}{% feld form.scan %}{% dateiknoepfe form.foto %}").render(
        Context({"form": form})
    )
    assert html.count("Datei wählen") == 2
    assert "Foto aufnehmen" in html


def test_dateifeld_nimmt_datei_an() -> None:
    """Review 2, C4: Die eigenen Knöpfe senden unter dem Namen des Felds."""
    datei = SimpleUploadedFile("a.png", b"x", content_type="image/png")
    form = _DateiForm(data={}, files={"foto": datei})
    assert form.is_valid(), form.errors


# --- Konto (B15) ---------------------------------------------------------------------


def test_konto_zeigt_email_und_zweiten_faktor(
    angemeldet: Client, benutzer_mit_totp: Benutzer
) -> None:
    """Review 2, B15: Die Kontoseite zeigt E-Mail, TOTP an/aus und die
    Wiederherstellungscodes (keine → erzeugen; vorhanden → anzeigen)."""
    html = angemeldet.get(reverse("kern:konto")).content.decode()
    assert benutzer_mit_totp.email in html
    totp = html.split('id="konto-totp"', 1)[1].split("</li>", 1)[0]
    assert ">an<" in totp
    codes = html.split('id="konto-codes"', 1)[1].split("</li>", 1)[0]
    assert ">keine<" in codes and reverse("mfa_generate_recovery_codes") in codes

    RecoveryCodes.activate(benutzer_mit_totp)
    html = angemeldet.get(reverse("kern:konto")).content.decode()
    codes = html.split('id="konto-codes"', 1)[1].split("</li>", 1)[0]
    assert ">vorhanden<" in codes and "10 von 10 unbenutzt" in codes
    assert reverse("mfa_view_recovery_codes") in codes
    # Konto ist in der Navigation markiert, auch auf den Seiten von allauth
    leiste = html.split('class="leiste"', 1)[1].split("</aside>", 1)[0]
    assert f'href="{reverse("kern:konto")}" aria-current="page"' in leiste
    assert navigation._konto("mfa_view_recovery_codes", "")
    assert navigation._konto("account_change_password", "")
    assert not navigation._konto("account_logout", "")


def test_konto_ohne_totp_bietet_einrichtung(client: Client, benutzer: Benutzer) -> None:
    """Review 2, B15: Ohne TOTP zeigt die Seite „aus“ (die Middleware lässt dann ohnehin nur
    die Einrichtung zu; geprüft an der Funktion)."""
    from apps.kern.suchseite import zweiter_faktor

    status = zweiter_faktor(benutzer)
    assert status["totp"] is False and status["codes"] is False
