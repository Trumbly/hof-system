/* Hof-System: Tastatur und Auswahl im Posteingang der Arbeitslisten (Review 2, B2).
 *
 * - j / k: nächster / voriger Posten (die Auswahl ist .posten-aktiv)
 * - Enter: übernimmt den Vorschlag ([data-uebernehmen] der Zeile), sonst wie e
 * - e: öffnet die Bearbeitung ([data-bearbeiten]: Dialog per HTMX oder Seite)
 * - x: wählt den Posten für die Mehrfachauswahl
 * Nach einer Aktion verschwindet die Zeile; die Auswahl springt zum nächsten Posten.
 * In Feldern, Auswahllisten und Dialogen gelten die Tasten nicht.
 */
(function () {
  "use strict";

  var aktivIndex = 0;

  function zeilen() {
    return Array.prototype.slice.call(document.querySelectorAll("#posteingang [data-posten]"));
  }

  function waehlen(index, fokus) {
    var alle = zeilen();
    if (!alle.length) {
      return;
    }
    aktivIndex = Math.max(0, Math.min(index, alle.length - 1));
    alle.forEach(function (z, i) {
      z.classList.toggle("posten-aktiv", i === aktivIndex);
      z.setAttribute("aria-current", i === aktivIndex ? "true" : "false");
    });
    var zeile = alle[aktivIndex];
    if (fokus) {
      zeile.focus({ preventScroll: true });
      zeile.scrollIntoView({ block: "nearest" });
    }
  }

  function aktiveZeile() {
    return zeilen()[aktivIndex] || null;
  }

  function imFeld(ziel) {
    if (!ziel || !ziel.closest) {
      return false;
    }
    return Boolean(
      ziel.closest("input, select, textarea, [contenteditable], dialog[open], details.menue[open]")
    );
  }

  function bearbeiten(zeile) {
    var link = zeile.querySelector("[data-bearbeiten]");
    if (link) {
      link.click();
    }
  }

  document.addEventListener("keydown", function (e) {
    if (e.altKey || e.ctrlKey || e.metaKey || imFeld(e.target) || !document.getElementById("posteingang")) {
      return;
    }
    var zeile = aktiveZeile();
    if (e.key === "j") {
      e.preventDefault();
      waehlen(aktivIndex + 1, true);
    } else if (e.key === "k") {
      e.preventDefault();
      waehlen(aktivIndex - 1, true);
    } else if (e.key === "Enter" && zeile) {
      if (e.target.closest && e.target.closest("a, button") && e.target !== zeile) {
        return; // Enter auf einem Link oder Knopf tut, was der Link tut
      }
      e.preventDefault();
      var knopf = zeile.querySelector("[data-uebernehmen]");
      if (knopf) {
        knopf.click();
      } else {
        bearbeiten(zeile);
      }
    } else if (e.key === "e" && zeile) {
      e.preventDefault();
      bearbeiten(zeile);
    } else if (e.key === "x" && zeile) {
      e.preventDefault();
      var haken = zeile.querySelector("input[name=posten]");
      if (haken) {
        haken.checked = !haken.checked;
      }
    }
  });

  // Klick in eine Zeile wählt sie (ohne den Klick auf Knöpfe und Felder zu stören).
  document.addEventListener("click", function (e) {
    var zeile = e.target.closest && e.target.closest("#posteingang [data-posten]");
    if (zeile) {
      waehlen(zeilen().indexOf(zeile), false);
    }
    var alle = e.target.closest && e.target.closest("[data-alle-waehlen]");
    if (alle) {
      document.querySelectorAll("#posteingang input[name=posten]").forEach(function (h) {
        h.checked = alle.checked;
      });
    }
  });

  // Vor einer Anfrage aus einer Zeile: deren Stelle merken.
  document.addEventListener("htmx:beforeRequest", function (e) {
    var zeile = e.target.closest && e.target.closest("[data-posten]");
    if (zeile) {
      var i = zeilen().indexOf(zeile);
      if (i >= 0) {
        aktivIndex = i;
      }
    }
  });

  // Nach jeder Antwort: Ist die gewählte Zeile weg, rückt der nächste Posten an ihre Stelle.
  document.addEventListener("htmx:afterSettle", function () {
    // Ging der Fokus mit der Zeile verloren, bekommt ihn der nächste Posten.
    var verloren = !document.activeElement || document.activeElement === document.body;
    waehlen(aktivIndex, verloren && !document.querySelector("dialog[open]"));
  });

  document.addEventListener("DOMContentLoaded", function () {
    waehlen(0, false);
  });
})();
