"""Fabriken für erfundene Testdaten (factory_boy, Faker de_DE).

Sie schreiben über die Services, nicht direkt in die Modelle (CLAUDE.md, Regel 4.1).
E-Mail-Adressen enden immer auf ``.invalid``.
"""

from __future__ import annotations

from typing import Any

import factory
from faker import Faker

from apps.kern import services
from apps.kern.models import Benutzer, Mandant, Objekt

fake = Faker("de_DE")

TESTPASSWORT = "Testhof-Passwort-nur-fuer-Tests-7"


class _ServiceFabrik(factory.Factory):  # type: ignore[misc]
    class Meta:
        abstract = True


class BenutzerFabrik(_ServiceFabrik):
    class Meta:
        model = Benutzer

    email = factory.Sequence(lambda n: f"benutzer{n}@testhof.invalid")
    passwort = TESTPASSWORT

    @classmethod
    def _create(cls, model_class: type[Benutzer], **kwargs: Any) -> Benutzer:
        return services.benutzer_anlegen(kwargs["email"], kwargs["passwort"])


class MandantFabrik(_ServiceFabrik):
    class Meta:
        model = Mandant

    art = Mandant.Art.GBR
    kuerzel = factory.Sequence(lambda n: f"M{n}")
    bezeichnung = factory.LazyFunction(lambda: f"GbR {fake.last_name()}")

    @classmethod
    def _create(cls, model_class: type[Mandant], **kwargs: Any) -> Mandant:
        return services.mandant_anlegen(**kwargs)


class ObjektFabrik(_ServiceFabrik):
    class Meta:
        model = Objekt

    mandant = factory.SubFactory(MandantFabrik)
    kuerzel = factory.Sequence(lambda n: f"OBJ{n}")
    bezeichnung = factory.LazyFunction(lambda: f"Halle {fake.city()}")
    anschrift = factory.LazyFunction(lambda: fake.address())

    @classmethod
    def _create(cls, model_class: type[Objekt], **kwargs: Any) -> Objekt:
        return services.objekt_anlegen(**kwargs)
