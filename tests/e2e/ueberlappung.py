"""Prüfung auf überlappende Elemente in Formularen (Review 3, Punkt 2).

Bei Radio-Gruppen schoben sich Text, Fehlermeldung und das nächste Feld übereinander: Django
setzte die Klasse ``feld-haken`` (20 × 20 px) an den umschließenden ``div`` der Gruppe, die
Optionen quollen heraus. Das sieht nur, wer hinschaut; deshalb vergleicht diese Prüfung in
jedem sichtbaren ``form`` die Rechtecke von Label, Legende, Eingabe, Fehlertext, Hilfetext
und Knopf. Zwei davon dürfen sich nicht überschneiden, außer eines enthält das andere (das
Radio im eigenen Label).

Rechtecke werden auf ihre scrollenden Vorfahren zugeschnitten: Was im Dialog unter dem Rand
liegt, überlappt nicht mit dem Fuß des Dialogs.
"""

from __future__ import annotations

from playwright.sync_api import Page

#: Was in einem Formular geprüft wird.
ELEMENTE = (
    "label, legend, input:not([type=hidden]), select, textarea, button, .feld-fehler, .feld-hilfe"
)
#: Überschneidung in beiden Richtungen in px, ab der ein Paar als überlappend gilt
#: (Rundung, Fokusrahmen und Ränder von 1 px bleiben außen vor).
TOLERANZ_PX = 2

_SKRIPT = """([selektor, toleranz, behaelter]) => {
    const sichtbar = (e) => {
        if (!e.getClientRects().length) return false;
        // Inhalt eines zugeklappten <details> hat Rechtecke, ist aber nicht zu sehen
        if (e.checkVisibility && !e.checkVisibility({contentVisibilityAuto: true}))
            return false;
        const s = getComputedStyle(e);
        if (s.visibility === 'hidden' || parseFloat(s.opacity) === 0) return false;
        const r = e.getBoundingClientRect();
        return r.width > 1 && r.height > 1;
    };
    // Rechteck samt Inhalt (Text, der aus dem Element quillt, zählt mit), zugeschnitten auf
    // alle Vorfahren, die ihren Inhalt abschneiden
    const zugeschnitten = (e) => {
        const r = e.getBoundingClientRect();
        let [l, o, re, u] = [r.left, r.top, r.right, r.bottom];
        if (!['INPUT', 'SELECT', 'TEXTAREA'].includes(e.tagName)) {
            const bereich = document.createRange();
            bereich.selectNodeContents(e);
            const t = bereich.getBoundingClientRect();
            if (t.width > 0 && t.height > 0) {
                l = Math.min(l, t.left); o = Math.min(o, t.top);
                re = Math.max(re, t.right); u = Math.max(u, t.bottom);
            }
        }
        for (let v = e.parentElement; v && v !== document.body; v = v.parentElement) {
            const s = getComputedStyle(v);
            if (/(auto|scroll|hidden|clip)/.test(s.overflowX + s.overflowY)) {
                const b = v.getBoundingClientRect();
                l = Math.max(l, b.left); o = Math.max(o, b.top);
                re = Math.min(re, b.right); u = Math.min(u, b.bottom);
            }
        }
        return re - l > 1 && u - o > 1 ? {l, o, re, u} : null;
    };
    const name = (e) => {
        let n = e.tagName.toLowerCase();
        if (e.name) n += `[name=${e.name}]`;
        else if (e.htmlFor) n += `[for=${e.htmlFor}]`;
        else if (e.className && typeof e.className === 'string')
            n += '.' + e.className.trim().split(/\\s+/).join('.');
        const text = (e.innerText || e.value || '').trim().replace(/\\s+/g, ' ').slice(0, 30);
        return text ? `${n} „${text}“` : n;
    };
    const befunde = [];
    for (const form of document.querySelectorAll(behaelter)) {
        if (!sichtbar(form)) continue;
        const teile = [];
        for (const e of form.querySelectorAll(selektor)) {
            if (!sichtbar(e)) continue;
            const r = zugeschnitten(e);
            if (r) teile.push([e, r]);
        }
        for (let i = 0; i < teile.length; i++) {
            for (let j = i + 1; j < teile.length; j++) {
                const [a, ra] = teile[i];
                const [b, rb] = teile[j];
                if (a.contains(b) || b.contains(a)) continue;
                const x = Math.min(ra.re, rb.re) - Math.max(ra.l, rb.l);
                const y = Math.min(ra.u, rb.u) - Math.max(ra.o, rb.o);
                if (x > toleranz && y > toleranz) {
                    const ort = form.tagName.toLowerCase() + (form.id ? `#${form.id}`
                        : ` ${form.getAttribute('action') || ''}`);
                    befunde.push(`${ort}: ${name(a)} überlappt ${name(b)} `
                        + `(${Math.round(x)} × ${Math.round(y)} px)`);
                    if (befunde.length >= 8) return befunde;
                }
            }
        }
    }
    return befunde;
}"""


def ueberlappende_elemente(seite: Page, behaelter: str = "form") -> list[str]:
    """Paare sichtbarer Formular-Elemente, deren Rechtecke sich überschneiden (leer: gut).

    ``behaelter``: Selektor der Bereiche, innerhalb derer verglichen wird (Standard: jedes
    Formular; auf ``/stil/`` die Beispiele, die in keinem Formular stehen)."""
    return list(seite.evaluate(_SKRIPT, [ELEMENTE, TOLERANZ_PX, behaelter]))
