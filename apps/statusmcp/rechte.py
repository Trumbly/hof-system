"""Rolle ``hof_statusmcp``: darf nur die Sichten ``sm_*`` lesen (Schritt 11, Aufgabe 2).

Ohne Django-Importe, damit auch der Server (``apps.statusmcp.server``) den Namen kennt.

``RECHTE_SQL`` ist idempotent: Es legt die Rolle an, falls sie fehlt (NOLOGIN, ohne jedes
Sonderrecht), nimmt ihr alle Rechte an Tabellen, Sequenzen und künftigen Objekten im Schema
``public`` und gibt ihr ``SELECT`` auf jede Sicht ``sm_*``. Eine spätere Migration, die eine
Sicht neu anlegt (``DROP`` und ``CREATE`` verlieren die Rechte), führt ``RECHTE_SQL`` danach
noch einmal aus.

Die Sichten laufen mit den Rechten ihres Eigentümers (Standard in Postgres, kein
``security_invoker``): Der Leser braucht kein Recht an den Basistabellen.
"""

from __future__ import annotations

#: Name der Rolle ohne Login. Der Login-Benutzer (``statusmcp_benutzer``) ist ihr Mitglied.
ROLLE = "hof_statusmcp"

#: Präfix aller Sichten, die der Status-MCP lesen darf.
PRAEFIX = "sm_"

# Nur Konstanten aus diesem Modul im Text, keine Eingaben (darum noqa: S608).
RECHTE_SQL = f"""
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{ROLLE}') THEN
        CREATE ROLE {ROLLE} NOLOGIN;
    END IF;
END
$$;
ALTER ROLE {ROLLE} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {ROLLE};
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {ROLLE};
REVOKE CREATE ON SCHEMA public FROM {ROLLE};
GRANT USAGE ON SCHEMA public TO {ROLLE};
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM {ROLLE};
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM {ROLLE};
DO $$
DECLARE
    sicht record;
BEGIN
    FOR sicht IN
        SELECT c.relname
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'v'
            AND left(c.relname, {len(PRAEFIX)}) = '{PRAEFIX}'
    LOOP
        EXECUTE format('GRANT SELECT ON public.%I TO {ROLLE}', sicht.relname);
    END LOOP;
END
$$;
"""  # noqa: S608
