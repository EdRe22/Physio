"""Datenbank-seitiger Schutz: Der Audit-Log lässt sich nicht ändern oder löschen, auch nicht per SQL.

Ergänzt die Python-Sperre (AppendOnlyError) und die Hash-Kette. Wer Schema-Rechte hat, kann die Trigger
weiterhin entfernen; deshalb läuft die App in Produktion mit einem DB-Benutzer ohne Eigentümerrechte
(siehe DEPLOYMENT.md) und die Kette wird regelmäßig mit `manage.py verify_audit` geprüft.
PostgreSQL-Variante: bisher nur geschrieben, nicht gegen eine echte PostgreSQL-Instanz getestet.
"""
from django.db import migrations

SQLITE_UP = [
    "CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_auditentry "
    "BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END",
    "CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_auditentry "
    "BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END",
]
SQLITE_DOWN = ["DROP TRIGGER IF EXISTS audit_no_update", "DROP TRIGGER IF EXISTS audit_no_delete"]

POSTGRES_UP = [
    "CREATE OR REPLACE FUNCTION audit_append_only() RETURNS trigger AS $$ "
    "BEGIN RAISE EXCEPTION 'audit log is append-only'; END; $$ LANGUAGE plpgsql",
    "CREATE TRIGGER audit_no_update_delete BEFORE UPDATE OR DELETE ON audit_auditentry "
    "FOR EACH ROW EXECUTE FUNCTION audit_append_only()",
    "CREATE TRIGGER audit_no_truncate BEFORE TRUNCATE ON audit_auditentry "
    "FOR EACH STATEMENT EXECUTE FUNCTION audit_append_only()",
]
POSTGRES_DOWN = [
    "DROP TRIGGER IF EXISTS audit_no_truncate ON audit_auditentry",
    "DROP TRIGGER IF EXISTS audit_no_update_delete ON audit_auditentry",
    "DROP FUNCTION IF EXISTS audit_append_only()",
]


def _run(statements_by_vendor):
    def forward(apps, schema_editor):
        for sql in statements_by_vendor.get(schema_editor.connection.vendor, []):
            schema_editor.execute(sql)

    return forward


class Migration(migrations.Migration):
    dependencies = [("audit", "0001_initial")]

    operations = [
        migrations.RunPython(
            _run({"sqlite": SQLITE_UP, "postgresql": POSTGRES_UP}),
            _run({"sqlite": SQLITE_DOWN, "postgresql": POSTGRES_DOWN}),
        ),
    ]
