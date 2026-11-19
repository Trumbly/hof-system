/* Hof-System: kleines Verhalten der Bausteine (Review 2, C3 und C4). Ohne Framework.
 *
 * - Dialog: HTMX lädt den Inhalt nach #dialog-inhalt, dann öffnet sich #dialog. Nach dem
 *   Speichern schickt der Server das Ereignis „dialogSchliessen“ (apps.kern.htmx).
 * - [data-dialog-oeffnen="id"] öffnet einen Dialog, [data-dialog-schliessen] schließt ihn.
 * - Toast-Meldungen verschwinden nach 8 Sekunden (Fehler bleiben, bis man sie schließt).
 * - Dateiknöpfe zeigen eine Vorschau der gewählten Bilder und die Dateinamen. Ein
 *   Dateifeld ohne eigene Knöpfe (input[type=file] direkt im Markup) bekommt sie beim Laden:
 *   In keiner Seite steht „Choose file“ (Review 2, C4).
 * - Suche in der Kopfzeile (Review 2, B1): Strg+K bzw. ⌘K setzt den Cursor ins Suchfeld,
 *   Pfeiltasten wählen einen Treffer, Enter öffnet ihn (ohne Wahl den ersten), Escape
 *   schließt die Treffer.
 */
(function () {
  "use strict";

  function dialog() {
    return document.getElementById("dialog");
  }

  function oeffnen(d) {
    if (d && !d.open) {
      d.showModal();
    }
  }

  function erstesFeld(d) {
    var feld = d.querySelector("[autofocus], .dialog-rumpf input:not([type=hidden]), .dialog-rumpf select, .dialog-rumpf textarea");
    if (feld) {
      feld.focus();
    }
  }

  document.addEventListener("htmx:afterSwap", function (e) {
    var ziel = e.detail && e.detail.target;
    if (ziel && ziel.id === "dialog-inhalt" && ziel.children.length) {
      var d = dialog();
      oeffnen(d);
      erstesFeld(d);
    }
  });

  document.addEventListener("dialogSchliessen", function () {
    var d = dialog();
    if (d && d.open) {
      d.close();
    }
  });

  document.addEventListener("close", function (e) {
    if (e.target && e.target.id === "dialog") {
      document.getElementById("dialog-inhalt").innerHTML = "";
    }
  }, true);

  document.addEventListener("click", function (e) {
    var auf = e.target.closest("[data-dialog-oeffnen]");
    if (auf) {
      oeffnen(document.getElementById(auf.getAttribute("data-dialog-oeffnen")));
      return;
    }
    var zu = e.target.closest("[data-dialog-schliessen]");
    if (zu) {
      var d = zu.closest("dialog");
      if (d) {
        d.close();
      }
      return;
    }
    var toastZu = e.target.closest("[data-toast-schliessen]");
    if (toastZu) {
      toastZu.closest(".toast").remove();
      return;
    }
    // Klick auf den Schleier neben dem Dialog schließt ihn.
    if (e.target.tagName === "DIALOG" && e.target.open) {
      var r = e.target.getBoundingClientRect();
      if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) {
        e.target.close();
      }
    }
  });

  // Menüs („mehr ▾“): ein offenes Menü schließt sich beim Klick daneben und mit Escape.
  document.addEventListener("click", function (e) {
    document.querySelectorAll("details.menue[open]").forEach(function (m) {
      if (!m.contains(e.target)) {
        m.removeAttribute("open");
      }
    });
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      document.querySelectorAll("details.menue[open]").forEach(function (m) {
        m.removeAttribute("open");
      });
    }
  });

  function toastsPflegen() {
    document.querySelectorAll(".toast:not([data-gepflegt])").forEach(function (t) {
      t.setAttribute("data-gepflegt", "1");
      if (!t.classList.contains("toast-fehler")) {
        window.setTimeout(function () {
          t.classList.add("toast-weg");
          window.setTimeout(function () { t.remove(); }, 300);
        }, 8000);
      }
    });
  }
  document.addEventListener("DOMContentLoaded", toastsPflegen);
  document.addEventListener("htmx:afterSettle", toastsPflegen);

  // Dateiknöpfe: Vorschau der Bilder, sonst der Dateiname.
  document.addEventListener("change", function (e) {
    var eingabe = e.target;
    if (!eingabe.matches || !eingabe.matches("input[type=file][data-vorschau]")) {
      return;
    }
    var liste = document.getElementById(eingabe.getAttribute("data-vorschau"));
    if (!liste) {
      return;
    }
    // Die andere Eingabe desselben Felds leeren: gesendet wird nur die zuletzt gewählte.
    var gruppe = eingabe.closest(".datei");
    if (gruppe) {
      gruppe.querySelectorAll("input[type=file]").forEach(function (andere) {
        if (andere !== eingabe) {
          andere.value = "";
        }
      });
    }
    liste.innerHTML = "";
    var dateien = Array.prototype.slice.call(eingabe.files || []);
    if (!dateien.length) {
      var leer = document.createElement("li");
      leer.className = "datei-leer";
      leer.textContent = liste.getAttribute("data-leer") || "";
      liste.appendChild(leer);
      return;
    }
    // „sofort“ (Baustein dateiknoepfe): Die Wahl sendet das Formular gleich ab.
    if (eingabe.hasAttribute("data-sofort-senden") && eingabe.form) {
      eingabe.form.classList.add("sendet");
      if (eingabe.form.requestSubmit) {
        eingabe.form.requestSubmit();
      } else {
        eingabe.form.submit();
      }
    }
    dateien.forEach(function (datei) {
      var li = document.createElement("li");
      if (datei.type && datei.type.indexOf("image/") === 0) {
        var bild = document.createElement("img");
        bild.alt = "";
        bild.src = URL.createObjectURL(datei);
        bild.onload = function () { URL.revokeObjectURL(bild.src); };
        li.appendChild(bild);
      }
      var name = document.createElement("span");
      name.textContent = datei.name;
      li.appendChild(name);
      liste.appendChild(li);
    });
  });

  // --- Eigene Dateiknöpfe für jedes Dateifeld (Review 2, C4) -------------------------
  // Formulare bekommen sie schon vom Server (templates/django/forms/widgets/); hier die
  // Felder, die eine Seite selbst schreibt. Bilder: „Foto aufnehmen“ (Kamera) und „Datei
  // wählen“; sonst nur „Datei wählen“.
  var zaehlerDatei = 0;

  function knopf(text, eingabe, symbolPfad) {
    var label = document.createElement("label");
    label.className = "knopf knopf-zweit datei-knopf";
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("class", "symbol");
    svg.innerHTML = symbolPfad;
    label.appendChild(eingabe);
    label.appendChild(svg);
    label.appendChild(document.createTextNode(text));
    return label;
  }

  function dateiknoepfeAufbauen(eingabe) {
    if (eingabe.classList.contains("datei-eingabe") || eingabe.closest(".datei")) {
      return;
    }
    zaehlerDatei += 1;
    var id = (eingabe.id || "datei-" + zaehlerDatei) + "-vorschau";
    var bild = /image/.test(eingabe.getAttribute("accept") || "") || eingabe.hasAttribute("capture");
    var gruppe = document.createElement("span");
    gruppe.className = "datei datei-auto";
    var knoepfe = document.createElement("span");
    knoepfe.className = "datei-knoepfe";
    knoepfe.setAttribute("role", "group");
    eingabe.parentNode.insertBefore(gruppe, eingabe);
    if (bild) {
      var kamera = eingabe.cloneNode(false);
      kamera.removeAttribute("id");
      kamera.removeAttribute("required");
      kamera.setAttribute("accept", "image/*");
      kamera.setAttribute("capture", "environment");
      kamera.className = "datei-eingabe";
      kamera.setAttribute("data-vorschau", id);
      knoepfe.appendChild(knopf("Foto aufnehmen", kamera, '<path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/>'));
      eingabe.removeAttribute("capture");
    }
    eingabe.removeAttribute("required");
    eingabe.classList.add("datei-eingabe");
    eingabe.setAttribute("data-vorschau", id);
    knoepfe.appendChild(knopf("Datei wählen", eingabe, '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/>'));
    var vorschau = document.createElement("ul");
    vorschau.className = "datei-vorschau";
    vorschau.id = id;
    vorschau.setAttribute("aria-live", "polite");
    vorschau.setAttribute("data-leer", "Noch nichts gewählt.");
    vorschau.innerHTML = '<li class="datei-leer">Noch nichts gewählt.</li>';
    gruppe.appendChild(knoepfe);
    gruppe.appendChild(vorschau);
  }

  function dateifelderPflegen(wurzel) {
    (wurzel || document).querySelectorAll("input[type=file]").forEach(dateiknoepfeAufbauen);
  }
  document.addEventListener("DOMContentLoaded", function () { dateifelderPflegen(document); });
  document.addEventListener("htmx:afterSettle", function (e) { dateifelderPflegen(e.target); });

  // --- Suche in der Kopfzeile (Review 2, B1) -------------------------------------------
  function sichtbar(element) {
    return element && element.offsetParent !== null;
  }

  function sucheZu(form) {
    var liste = form.querySelector(".suche-treffer");
    var feld = form.querySelector(".suche-eingabe");
    if (liste) {
      liste.hidden = true;
    }
    if (feld) {
      feld.setAttribute("aria-expanded", "false");
      feld.removeAttribute("aria-activedescendant");
    }
  }

  function eintraege(form) {
    return Array.prototype.slice.call(form.querySelectorAll(".suche-treffer a.suche-eintrag"));
  }

  function waehlen(form, index) {
    var alle = eintraege(form);
    var feld = form.querySelector(".suche-eingabe");
    alle.forEach(function (a, i) {
      a.setAttribute("aria-selected", i === index ? "true" : "false");
      if (!a.id) {
        a.id = feld.id + "-treffer-" + i;
      }
    });
    if (alle[index]) {
      feld.setAttribute("aria-activedescendant", alle[index].id);
      alle[index].scrollIntoView({ block: "nearest" });
    } else {
      feld.removeAttribute("aria-activedescendant");
    }
  }

  function gewaehlt(form) {
    var alle = eintraege(form);
    for (var i = 0; i < alle.length; i += 1) {
      if (alle[i].getAttribute("aria-selected") === "true") {
        return i;
      }
    }
    return -1;
  }

  function oeffnenTreffer(form) {
    var alle = eintraege(form);
    var i = gewaehlt(form);
    var ziel = alle[i >= 0 ? i : 0];
    if (ziel) {
      window.location.assign(ziel.href);
      return true;
    }
    return false;
  }

  document.addEventListener("htmx:afterSwap", function (e) {
    var liste = e.detail && e.detail.target;
    if (!liste || !liste.classList || !liste.classList.contains("suche-treffer")) {
      return;
    }
    var form = liste.closest("form.suche");
    var feld = form.querySelector(".suche-eingabe");
    form.removeAttribute("data-unterwegs");
    var leer = !liste.textContent.trim() || !feld.value.trim();
    liste.hidden = leer;
    feld.setAttribute("aria-expanded", leer ? "false" : "true");
    waehlen(form, 0);
    if (form.getAttribute("data-enter-wartet")) {
      form.removeAttribute("data-enter-wartet");
      if (!oeffnenTreffer(form)) {
        form.submit();
      }
    }
  });

  document.addEventListener("keydown", function (e) {
    // Strg+K / ⌘K: zur Suche (das sichtbare Feld: Seitenleiste oder Kopfzeile)
    if ((e.ctrlKey || e.metaKey) && !e.altKey && (e.key === "k" || e.key === "K")) {
      var felder = Array.prototype.slice.call(document.querySelectorAll(".suche-eingabe"));
      var feld = felder.filter(sichtbar)[0];
      if (feld) {
        e.preventDefault();
        feld.focus();
        feld.select();
      }
      return;
    }
    var eingabe = e.target;
    if (!eingabe.classList || !eingabe.classList.contains("suche-eingabe")) {
      return;
    }
    var form = eingabe.closest("form.suche");
    var liste = form.querySelector(".suche-treffer");
    var anzahl = eintraege(form).length;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (!anzahl) {
        return;
      }
      e.preventDefault();
      liste.hidden = false;
      eingabe.setAttribute("aria-expanded", "true");
      var i = gewaehlt(form);
      i = e.key === "ArrowDown" ? (i + 1) % anzahl : (i - 1 + anzahl) % anzahl;
      waehlen(form, i);
    } else if (e.key === "Enter") {
      if (form.hasAttribute("data-unterwegs")) {
        // Die Treffer kommen gleich: dann den ersten öffnen.
        e.preventDefault();
        form.setAttribute("data-enter-wartet", "1");
        // Kommt nichts (Wert unverändert, Netz weg), geht es nach kurzer Zeit trotzdem weiter.
        window.setTimeout(function () {
          if (form.getAttribute("data-enter-wartet")) {
            form.removeAttribute("data-enter-wartet");
            form.removeAttribute("data-unterwegs");
            if (!oeffnenTreffer(form)) {
              form.submit();
            }
          }
        }, 1500);
        return;
      }
      if (!liste.hidden && oeffnenTreffer(form)) {
        e.preventDefault();
      }
      // sonst: das Formular geht zur Seite „Suche“
    } else if (e.key === "Escape") {
      if (!liste.hidden) {
        e.preventDefault();
        sucheZu(form);
      } else {
        eingabe.blur();
      }
    }
  });

  // Getippt, Treffer noch nicht da (Verzögerung oder Anfrage läuft): Enter wartet auf sie.
  document.addEventListener("input", function (e) {
    var feld = e.target;
    if (feld.classList && feld.classList.contains("suche-eingabe")) {
      feld.closest("form.suche").setAttribute("data-unterwegs", "1");
    }
  });
  document.addEventListener("htmx:responseError", function (e) {
    var form = e.target.closest && e.target.closest("form.suche");
    if (form) {
      form.removeAttribute("data-unterwegs");
      form.removeAttribute("data-enter-wartet");
    }
  });

  // Mit der Maus über einen Treffer: er ist gewählt. Klick daneben schließt die Treffer.
  document.addEventListener("mouseover", function (e) {
    var a = e.target.closest && e.target.closest(".suche-treffer a.suche-eintrag");
    var form = a && a.closest("form.suche");
    if (form) {
      waehlen(form, eintraege(form).indexOf(a));
    }
  });
  document.addEventListener("click", function (e) {
    document.querySelectorAll("form.suche").forEach(function (form) {
      if (!form.contains(e.target)) {
        sucheZu(form);
      }
    });
  });
  document.addEventListener("focusin", function (e) {
    var feld = e.target;
    if (feld.classList && feld.classList.contains("suche-eingabe")) {
      var form = feld.closest("form.suche");
      var liste = form.querySelector(".suche-treffer");
      if (feld.value.trim() && eintraege(form).length) {
        liste.hidden = false;
        feld.setAttribute("aria-expanded", "true");
      }
    }
  });

  // Auf dem Mac heißt die Taste ⌘ statt Strg.
  document.addEventListener("DOMContentLoaded", function () {
    if (/Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent)) {
      document.querySelectorAll(".suche-taste").forEach(function (k) { k.textContent = "⌘K"; });
    }
  });
})();
