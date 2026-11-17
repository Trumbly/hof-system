"""Seiten des Kerns."""

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render


@login_required
def startseite(request: HttpRequest) -> HttpResponse:
    """Startseite. Wird in Schritt 9 zum Hof-Cockpit."""
    return render(request, "kern/startseite.html")
