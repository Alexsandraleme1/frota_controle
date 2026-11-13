"""Migra um backup ZIP do SECW Frota (SQLite + uploads) para PostgreSQL.
Uso: DATABASE_URL='postgresql://...' python migrar_sqlite_para_postgres.py backup.zip
"""
import os, sys, sqlite3, zipfile, tempfile, shutil
import psycopg2
import app as frota_app

# Cria as tabelas e índices no PostgreSQL antes de importar os registros.
frota_app.init_db()

if len(sys.argv) != 2:
    raise SystemExit("Uso: python migrar_sqlite_para_postgres.py caminho_do_backup.zip OU caminho/secw_frota.db")
if not os.environ.get("DATABASE_URL"):
    raise SystemExit("Configure DATABASE_URL com a Internal Database URL do PostgreSQL no Render.")
backup_zip = os.path.abspath(sys.argv[1])
tables = ["users", "vehicles", "employees", "usages", "equipment_items", "usage_equipment_checks", "fuel", "tolls", "maintenance", "documents", "inspections", "notifications", "tracking_points", "tracking_sessions", "audit_log", "fines"]

with tempfile.TemporaryDirectory() as tmp:
    input_path = os.path.abspath(sys.argv[1])
    if zipfile.is_zipfile(input_path):
        with zipfile.ZipFile(input_path) as zf:
            names = set(zf.namelist())
            if "secw_frota.db" not in names:
                raise SystemExit("O ZIP não contém secw_frota.db. Use um backup SQLite antigo do sistema.")
            zf.extract("secw_frota.db", tmp)
            for name in names:
                if name.startswith("uploads/") and not name.endswith("/"):
                    target_path = os.path.realpath(os.path.join(tmp, name))
                    if target_path.startswith(os.path.realpath(tmp) + os.sep):
                        zf.extract(name, tmp)
        sqlite_path = os.path.join(tmp, "secw_frota.db")
        uploads_src = os.path.join(tmp, "uploads")
    else:
        sqlite_path = input_path
        uploads_src = os.path.join(os.environ.get("SECFROTA_DATA_DIR", os.path.dirname(__file__)), "uploads")
    if not os.path.isfile(sqlite_path):
        raise SystemExit(f"Banco SQLite não encontrado: {sqlite_path}")
    source = sqlite3.connect(sqlite_path)
    source.row_factory = sqlite3.Row
    target = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        with target.cursor() as cur:
            # O init_db cria um administrador padrão; removemos apenas esse registro
            # de bootstrap para preservar o ID/senha do administrador do SQLite.
            cur.execute("DELETE FROM users WHERE username='admin'")
            cur.execute("DELETE FROM employees WHERE name='Administrador SEC.W'")
            for table in tables:
                source_cols = [r[1] for r in source.execute(f"PRAGMA table_info({table})").fetchall()]
                if not source_cols:
                    continue
                cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=%s", (table,))
                target_cols = {r[0] for r in cur.fetchall()}
                cols = [c for c in source_cols if c in target_cols]
                if not cols:
                    continue
                quoted = ",".join('"' + c.replace('"','""') + '"' for c in cols)
                placeholders = ",".join(["%s"] * len(cols))
                rows = source.execute(f"SELECT {quoted} FROM {table}").fetchall()
                for row in rows:
                    cur.execute(f"INSERT INTO {table} ({quoted}) VALUES ({placeholders}) ON CONFLICT DO NOTHING", tuple(row[c] for c in cols))
                if "id" in target_cols:
                    cur.execute(f"SELECT setval(pg_get_serial_sequence('{table}','id'), COALESCE(MAX(id),1), MAX(id) IS NOT NULL) FROM {table}")
        target.commit()
    except Exception:
        target.rollback()
        raise
    finally:
        source.close(); target.close()
    if os.path.isdir(uploads_src):
        uploads_dst = frota_app.UPLOAD_DIR
        os.makedirs(uploads_dst, exist_ok=True)
        for root, _, files in os.walk(uploads_src):
            for filename in files:
                src = os.path.join(root, filename)
                rel = os.path.relpath(src, uploads_src)
                dst = os.path.join(uploads_dst, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                if not os.path.exists(dst): shutil.copy2(src, dst)
print("Migração concluída. Registros existentes foram importados sem sobrescrever IDs já presentes.")
