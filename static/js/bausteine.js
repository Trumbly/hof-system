/* Hof-System: kleines Verhalten der Bausteine (Review 2, C3 und C4). Ohne Framework.
 *
 * - Dialog: HTMX lädt den Inhalt nach #dialog-inhalt, dann öffnet sich #dialog. Nach dem
 *   Speichern schickt der Server das Ereignis „dialogSchliessen“ (apps.kern.htmx).
 * - [data-dialog-oeffnen="id"] öffnet einen Dialog, [data-dialog-schliessen] schließt ihn.
 * - Toast-Meldungen verschwinden nach 8 Sekunden (Fehler bleiben, bis man sie schließt).
 * - Dateiknöpfe zeigen eine Vorschau der gewählten Bilder und die Dateinamen.
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

  // Bedingte Teile (Review 2, B6 und B8): [data-wenn="feld"] erscheint nur, wenn das Feld
  // desselben Formulars passt. Der Server setzt den Anfangszustand (hidden) selbst.
  //   data-gleich="wert"  Auswahl oder Radio hat diesen Wert; Checkbox „an“ oder „aus“
  //   data-ab="100"       Zahl im Feld (Komma oder Punkt) ist mindestens so groß
  function feldWert(form, name) {
    var felder = form.querySelectorAll("[name='" + name + "']");
    var wert = "";
    Array.prototype.forEach.call(felder, function (f) {
      if (f.type === "checkbox") {
        wert = f.checked ? "an" : "aus";
      } else if (f.type === "radio") {
        if (f.checked) {
          wert = f.value;
        }
      } else {
        wert = f.value;
      }
    });
    return wert;
  }

  function zahlLesen(text) {
    text = String(text || "").trim();
    if (text.indexOf(",") >= 0) {
      text = text.replace(/\./g, "").replace(",", ".");
    }
    return parseFloat(text);
  }

  function bedingtePruefen() {
    document.querySelectorAll("[data-wenn]").forEach(function (teil) {
      var form = teil.closest("form") || document;
      var wert = feldWert(form, teil.getAttribute("data-wenn"));
      var zeigen = true;
      if (teil.hasAttribute("data-gleich")) {
        zeigen = wert === teil.getAttribute("data-gleich");
      }
      if (teil.hasAttribute("data-ab")) {
        var zahl = zahlLesen(wert);
        zeigen = !isNaN(zahl) && zahl >= parseFloat(teil.getAttribute("data-ab"));
      }
      teil.hidden = !zeigen;
    });
  }
  document.addEventListener("change", bedingtePruefen);
  document.addEventListener("input", bedingtePruefen);
  document.addEventListener("htmx:afterSettle", bedingtePruefen);

  // Sprungmarke öffnet den Dialog (Review 2, B7): Zeigt die Adresse auf eine Karte
  // (#stammfelder, etwa aus dem Datenstand) und hat die Karte einen Link [data-anker-dialog],
  // öffnet sich dessen Dialog. Ohne HTMX führt der Link zur Seite mit offenem Formular.
  document.addEventListener("DOMContentLoaded", function () {
    var ziel = window.location.hash && document.getElementById(window.location.hash.slice(1));
    var link = ziel && ziel.querySelector("[data-anker-dialog]");
    if (link) {
      window.setTimeout(function () { link.click(); }, 0);
    }
  });

  // Vorbelegen aus der gewählten Option (Review 2, B6): <option data-werte='{"miete": "55,00"}'>
  // setzt beim Wechsel die Felder gleichen Namens im Formular, z. B. die Zielmiete der Einheit.
  document.addEventListener("change", function (e) {
    var auswahl = e.target;
    if (!auswahl.matches || !auswahl.matches("select")) {
      return;
    }
    var option = auswahl.options[auswahl.selectedIndex];
    var roh = option && option.getAttribute("data-werte");
    var form = auswahl.closest("form");
    if (!roh || !form) {
      return;
    }
    var werte = JSON.parse(roh);
    Object.keys(werte).forEach(function (name) {
      var feld = form.querySelector("[name='" + name + "']");
      if (feld) {
        feld.value = werte[name];
        feld.dispatchEvent(new Event("input", { bubbles: true }));
      }
    });
  });
})();
