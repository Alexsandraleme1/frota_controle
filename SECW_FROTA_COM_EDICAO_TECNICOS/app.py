
import os, sqlite3, csv, io, uuid, zipfile, tempfile, secrets, json, re
from datetime import date, datetime, timedelta
from functools import wraps
from flask import Flask, g, render_template, request, redirect, url_for, session, flash, send_file, abort, jsonify, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from PIL import Image, ImageOps

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql://" + DATABASE_URL[len("postgres://"): ]
USE_POSTGRES = bool(DATABASE_URL)
if USE_POSTGRES:
    import psycopg2
    from psycopg2.extras import DictCursor
    INTEGRITY_ERRORS = psycopg2.IntegrityError
else:
    INTEGRITY_ERRORS = sqlite3.IntegrityError

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
# Em produção no Render, configure SECFROTA_DATA_DIR=/var/data e monte um Persistent Disk.
DATA_DIR = os.environ.get("SECFROTA_DATA_DIR", BASE_DIR)
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "secw_frota.db")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

app = Flask(__name__)
# Use a chave definida pelo administrador; caso contrário, gere uma chave local persistente.
_secret_path = os.path.join(DATA_DIR, ".secret_key")
_secret = os.environ.get("SECFROTA_SECRET")
if not _secret:
    try:
        with open(_secret_path, "r", encoding="utf-8") as _secret_file:
            _secret = _secret_file.read().strip()
    except OSError:
        _secret = secrets.token_urlsafe(48)
        try:
            with open(_secret_path, "w", encoding="utf-8") as _secret_file:
                _secret_file.write(_secret)
        except OSError:
            pass
app.secret_key = _secret
app.config["MAX_CONTENT_LENGTH"] = 35 * 1024 * 1024
app.config["UPLOAD_FOLDER"] = UPLOAD_DIR
app.permanent_session_lifetime = timedelta(days=14)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=os.environ.get("SECFROTA_HTTPS", "0") == "1")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
 password_hash TEXT NOT NULL, full_name TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('admin','fleet','employee')), active INTEGER DEFAULT 1, employee_id INTEGER, permissions TEXT DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS vehicles (
 id INTEGER PRIMARY KEY AUTOINCREMENT, plate TEXT UNIQUE NOT NULL, brand TEXT, model TEXT,
 year TEXT, color TEXT, asset_number TEXT, current_km REAL DEFAULT 0,
 status TEXT DEFAULT 'Disponível', notes TEXT, photo TEXT, renavam TEXT, chassis TEXT, acquisition_date TEXT, insurance_due TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS employees (
 id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, department TEXT, phone TEXT,
 license_number TEXT, license_category TEXT, license_due TEXT, active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS usages (
 id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_id INTEGER NOT NULL, employee_id INTEGER NOT NULL,
 departure TEXT NOT NULL, planned_return TEXT, destination TEXT, km_out REAL NOT NULL, return_at TEXT,
 km_in REAL, notes TEXT, created_by INTEGER, return_notes TEXT, status TEXT DEFAULT 'Aprovada',
 departure_confirmed_by TEXT, departure_confirmed_at TEXT, return_confirmed_by TEXT, return_confirmed_at TEXT, usage_mode TEXT DEFAULT 'Avulsa',
 FOREIGN KEY(vehicle_id) REFERENCES vehicles(id), FOREIGN KEY(employee_id) REFERENCES employees(id)
);
CREATE TABLE IF NOT EXISTS equipment_items (
 id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL COLLATE NOCASE UNIQUE,
 active INTEGER NOT NULL DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS usage_equipment_checks (
 id INTEGER PRIMARY KEY AUTOINCREMENT, usage_id INTEGER NOT NULL,
 item_name TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('Presente','Ausente')),
 notes TEXT, checked_at TEXT DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(usage_id) REFERENCES usages(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS fuel (
 id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_id INTEGER NOT NULL, date TEXT NOT NULL,
 km REAL NOT NULL, liters REAL NOT NULL, amount REAL NOT NULL, fuel_type TEXT,
 station TEXT, receipt TEXT, notes TEXT, FOREIGN KEY(vehicle_id) REFERENCES vehicles(id)
);
CREATE TABLE IF NOT EXISTS tolls (
 id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_id INTEGER NOT NULL, date TEXT NOT NULL,
 plaza TEXT NOT NULL, direction TEXT, amount REAL NOT NULL, payment_method TEXT,
 receipt TEXT, notes TEXT, created_by INTEGER,
 FOREIGN KEY(vehicle_id) REFERENCES vehicles(id)
);
CREATE TABLE IF NOT EXISTS maintenance (
 id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_id INTEGER NOT NULL, kind TEXT NOT NULL,
 date TEXT NOT NULL, km REAL NOT NULL, description TEXT, workshop TEXT, amount REAL DEFAULT 0,
 next_date TEXT, next_km REAL, status TEXT DEFAULT 'Concluída',
 FOREIGN KEY(vehicle_id) REFERENCES vehicles(id)
);
CREATE TABLE IF NOT EXISTS documents (
 id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_id INTEGER NOT NULL, kind TEXT NOT NULL,
 due_date TEXT NOT NULL, status TEXT DEFAULT 'Vigente', notes TEXT,
 FOREIGN KEY(vehicle_id) REFERENCES vehicles(id)
);
CREATE TABLE IF NOT EXISTS inspections (
 id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_id INTEGER NOT NULL, employee_id INTEGER, date TEXT NOT NULL,
 km REAL NOT NULL, tires TEXT, lights TEXT, bodywork TEXT, equipment TEXT, observations TEXT, photo TEXT, created_by INTEGER,
 FOREIGN KEY(vehicle_id) REFERENCES vehicles(id), FOREIGN KEY(employee_id) REFERENCES employees(id)
);
CREATE TABLE IF NOT EXISTS notifications (
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, title TEXT NOT NULL,
 message TEXT NOT NULL, link TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, read_at TEXT,
 FOREIGN KEY(user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS tracking_points (
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, employee_id INTEGER NOT NULL,
 usage_id INTEGER NOT NULL, vehicle_id INTEGER NOT NULL, latitude REAL NOT NULL, longitude REAL NOT NULL,
 accuracy REAL, speed REAL, heading REAL, recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(user_id) REFERENCES users(id), FOREIGN KEY(employee_id) REFERENCES employees(id),
 FOREIGN KEY(usage_id) REFERENCES usages(id), FOREIGN KEY(vehicle_id) REFERENCES vehicles(id)
);
CREATE TABLE IF NOT EXISTS tracking_sessions (
 user_id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0,
 consent_at TEXT, stopped_at TEXT, updated_at TEXT,
 FOREIGN KEY(user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS audit_log (
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, action TEXT NOT NULL,
 entity TEXT NOT NULL, entity_id INTEGER, details TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS fines (
 id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_id INTEGER NOT NULL, employee_id INTEGER,
 date TEXT NOT NULL, description TEXT NOT NULL, amount REAL DEFAULT 0,
 status TEXT DEFAULT 'Pendente', due_date TEXT, notes TEXT,
 FOREIGN KEY(vehicle_id) REFERENCES vehicles(id),
 FOREIGN KEY(employee_id) REFERENCES employees(id)
);
"""

class PgCursor:
    def __init__(self, cursor, lastrowid=None):
        self._cursor = cursor
        self.lastrowid = lastrowid
    def fetchone(self): return self._cursor.fetchone()
    def fetchall(self): return self._cursor.fetchall()
    def __iter__(self): return iter(self._cursor)

class PgConnection:
    """Adaptador pequeno para manter as consultas existentes compatíveis com PostgreSQL."""
    def __init__(self, url):
        self.raw = psycopg2.connect(url, cursor_factory=DictCursor)
        self.last_id = None
    def execute(self, sql, params=()):
        sql = sql.strip()
        if "last_insert_rowid()" in sql.lower():
            cur = self.raw.cursor()
            cur.execute("SELECT %s AS id", (self.last_id,))
            return cur
        sql = sql.replace("?", "%s")
        sql = re.sub(r"\b([A-Za-z_][A-Za-z0-9_]*)\s+COLLATE\s+NOCASE\b", r"LOWER(\1)", sql, flags=re.I)
        sql = re.sub(r"date\('now','start of month'\)", "date_trunc('month', CURRENT_DATE)::date::text", sql, flags=re.I)
        insert_tables = {"users","vehicles","employees","usages","equipment_items","usage_equipment_checks","fuel","tolls","maintenance","documents","inspections","notifications","tracking_points","audit_log","fines"}
        m = re.match(r"INSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)", sql, flags=re.I)
        wants_id = bool(m and m.group(1).lower() in insert_tables and "RETURNING" not in sql.upper())
        if wants_id:
            sql = sql.rstrip().rstrip(";") + " RETURNING id"
        cur = self.raw.cursor()
        cur.execute(sql, params)
        if wants_id:
            row = cur.fetchone()
            self.last_id = row["id"] if row else None
            return PgCursor(cur, self.last_id)
        return cur
    def commit(self): self.raw.commit()
    def rollback(self): self.raw.rollback()
    def close(self): self.raw.close()

def db():
    if "db" not in g:
        if USE_POSTGRES:
            g.db = PgConnection(DATABASE_URL)
        else:
            g.db = sqlite3.connect(DB_PATH)
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA foreign_keys = ON")
    return g.db

@app.teardown_appcontext
def close_db(exc):
    conn = g.pop("db", None)
    if conn is not None: conn.close()

def audit(action, entity, entity_id=None, details=""):
    """Registra ações relevantes para rastreabilidade administrativa."""
    try:
        db().execute("INSERT INTO audit_log(user_id,username,action,entity,entity_id,details) VALUES(?,?,?,?,?,?)",
            (session.get("user_id"), session.get("username"), action, entity, entity_id, str(details)[:1000]))
    except Exception:
        # O registro principal não deve falhar por uma falha isolada de auditoria.
        pass

def notify_user(user_id, title, message, link=None):
    if user_id:
        db().execute("INSERT INTO notifications(user_id,title,message,link) VALUES(?,?,?,?)",
                     (int(user_id), str(title)[:120], str(message)[:500], link))

def notify_roles(title, message, link=None, roles_list=("admin", "fleet")):
    ids = db().execute("SELECT id FROM users WHERE active=1 AND role IN (%s)" % ",".join("?" for _ in roles_list), tuple(roles_list)).fetchall()
    for row in ids:
        notify_user(row["id"], title, message, link)

PERMISSION_LABELS = {
    "dashboard": "Painel", "notifications": "Notificações", "vehicles": "Veículos",
    "usage": "Utilização", "daily_use": "Responsabilidade diária", "employees": "Colaboradores", "inspections": "Vistorias",
    "fuel": "Abastecimentos", "tolls": "Pedágios", "maintenance": "Manutenções", "documents": "Documentos",
    "fines": "Multas e infrações", "reports": "Relatórios", "advanced_reports": "Indicadores avançados", "tracking": "Acompanhamento da frota",
}
DEFAULT_EMPLOYEE_PERMISSIONS = ["dashboard", "notifications", "usage", "daily_use", "fuel"]
DEFAULT_FLEET_PERMISSIONS = list(PERMISSION_LABELS.keys())

def user_permissions(user_id=None):
    if session.get("role") == "admin":
        return list(PERMISSION_LABELS.keys())
    user_id = user_id or session.get("user_id")
    if not user_id:
        return []
    row = db().execute("SELECT permissions,role FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        return []
    try:
        values = json.loads(row["permissions"] or "[]")
        if not isinstance(values, list):
            values = DEFAULT_EMPLOYEE_PERMISSIONS if row["role"] == "employee" else DEFAULT_FLEET_PERMISSIONS
    except (ValueError, TypeError):
        values = DEFAULT_EMPLOYEE_PERMISSIONS if row["role"] == "employee" else DEFAULT_FLEET_PERMISSIONS
    return [v for v in values if v in PERMISSION_LABELS]

ENDPOINT_PERMISSION = {
    "vehicles": "vehicles", "vehicle_edit": "vehicles", "vehicle_delete": "vehicles", "vehicle_history": "vehicles",
    "employees": "employees", "employee_delete": "employees", "employee_toggle": "employees",
    "usage": "usage", "daily_usage": "daily_use", "usage_approve": "usage", "usage_reject": "usage", "usage_return": "usage", "usage_edit": "usage",
    "fuel": "fuel", "tolls": "tolls", "maintenance": "maintenance", "documents": "documents", "fines": "fines", "fine_status": "fines",
    "inspections": "inspections", "reports": "reports", "reports_pdf": "reports", "reports_csv": "reports", "advanced_reports": "advanced_reports",
}

def init_db():
    if USE_POSTGRES:
        conn = PgConnection(DATABASE_URL)
        try:
            pg_schema = SCHEMA.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY").replace(" TEXT NOT NULL COLLATE NOCASE UNIQUE", " TEXT NOT NULL UNIQUE")
            for statement in pg_schema.split(";"):
                if statement.strip(): conn.execute(statement)
            def columns(table):
                rows = conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=?", (table,)).fetchall()
                return {row["column_name"] for row in rows}
            migrations = {
                "vehicles": {"photo":"TEXT", "renavam":"TEXT", "chassis":"TEXT", "acquisition_date":"TEXT", "insurance_due":"TEXT"},
                "employees": {"document_number":"TEXT", "license_number":"TEXT", "license_category":"TEXT", "license_due":"TEXT", "photo":"TEXT"},
                "users": {"employee_id":"INTEGER", "permissions":"TEXT DEFAULT '{}'"},
                "fuel": {"created_by":"INTEGER"},
                "usages": {"planned_return":"TEXT", "return_notes":"TEXT", "status":"TEXT DEFAULT 'Aprovada'", "departure_confirmed_by":"TEXT", "departure_confirmed_at":"TEXT", "return_confirmed_by":"TEXT", "return_confirmed_at":"TEXT", "usage_mode":"TEXT DEFAULT 'Avulsa'", "departure_photos":"TEXT", "return_photos":"TEXT", "departure_lat":"REAL", "departure_lon":"REAL", "return_lat":"REAL", "return_lon":"REAL"}
            }
            for table, fields in migrations.items():
                existing = columns(table)
                for col, typ in fields.items():
                    if col not in existing: conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
            had_permissions_column = "permissions" in columns("users")
            username_exists = conn.execute("SELECT id FROM users WHERE username='admin' LIMIT 1").fetchone()
            if username_exists:
                conn.execute("UPDATE users SET full_name='Administrador SEC.W', role='admin', active=1 WHERE username='admin'")
                conn.execute("UPDATE users SET password_hash=? WHERE username='admin'", (generate_password_hash("Secw@123"),)) if not had_permissions_column else None
                conn.execute("UPDATE users SET permissions=? WHERE username='admin'", (json.dumps(list(PERMISSION_LABELS.keys())),))
            else:
                conn.execute("INSERT INTO users(username,password_hash,full_name,role,active,permissions) VALUES(?,?,?,?,1,?)", ("admin", generate_password_hash("Secw@123"), "Administrador SEC.W", "admin", json.dumps(list(PERMISSION_LABELS.keys()))))
            if conn.execute("SELECT COUNT(*) AS total FROM employees WHERE name='Administrador SEC.W'").fetchone()["total"] == 0:
                conn.execute("INSERT INTO employees(name,department) VALUES(?,?)", ("Administrador SEC.W", "Administrativo"))
            conn.commit()
        finally: conn.close()
        return
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    vehicle_columns = {row[1] for row in conn.execute("PRAGMA table_info(vehicles)").fetchall()}
    for col, typ in {"photo":"TEXT", "renavam":"TEXT", "chassis":"TEXT", "acquisition_date":"TEXT", "insurance_due":"TEXT"}.items():
        if col not in vehicle_columns: conn.execute(f"ALTER TABLE vehicles ADD COLUMN {col} {typ}")
    employee_columns = {row[1] for row in conn.execute("PRAGMA table_info(employees)").fetchall()}
    for col, typ in (("document_number","TEXT"),("license_number","TEXT"),("license_category","TEXT"),("license_due","TEXT"),("photo","TEXT")):
        if col not in employee_columns: conn.execute(f"ALTER TABLE employees ADD COLUMN {col} {typ}")
    user_columns = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    had_permissions_column = "permissions" in user_columns
    if "employee_id" not in user_columns: conn.execute("ALTER TABLE users ADD COLUMN employee_id INTEGER")
    if "permissions" not in user_columns: conn.execute("ALTER TABLE users ADD COLUMN permissions TEXT DEFAULT '{}'")
    fuel_columns = {row[1] for row in conn.execute("PRAGMA table_info(fuel)").fetchall()}
    if "created_by" not in fuel_columns: conn.execute("ALTER TABLE fuel ADD COLUMN created_by INTEGER")
    usage_columns = {row[1] for row in conn.execute("PRAGMA table_info(usages)").fetchall()}
    for col, typ in (("planned_return","TEXT"),("return_notes","TEXT"),("status","TEXT DEFAULT 'Aprovada'"),("departure_confirmed_by","TEXT"),("departure_confirmed_at","TEXT"),("return_confirmed_by","TEXT"),("return_confirmed_at","TEXT"),("usage_mode","TEXT DEFAULT 'Avulsa'"),("departure_photos","TEXT"),("return_photos","TEXT"),("departure_lat","REAL"),("departure_lon","REAL"),("return_lat","REAL"),("return_lon","REAL")):
        if col not in usage_columns: conn.execute(f"ALTER TABLE usages ADD COLUMN {col} {typ}")
    username_exists = conn.execute("SELECT id FROM users WHERE username='admin' LIMIT 1").fetchone()
    if username_exists:
        conn.execute("UPDATE users SET full_name='Administrador SEC.W', role='admin', active=1 WHERE username='admin'")
        if not had_permissions_column: conn.execute("UPDATE users SET password_hash=? WHERE username='admin'", (generate_password_hash("Secw@123"),))
        conn.execute("UPDATE users SET permissions=? WHERE username='admin'", (json.dumps(list(PERMISSION_LABELS.keys())),))
    else:
        conn.execute("INSERT INTO users(username,password_hash,full_name,role,active,permissions) VALUES(?,?,?,?,1,?)", ("admin", generate_password_hash("Secw@123"), "Administrador SEC.W", "admin", json.dumps(list(PERMISSION_LABELS.keys()))))
    if conn.execute("SELECT COUNT(*) FROM employees WHERE name='Administrador SEC.W'").fetchone()[0] == 0:
        conn.execute("INSERT INTO employees(name,department) VALUES(?,?)", ("Administrador SEC.W", "Administrativo"))
    conn.commit(); conn.close()

@app.context_processor
def inject_profile_photo():
    photo = None
    employee_id = session.get("employee_id")
    if employee_id:
        try:
            row = db().execute("SELECT photo FROM employees WHERE id=?", (employee_id,)).fetchone()
            photo = row["photo"] if row else None
        except Exception:
            photo = None
    return {"current_employee_photo": photo}

def maybe_daily_backup():
    """Cria backup ZIP local no primeiro acesso de cada dia e conserva os 14 mais recentes."""
    today = date.today().isoformat()
    marker = os.path.join(BACKUP_DIR, "last_auto_backup.txt")
    try:
        if os.path.isfile(marker) and open(marker, "r", encoding="utf-8").read().strip() == today:
            return
        backup_name = f"secw_frota_auto_{today}.zip"
        backup_path = os.path.join(BACKUP_DIR, backup_name)
        with tempfile.TemporaryDirectory() as tempdir:
            db_copy = os.path.join(tempdir, "secw_frota.db")
            with zipfile.ZipFile(backup_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                if USE_POSTGRES:
                    archive.writestr("BANCO-POSTGRES.txt", "Banco de dados hospedado no PostgreSQL do Render. Faça backup pelo painel do provedor antes de operações críticas.\n")
                else:
                    source = sqlite3.connect(DB_PATH); target = sqlite3.connect(db_copy)
                    source.backup(target); target.close(); source.close()
                    archive.write(db_copy, "secw_frota.db")
                for root, _, files in os.walk(UPLOAD_DIR):
                    for filename in files:
                        full = os.path.join(root, filename)
                        archive.write(full, os.path.join("uploads", os.path.relpath(full, UPLOAD_DIR)))
        with open(marker, "w", encoding="utf-8") as f:
            f.write(today)
        backups = sorted((os.path.join(BACKUP_DIR, n) for n in os.listdir(BACKUP_DIR)
                          if n.startswith("secw_frota_auto_") and n.endswith(".zip")), key=os.path.getmtime, reverse=True)
        for old in backups[14:]:
            try: os.remove(old)
            except OSError: pass
    except Exception:
        # A falha de backup não deve impedir o acesso ao sistema; o backup manual continua disponível.
        return

@app.before_request
def ensure_db():
    init_db()
    maybe_daily_backup()
    if not session.get("user_id") or session.get("role") == "admin":
        return None
    endpoint = request.endpoint or ""
    feature = ENDPOINT_PERMISSION.get(endpoint)
    if feature and feature not in user_permissions():
        abort(403)

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"): return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper

def roles(*allowed):
    def deco(fn):
        @wraps(fn)
        @login_required
        def wrapper(*args, **kwargs):
            if session.get("role") in allowed:
                return fn(*args, **kwargs)
            feature = ENDPOINT_PERMISSION.get(fn.__name__)
            if "admin" not in allowed and feature and feature in user_permissions():
                return fn(*args, **kwargs)
            abort(403)
        return wrapper
    return deco

@app.template_filter("fromjson")
def fromjson_filter(value):
    try:
        return json.loads(value) if value else {}
    except (TypeError, ValueError):
        return {}

@app.context_processor
def inject_globals():
    active_employee_usages = []
    if session.get("user_id") and session.get("role") == "employee" and session.get("employee_id"):
        try:
            active_employee_usages = db().execute("""SELECT id,vehicle_id,usage_mode FROM usages
                WHERE employee_id=? AND status='Aprovada' AND return_at IS NULL ORDER BY id DESC""",
                (session["employee_id"],)).fetchall()
        except Exception:
            active_employee_usages = []
    return {"current_user": session.get("full_name"), "role": session.get("role"),
            "permissions": user_permissions() if session.get("user_id") else [],
            "permission_labels": PERMISSION_LABELS, "today": date.today().isoformat(),
            "active_employee_usages": active_employee_usages}

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        u = db().execute("SELECT * FROM users WHERE username=? AND active=1", (request.form.get("username","").strip(),)).fetchone()
        if u and check_password_hash(u["password_hash"], request.form.get("password","")):
            session.clear(); session.update(user_id=u["id"], username=u["username"], full_name=u["full_name"], role=u["role"], employee_id=u["employee_id"]); session.permanent = bool(request.form.get("remember"))
            audit("Login", "sessão", u["id"]); db().commit()
            allowed = user_permissions(u["id"])
            if u["role"] == "employee":
                return redirect(url_for("dashboard"))
            if "dashboard" in allowed or u["role"] == "admin":
                return redirect(url_for("dashboard"))
            preferred = [("usage", "usage"), ("fuel", "fuel"), ("tolls", "tolls"), ("vehicles", "vehicles"), ("employees", "employees"), ("inspections", "inspections"), ("maintenance", "maintenance"), ("documents", "documents"), ("fines", "fines"), ("reports", "reports")]
            for feature, endpoint in preferred:
                if feature in allowed:
                    return redirect(url_for(endpoint))
            flash("Seu usuário ainda não tem nenhuma função liberada. Peça ao administrador para configurar suas permissões.", "error")
            return redirect(url_for("change_password"))
        flash("Usuário ou senha inválidos.", "error")
    return render_template("login.html")

@app.get("/logout")
def logout():
    session.clear(); return redirect(url_for("login"))

@app.get("/")
@login_required
def dashboard():
    d = db()
    if session.get("role") == "employee":
        if not session.get("employee_id"):
            return render_template("technician_dashboard.html", employee=None, license_status="Sem vínculo", license_message="Solicite ao administrador o vínculo do seu cadastro.", active_usage=None, notifications=[])
        employee = d.execute("SELECT * FROM employees WHERE id=?", (session["employee_id"],)).fetchone()
        if not employee:
            return render_template("technician_dashboard.html", employee=None, license_status="Sem cadastro", license_message="Seu cadastro não foi localizado. Procure o responsável pela frota.", active_usage=None, notifications=[])
        due = employee["license_due"] or ""
        if not due:
            license_status, license_message = "Não informado", "A validade da sua CNH ainda não foi cadastrada. Procure o responsável pela frota."
        elif due < date.today().isoformat():
            license_status, license_message = "Vencida", "Atenção: sua CNH está vencida. Não conduza veículos até regularizar a documentação."
        elif due <= (date.today()+timedelta(days=30)).isoformat():
            license_status, license_message = "Próxima do vencimento", "Sua CNH vence em breve. Verifique a renovação."
        else:
            license_status, license_message = "Em dia", "Tudo certo com sua CNH. Boa jornada!"
        active_usage = d.execute("""SELECT u.*,v.plate,v.model FROM usages u JOIN vehicles v ON v.id=u.vehicle_id
            WHERE u.employee_id=? AND u.status='Aprovada' AND u.return_at IS NULL ORDER BY u.id DESC LIMIT 1""", (session["employee_id"],)).fetchone()
        return render_template("technician_dashboard.html", employee=employee, license_status=license_status,
            license_message=license_message, active_usage=active_usage)
    counts = {
      "total": d.execute("SELECT COUNT(*) FROM vehicles").fetchone()[0],
      "available": d.execute("SELECT COUNT(*) FROM vehicles WHERE status='Disponível'").fetchone()[0],
      "in_use": d.execute("SELECT COUNT(*) FROM vehicles WHERE status='Em uso'").fetchone()[0],
      "maintenance": d.execute("SELECT COUNT(*) FROM vehicles WHERE status='Em manutenção'").fetchone()[0],
      "pending": d.execute("SELECT COUNT(*) FROM maintenance WHERE status='Pendente'").fetchone()[0],
      "docs": d.execute("SELECT COUNT(*) FROM documents WHERE due_date<=? AND due_date IS NOT NULL", ((date.today()+timedelta(days=30)).isoformat(),)).fetchone()[0] +
              d.execute("SELECT COUNT(*) FROM vehicles WHERE insurance_due IS NOT NULL AND insurance_due!='' AND insurance_due<=?", ((date.today()+timedelta(days=30)).isoformat(),)).fetchone()[0],
      "fines": d.execute("SELECT COUNT(*) FROM fines WHERE status='Pendente'").fetchone()[0],
      "requests": d.execute("SELECT COUNT(*) FROM usages WHERE status='Solicitada'").fetchone()[0],
      "licenses": d.execute("SELECT COUNT(*) FROM employees WHERE active=1 AND license_due IS NOT NULL AND license_due!='' AND license_due<=?", ((date.today()+timedelta(days=30)).isoformat(),)).fetchone()[0]
    }
    due_limit=(date.today()+timedelta(days=30)).isoformat()
    alerts = d.execute("""SELECT x.kind,x.due_date,x.status,v.plate FROM documents x JOIN vehicles v ON v.id=x.vehicle_id
                         WHERE x.due_date<=? AND x.due_date IS NOT NULL
                         UNION ALL SELECT 'Seguro' AS kind,v.insurance_due AS due_date,'Atenção' AS status,v.plate
                         FROM vehicles v WHERE v.insurance_due IS NOT NULL AND v.insurance_due!='' AND v.insurance_due<=?
                         ORDER BY due_date LIMIT 12""", (due_limit,due_limit)).fetchall()
    license_alerts = d.execute("""SELECT name,license_number,license_category,license_due FROM employees
        WHERE active=1 AND license_due IS NOT NULL AND license_due!='' AND license_due<=?
        ORDER BY license_due LIMIT 12""", (due_limit,)).fetchall()
    maintenance_alerts = d.execute("""SELECT m.*,v.plate,v.current_km FROM maintenance m JOIN vehicles v ON v.id=m.vehicle_id
        WHERE (m.next_date IS NOT NULL AND m.next_date<=?) OR (m.next_km IS NOT NULL AND v.current_km>=m.next_km)
        ORDER BY COALESCE(m.next_date,'9999-12-31') LIMIT 8""", ((date.today()+timedelta(days=30)).isoformat(),)).fetchall()
    fine_alerts=d.execute("""SELECT f.*,v.plate,e.name employee FROM fines f JOIN vehicles v ON v.id=f.vehicle_id
        LEFT JOIN employees e ON e.id=f.employee_id WHERE f.status IN ('Pendente','Em contestação') AND f.due_date IS NOT NULL AND f.due_date<=?
        ORDER BY f.due_date LIMIT 8""", ((date.today()+timedelta(days=30)).isoformat(),)).fetchall()
    spending = d.execute("""SELECT
      COALESCE((SELECT SUM(amount) FROM fuel WHERE date>=date('now','start of month')),0) fuel,
      COALESCE((SELECT SUM(amount) FROM maintenance WHERE date>=date('now','start of month')),0) maintenance,
      COALESCE((SELECT SUM(amount) FROM fines WHERE date>=date('now','start of month') AND status!='Cancelada'),0) fines,
      COALESCE((SELECT SUM(amount) FROM tolls WHERE date>=date('now','start of month')),0) tolls""").fetchone()
    return render_template("dashboard.html", counts=counts, alerts=alerts, license_alerts=license_alerts, maintenance_alerts=maintenance_alerts, fine_alerts=fine_alerts, spending=spending)

@app.route("/vehicles", methods=["GET","POST"])
@login_required
def vehicles():
    d=db()
    if request.method=="POST":
        if session.get("role") not in ("admin", "fleet"):
            abort(403)
        try:
            photo = save_vehicle_photo(request.files.get("photo"))
            d.execute("""INSERT INTO vehicles(plate,brand,model,year,color,asset_number,current_km,status,notes,photo,renavam,chassis,acquisition_date,insurance_due)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", tuple(request.form.get(k,"").strip() for k in
              ["plate","brand","model","year","color","asset_number","current_km","status","notes"]) + (photo,) + tuple(request.form.get(k,"").strip() for k in ["renavam","chassis","acquisition_date","insurance_due"]))
            d.commit(); audit("Cadastro", "veículo", d.execute("SELECT last_insert_rowid()").fetchone()[0], request.form.get("plate")); d.commit(); flash("Veículo cadastrado com sucesso.","success")
        except ValueError as exc: flash(str(exc),"error")
        except INTEGRITY_ERRORS: flash("Já existe um veículo cadastrado com essa placa.","error")
        return redirect(url_for("vehicles"))
    rows=d.execute("SELECT * FROM vehicles ORDER BY plate").fetchall()
    return render_template("list.html", title="Veículos", section="vehicles", rows=rows)

@app.route("/vehicles/<int:item_id>/edit", methods=["GET","POST"])
@roles("admin","fleet")
def vehicle_edit(item_id):
    d=db(); item=d.execute("SELECT * FROM vehicles WHERE id=?",(item_id,)).fetchone()
    if not item: abort(404)
    if request.method=="POST":
        try:
            uploaded_photo = save_vehicle_photo(request.files.get("photo"))
            photo = uploaded_photo or item["photo"]
            d.execute("""UPDATE vehicles SET plate=?,brand=?,model=?,year=?,color=?,asset_number=?,current_km=?,status=?,notes=?,photo=?,renavam=?,chassis=?,acquisition_date=?,insurance_due=? WHERE id=?""",
              tuple(request.form.get(k,"").strip() for k in ["plate","brand","model","year","color","asset_number","current_km","status","notes"])+(photo,)+tuple(request.form.get(k,"").strip() for k in ["renavam","chassis","acquisition_date","insurance_due"])+(item_id,))
            d.commit(); audit("Edição", "veículo", item_id, request.form.get("plate")); d.commit(); flash("Cadastro atualizado.","success"); return redirect(url_for("vehicles"))
        except ValueError as exc: flash(str(exc),"error")
        except INTEGRITY_ERRORS: flash("Já existe um veículo cadastrado com essa placa.","error")
    return render_template("form.html", title="Editar veículo", item=item, fields=vehicle_fields())


def save_vehicle_photo(upload):
    """Salva foto opcional do veículo e retorna o nome seguro do arquivo."""
    if not upload or not upload.filename:
        return None
    original = secure_filename(upload.filename)
    ext = os.path.splitext(original)[1].lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise ValueError("Envie a foto do veículo em JPG, PNG ou WEBP.")
    filename = f"veiculo_{uuid.uuid4().hex}{ext}"
    upload.save(os.path.join(UPLOAD_DIR, filename))
    return filename

def save_employee_photo(upload):
    """Salva foto opcional de perfil do colaborador."""
    if not upload or not upload.filename:
        return None
    ext = os.path.splitext(secure_filename(upload.filename))[1].lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise ValueError("A foto do colaborador deve estar em JPG, PNG ou WEBP.")
    filename = f"colaborador_{uuid.uuid4().hex}{ext}"
    upload.save(os.path.join(UPLOAD_DIR, filename))
    return filename

def save_required_inspection_photos(prefix):
    """Recebe seis fotos, reduz resolução/tamanho e salva em JPEG otimizado."""
    labels = ("frente", "traseira", "lateral_esquerda", "lateral_direita", "interior", "painel_km")
    saved = {}
    created_files = []
    try:
        for label in labels:
            field = f"{prefix}_{label}"
            upload = request.files.get(field)
            if not upload or not upload.filename:
                raise ValueError(f"Foto obrigatória ausente: {label.replace('_', ' ')}.")
            ext = os.path.splitext(secure_filename(upload.filename))[1].lower()
            if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
                raise ValueError("As fotos devem estar em JPG, PNG ou WEBP.")

            # A imagem é normalizada para no máximo 1600 px e JPEG qualidade 75.
            # Isso reduz bastante o espaço em disco e facilita a abertura no celular.
            try:
                image = Image.open(upload.stream)
                image = ImageOps.exif_transpose(image)
                image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
                if image.mode not in ("RGB", "L"):
                    image = image.convert("RGB")
                elif image.mode == "L":
                    image = image.convert("RGB")
                filename = f"vistoria_{prefix}_{uuid.uuid4().hex}.jpg"
                filepath = os.path.join(UPLOAD_DIR, filename)
                image.save(filepath, format="JPEG", quality=75, optimize=True)
                image.close()
            except Exception as exc:
                raise ValueError(f"Não foi possível processar a foto {label.replace('_', ' ')}. Tente tirar novamente.") from exc

            saved[label] = filename
            created_files.append(filepath)
        return json.dumps(saved, ensure_ascii=False)
    except Exception:
        for filepath in created_files:
            try:
                os.remove(filepath)
            except OSError:
                pass
        raise


def vehicle_fields():
    return [("plate","Placa","text",True),("brand","Marca","text",False),("model","Modelo","text",False),
      ("year","Ano","text",False),("color","Cor","text",False),("asset_number","Patrimônio","text",False),
      ("current_km","Quilometragem atual","number",True),("status","Situação","select",True),
      ("renavam","RENAVAM","text",False),("chassis","Chassi","text",False),("acquisition_date","Data de aquisição","date",False),("insurance_due","Vencimento do seguro","date",False),("notes","Observações","textarea",False)]

@app.post("/vehicles/<int:item_id>/delete")
@roles("admin")
def vehicle_delete(item_id):
    try: db().execute("DELETE FROM vehicles WHERE id=?",(item_id,)); audit("Exclusão", "veículo", item_id); db().commit(); flash("Veículo excluído.","success")
    except INTEGRITY_ERRORS: flash("Não é possível excluir: há registros vinculados a este veículo.","error")
    return redirect(url_for("vehicles"))

@app.route("/employees", methods=["GET","POST"])
@roles("admin","fleet")
def employees():
    d=db()
    if request.method=="POST":
        name=request.form.get("name","").strip()
        username=request.form.get("username","").strip()
        password=request.form.get("password","")
        if bool(username) != bool(password):
            flash("Preencha login e senha juntos, ou deixe os dois campos vazios.", "error"); return redirect(url_for("employees"))
        if username and len(password) < 8:
            flash("A senha precisa ter pelo menos 8 caracteres.", "error"); return redirect(url_for("employees"))
        if username and d.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone():
            flash("Esse login já está sendo utilizado.", "error"); return redirect(url_for("employees"))
        try:
            profile_photo = save_employee_photo(request.files.get("photo"))
        except ValueError as exc:
            flash(str(exc), "error"); return redirect(url_for("employees"))
        d.execute("""INSERT INTO employees(name,department,phone,license_number,license_category,license_due,photo)
                  VALUES(?,?,?,?,?,?,?)""",
                  (name,request.form.get("department",""),request.form.get("phone",""),
                   request.form.get("license_number","" ).strip(), request.form.get("license_category",""),
                   request.form.get("license_due") or None, profile_photo))
        employee_id=d.execute("SELECT last_insert_rowid()").fetchone()[0]
        if username:
            d.execute("INSERT INTO users(username,password_hash,full_name,role,active,employee_id,permissions) VALUES(?,?,?,?,1,?,?)",
                      (username,generate_password_hash(password),name,"employee",employee_id,json.dumps(DEFAULT_EMPLOYEE_PERMISSIONS)))
            flash("Colaborador cadastrado com login. Ajuste as permissões na aba Permissões de acesso.","success")
        else:
            flash("Colaborador cadastrado. Você pode criar o login depois.","success")
        audit("Cadastro", "colaborador", employee_id, name); d.commit(); return redirect(url_for("employees"))
    rows=d.execute("SELECT * FROM employees ORDER BY name").fetchall()
    return render_template("list.html",title="Colaboradores",section="employees",rows=rows)

@app.post("/employees/<int:item_id>/photo")
@login_required
def employee_photo_update(item_id):
    if session.get("role") not in ("admin", "fleet") and not (session.get("role") == "employee" and int(session.get("employee_id") or 0) == item_id):
        abort(403)
    try:
        photo = save_employee_photo(request.files.get("photo"))
        if not photo:
            flash("Selecione uma imagem para atualizar a foto.", "error")
        else:
            db().execute("UPDATE employees SET photo=? WHERE id=?", (photo, item_id))
            audit("Atualização de foto", "colaborador", item_id)
            db().commit()
            flash("Foto do colaborador atualizada.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("dashboard") if session.get("role") == "employee" else url_for("employees"))

@app.post("/employees/<int:item_id>/delete")
@roles("admin")
def employee_delete(item_id):
    """Exclusão definitiva, permitida somente quando não há histórico vinculado."""
    d = db()
    employee = d.execute("SELECT id, name, photo FROM employees WHERE id=?", (item_id,)).fetchone()
    if not employee:
        flash("Colaborador não encontrado.", "error")
        return redirect(url_for("employees"))

    # Não apagar colaboradores que já aparecem em registros operacionais.
    linked_history = {
        "utilizações": d.execute("SELECT COUNT(*) FROM usages WHERE employee_id=?", (item_id,)).fetchone()[0],
        "vistorias": d.execute("SELECT COUNT(*) FROM inspections WHERE employee_id=?", (item_id,)).fetchone()[0],
        "multas": d.execute("SELECT COUNT(*) FROM fines WHERE employee_id=?", (item_id,)).fetchone()[0],
        "rastreamento": d.execute("SELECT COUNT(*) FROM tracking_points WHERE employee_id=?", (item_id,)).fetchone()[0],
    }
    existing = [f"{label}: {count}" for label, count in linked_history.items() if count]
    if existing:
        flash("Não é possível excluir definitivamente: existem registros históricos vinculados (" + ", ".join(existing) + "). Use Desativar para preservar o histórico.", "error")
        return redirect(url_for("employees"))

    try:
        user_ids = [row[0] for row in d.execute("SELECT id FROM users WHERE employee_id=?", (item_id,)).fetchall()]
        for user_id in user_ids:
            d.execute("DELETE FROM notifications WHERE user_id=?", (user_id,))
            d.execute("DELETE FROM tracking_sessions WHERE user_id=?", (user_id,))
            d.execute("DELETE FROM users WHERE id=?", (user_id,))
        audit("Exclusão definitiva", "colaborador", item_id, employee["name"])
        d.execute("DELETE FROM employees WHERE id=?", (item_id,))
        d.commit()
        if employee["photo"]:
            try:
                os.remove(os.path.join(UPLOAD_DIR, os.path.basename(employee["photo"])))
            except OSError:
                pass
        flash("Colaborador excluído definitivamente.", "success")
    except INTEGRITY_ERRORS:
        d.rollback()
        flash("Não foi possível excluir porque há outros registros vinculados. Desative o colaborador para preservar o histórico.", "error")
    return redirect(url_for("employees"))

@app.post("/employees/<int:item_id>/toggle")
@roles("admin")
def employee_toggle(item_id):
    db().execute("UPDATE employees SET active=1-active WHERE id=?",(item_id,)); audit("Alteração de status", "colaborador", item_id); db().commit()
    flash("Status do colaborador atualizado; histórico preservado.","success"); return redirect(url_for("employees"))

@app.route("/usage", methods=["GET","POST"])
@login_required
def usage():
    return _usage_screen(daily_mode=False)

@app.route("/daily-use", methods=["GET","POST"])
@login_required
def daily_usage():
    return _usage_screen(daily_mode=True)

@app.get("/devolucao")
@login_required
def devolution():
    return _usage_screen(return_only=True)

def _usage_screen(daily_mode=False, return_only=False):
    d=db()
    if return_only and request.method != "GET":
        abort(405)
    if request.method=="POST":
        vehicle_id=int(request.form["vehicle_id"]); km=float(request.form["km_out"])
        v=d.execute("SELECT * FROM vehicles WHERE id=?",(vehicle_id,)).fetchone()
        employee_id = int(request.form["employee_id"]) if request.form.get("employee_id") else None
        if session.get("role")=="employee":
            if not session.get("employee_id"):
                flash("Seu usuário ainda não está vinculado a um cadastro de colaborador. Solicite ao administrador que faça o vínculo.", "error")
                return redirect(url_for("daily_usage" if daily_mode else "usage"))
            employee_id = int(session["employee_id"])
        if employee_id is None:
            flash("Selecione um colaborador válido.", "error")
            return redirect(url_for("daily_usage" if daily_mode else "usage"))
        employee=d.execute("SELECT active,name FROM employees WHERE id=?", (employee_id,)).fetchone()
        planned_return = None if daily_mode else (request.form.get("planned_return") or None)
        departure = request.form["departure"]
        if planned_return and planned_return <= departure:
            flash("A previsão de devolução deve ser posterior à saída.", "error")
            return redirect(url_for("daily_usage" if daily_mode else "usage"))
        active_equipment = d.execute("SELECT id,name FROM equipment_items WHERE active=1 ORDER BY name COLLATE NOCASE").fetchall()
        equipment_answers = []
        if active_equipment:
            missing_answers = [item for item in active_equipment if request.form.get(f"equipment_{item['id']}") not in ("Presente", "Ausente")]
            if missing_answers:
                flash("Confira todos os equipamentos antes de registrar a saída.", "error")
                return redirect(url_for("daily_usage" if daily_mode else "usage"))
            equipment_answers = [(item["name"], request.form.get(f"equipment_{item['id']}"),
                                  (request.form.get(f"equipment_note_{item['id']}") or "").strip()) for item in active_equipment]
        # A responsabilidade diária é uma atribuição, não um bloqueio exclusivo:
        # outro técnico pode fazer uma utilização avulsa, desde que não exista outra
        # utilização avulsa ativa. O responsável diário continua registrado.
        daily_owner = d.execute("""SELECT u.id,u.employee_id,e.name employee_name
            FROM usages u JOIN employees e ON e.id=u.employee_id
            WHERE u.vehicle_id=? AND u.usage_mode='Diária' AND u.status='Aprovada' AND u.return_at IS NULL
            ORDER BY u.id DESC LIMIT 1""", (vehicle_id,)).fetchone()
        active_other = d.execute("""SELECT id FROM usages WHERE vehicle_id=? AND status IN ('Solicitada','Aprovada')
          AND return_at IS NULL AND (?='Diária' OR usage_mode!='Diária')
          AND departure < ? AND COALESCE(planned_return,return_at,'9999-12-31T23:59') > ? LIMIT 1""",
          (vehicle_id, 'Diária' if daily_mode else 'Avulsa', planned_return or '9999-12-31T23:59', departure)).fetchone()
        daily_only_available = bool(daily_owner and not daily_mode)
        vehicle_ok = bool(v and (v["status"] == "Disponível" or daily_only_available))
        if not vehicle_ok: flash("Veículo não está disponível para uma nova utilização.","error")
        elif not employee or not employee["active"]: flash("Colaborador inválido ou inativo.", "error")
        elif km < (v["current_km"] or 0): flash("A quilometragem de saída não pode ser menor que a atual.", "error")
        elif active_other: flash("Este veículo já possui outra utilização ativa. O compartilhamento só é permitido sobre a responsabilidade diária.", "error")
        else:
            try:
                departure_photos = save_required_inspection_photos("saida")
            except ValueError as exc:
                flash(str(exc), "error")
                return redirect(url_for("daily_usage" if daily_mode else "usage"))
            # Técnico pode retirar diretamente quando o veículo está disponível.
            usage_status = "Aprovada"
            d.execute("""INSERT INTO usages(vehicle_id,employee_id,departure,planned_return,destination,km_out,notes,created_by,status,departure_photos,departure_lat,departure_lon,departure_confirmed_by,departure_confirmed_at,usage_mode)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(vehicle_id,employee_id,departure,planned_return,
              request.form.get("destination"),km,request.form.get("notes"),session["user_id"],usage_status,departure_photos,
              request.form.get("gps_lat") or None, request.form.get("gps_lon") or None,session.get("full_name"),datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),"Diária" if daily_mode else "Avulsa"))
            usage_id=d.execute("SELECT last_insert_rowid()").fetchone()[0]
            for item_name, item_status, item_notes in equipment_answers:
                d.execute("INSERT INTO usage_equipment_checks(usage_id,item_name,status,notes) VALUES(?,?,?,?)",
                          (usage_id, item_name, item_status, item_notes))
            d.execute("UPDATE vehicles SET status='Em uso' WHERE id=?",(vehicle_id,))
            if not daily_mode and daily_owner:
                owner_users = d.execute("SELECT id FROM users WHERE employee_id=? AND active=1", (daily_owner["employee_id"],)).fetchall()
                notice = f"{employee['name']} registrou utilização do veículo {v['plate']}, que está sob sua responsabilidade diária."
                for owner_user in owner_users:
                    if int(owner_user["id"]) != int(session["user_id"]):
                        notify_user(owner_user["id"], "Utilização do seu veículo", notice, url_for("daily_usage"))
                notify_roles("Uso compartilhado de veículo", notice, url_for("daily_usage"), ("admin", "fleet"))
            elif session.get("role") in ("admin", "fleet"):
                tech_users = d.execute("SELECT id FROM users WHERE employee_id=? AND active=1", (employee_id,)).fetchall()
                for tech_user in tech_users:
                    notify_user(tech_user["id"], "Utilização registrada", f"Uma utilização do veículo {v['plate']} foi registrada em seu nome.", url_for("usage"))
            audit("Retirada com vistoria fotográfica", "utilização", usage_id, f"Veículo {v['plate']}; KM {km:g}; técnico {employee['name']}")
            d.commit()
            flash("Retirada registrada. Veículo vinculado a você.","success")
        return redirect(url_for("daily_usage" if daily_mode else "usage"))
    if return_only:
        if session.get("role")=="employee":
            if session.get("employee_id"):
                rows=d.execute("""SELECT u.*,v.plate,v.model,e.name employee,e.photo employee_photo FROM usages u JOIN vehicles v ON v.id=u.vehicle_id
                  JOIN employees e ON e.id=u.employee_id WHERE u.employee_id=? AND u.status='Aprovada' AND u.return_at IS NULL ORDER BY u.id DESC LIMIT 100""",
                  (session["employee_id"],)).fetchall()
            else:
                rows=[]
        else:
            rows=d.execute("""SELECT u.*,v.plate,v.model,e.name employee,e.photo employee_photo FROM usages u JOIN vehicles v ON v.id=u.vehicle_id
              JOIN employees e ON e.id=u.employee_id WHERE u.status='Aprovada' AND u.return_at IS NULL ORDER BY u.id DESC LIMIT 100""").fetchall()
    elif session.get("role")=="employee":
        if session.get("employee_id"):
            rows=d.execute("""SELECT u.*,v.plate,v.model,e.name employee,e.photo employee_photo FROM usages u JOIN vehicles v ON v.id=u.vehicle_id
              JOIN employees e ON e.id=u.employee_id WHERE u.employee_id=? AND COALESCE(u.usage_mode,'Avulsa')=? ORDER BY u.id DESC LIMIT 100""", (session["employee_id"], "Diária" if daily_mode else "Avulsa")).fetchall()
        else:
            rows=[]
    else:
        rows=d.execute("""SELECT u.*,v.plate,v.model,e.name employee,e.photo employee_photo FROM usages u JOIN vehicles v ON v.id=u.vehicle_id
          JOIN employees e ON e.id=u.employee_id WHERE COALESCE(u.usage_mode,'Avulsa')=? ORDER BY u.id DESC LIMIT 100""", ("Diária" if daily_mode else "Avulsa",)).fetchall()
    if return_only:
        vs=[]
    elif daily_mode:
        vs=d.execute("SELECT id,plate,model FROM vehicles WHERE status='Disponível' ORDER BY plate").fetchall()
    else:
        vs=d.execute("""SELECT v.id,v.plate,v.model FROM vehicles v
          WHERE v.status='Disponível' OR (v.status='Em uso' AND EXISTS(
            SELECT 1 FROM usages du WHERE du.vehicle_id=v.id AND du.usage_mode='Diária'
            AND du.status='Aprovada' AND du.return_at IS NULL)
            AND NOT EXISTS(SELECT 1 FROM usages au WHERE au.vehicle_id=v.id AND COALESCE(au.usage_mode,'Avulsa')!='Diária'
            AND au.status IN ('Solicitada','Aprovada') AND au.return_at IS NULL)) ORDER BY v.plate""").fetchall()
    if return_only:
        es=[]
    elif session.get("role")=="employee":
        if session.get("employee_id"):
            es=d.execute("SELECT id,name FROM employees WHERE id=? AND active=1", (session["employee_id"],)).fetchall()
        else:
            es=[]
    else:
        es=d.execute("SELECT id,name FROM employees WHERE active=1 ORDER BY name").fetchall()
    equipment_items = [] if return_only else d.execute("SELECT id,name FROM equipment_items WHERE active=1 ORDER BY name COLLATE NOCASE").fetchall()
    rows = [dict(r) | {"equipment_checks": d.execute("SELECT item_name,status,notes FROM usage_equipment_checks WHERE usage_id=? ORDER BY id", (r["id"],)).fetchall()} for r in rows]
    page_title = "Devoluções pendentes" if return_only else ("Responsabilidade diária" if daily_mode else "Utilização")
    return render_template("list.html",title=page_title,section="usage",rows=rows,vehicles=vs,employees=es,equipment_items=equipment_items,daily_mode=daily_mode,return_only=return_only)

@app.post("/usage/<int:item_id>/approve")
@roles("admin", "fleet")
def usage_approve(item_id):
    d=db(); u=d.execute("SELECT * FROM usages WHERE id=?", (item_id,)).fetchone()
    if not u: abort(404)
    v=d.execute("SELECT status FROM vehicles WHERE id=?", (u["vehicle_id"],)).fetchone()
    if u["status"] != "Solicitada":
        flash("Esta solicitação já foi processada.", "error")
    elif not v or v["status"] != "Disponível":
        flash("O veículo não está mais disponível.", "error")
    elif d.execute("SELECT id FROM usages WHERE vehicle_id=? AND id!=? AND status='Aprovada' AND return_at IS NULL AND departure < ? AND COALESCE(planned_return,return_at,'9999-12-31T23:59') > ? LIMIT 1", (u["vehicle_id"], item_id, u["planned_return"] or '9999-12-31T23:59', u["departure"])).fetchone():
        flash("Há conflito de agenda. Revise a previsão de devolução antes de aprovar.", "error")
    else:
        d.execute("UPDATE usages SET status='Aprovada' WHERE id=?", (item_id,))
        d.execute("UPDATE vehicles SET status='Em uso' WHERE id=?", (u["vehicle_id"],))
        audit("Aprovação", "solicitação de utilização", item_id)
        user_row=d.execute("SELECT created_by FROM usages WHERE id=?", (item_id,)).fetchone()
        notify_user(user_row["created_by"], "Solicitação aprovada", f"Sua solicitação do veículo foi aprovada.", url_for("usage"))
        d.commit()
        flash("Solicitação aprovada.", "success")
    return redirect(url_for("usage"))

@app.post("/usage/<int:item_id>/reject")
@roles("admin", "fleet")
def usage_reject(item_id):
    d=db(); u=d.execute("SELECT * FROM usages WHERE id=?", (item_id,)).fetchone()
    if not u: abort(404)
    if u["status"] != "Solicitada":
        flash("Esta solicitação já foi processada.", "error")
    else:
        d.execute("UPDATE usages SET status='Recusada' WHERE id=?", (item_id,))
        audit("Recusa", "solicitação de utilização", item_id)
        notify_user(u["created_by"], "Solicitação recusada", "Sua solicitação de utilização foi recusada.", url_for("usage"))
        d.commit()
        flash("Solicitação recusada.", "success")
    return redirect(url_for("usage"))

@app.post("/usage/<int:item_id>/return")
@login_required
def usage_return(item_id):
    d=db(); u=d.execute("SELECT * FROM usages WHERE id=? AND return_at IS NULL",(item_id,)).fetchone()
    if not u: abort(404)
    if u["status"] == "Solicitada": abort(403)
    if session.get("role")=="employee" and (not session.get("employee_id") or int(session["employee_id"])!=int(u["employee_id"])): abort(403)
    km=float(request.form.get("km_in") or 0)
    v=d.execute("SELECT current_km FROM vehicles WHERE id=?",(u["vehicle_id"],)).fetchone()
    if km < max(float(u["km_out"]),float(v["current_km"] or 0)):
        flash("A quilometragem de retorno deve ser igual ou superior à saída e à quilometragem atual.","error")
    else:
        shared_daily_owner = None
        if u["usage_mode"] != "Diária":
            shared_daily_owner = d.execute("""SELECT du.employee_id,e.name FROM usages du JOIN employees e ON e.id=du.employee_id
                WHERE du.vehicle_id=? AND du.usage_mode='Diária' AND du.status='Aprovada' AND du.return_at IS NULL
                ORDER BY du.id DESC LIMIT 1""", (u["vehicle_id"],)).fetchone()
        try:
            return_photos = save_required_inspection_photos("devolucao")
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("daily_usage" if u["usage_mode"]=="Diária" else "usage"))
        d.execute("UPDATE usages SET return_at=?,km_in=?,return_notes=?,return_photos=?,return_lat=?,return_lon=?,return_confirmed_by=?,return_confirmed_at=? WHERE id=?",
            (datetime.now().strftime("%Y-%m-%dT%H:%M"),km,request.form.get("return_notes", "").strip(),return_photos,
             request.form.get("gps_lat") or None, request.form.get("gps_lon") or None,session.get("full_name"),datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),item_id))
        remaining_daily = d.execute("SELECT id FROM usages WHERE vehicle_id=? AND usage_mode='Diária' AND status='Aprovada' AND return_at IS NULL AND id!=? LIMIT 1", (u["vehicle_id"], item_id)).fetchone()
        d.execute("UPDATE vehicles SET current_km=?,status=? WHERE id=?",(km, "Em uso" if remaining_daily else "Disponível", u["vehicle_id"]))
        if shared_daily_owner:
            owner_user = d.execute("SELECT id FROM users WHERE employee_id=? AND active=1", (shared_daily_owner["employee_id"],)).fetchone()
            notice = f"A utilização compartilhada do veículo foi encerrada. O veículo voltou para a responsabilidade diária de {shared_daily_owner['name']}."
            if owner_user: notify_user(owner_user["id"], "Veículo devolvido", notice, url_for("daily_usage"))
            notify_roles("Veículo devolvido", notice, url_for("daily_usage"), ("admin", "fleet"))
        audit("Devolução com vistoria fotográfica", "utilização", item_id, f"KM retorno {km:g}")
        d.commit(); flash("Devolução registrada com as seis fotos. Veículo disponível.","success")
    return redirect(url_for("daily_usage" if u["usage_mode"]=="Diária" else "usage"))

@app.route("/usage/<int:item_id>/edit", methods=["GET","POST"])
@roles("admin")
def usage_edit(item_id):
    d=db(); item=d.execute("SELECT * FROM usages WHERE id=?", (item_id,)).fetchone()
    if not item: abort(404)
    if request.method == "POST":
        km_out=float(request.form["km_out"])
        if item["km_in"] is not None and float(item["km_in"]) < km_out:
            flash("A KM de saída não pode superar a KM de retorno registrada.", "error")
        else:
            d.execute("UPDATE usages SET departure=?,destination=?,km_out=?,notes=? WHERE id=?",
                (request.form["departure"],request.form.get("destination"),km_out,request.form.get("notes"),item_id))
            audit("Edição", "utilização", item_id, "Dados originais da saída editados pelo administrador"); d.commit()
            flash("Registro de saída atualizado.", "success"); return redirect(url_for("usage"))
    return render_template("usage_edit.html", item=item)

@app.route("/fuel", methods=["GET","POST"])
@login_required
def fuel():
    if session.get("role") not in ("admin", "fleet", "employee"):
        abort(403)
    d=db()
    if request.method=="POST":
        receipt=None; f=request.files.get("receipt")
        vehicle_id=int(request.form["vehicle_id"]); fuel_km=float(request.form["km"]); liters=float(request.form["liters"]); amount=float(request.form["amount"])
        if liters <= 0 or amount < 0 or fuel_km < 0:
            flash("Informe quilometragem válida, litros maiores que zero e valor não negativo. Os demais dados foram mantidos.", "error")
            rows=d.execute("SELECT f.*,v.plate FROM fuel f JOIN vehicles v ON v.id=f.vehicle_id ORDER BY f.date DESC,f.id DESC LIMIT 100").fetchall()
            vs=d.execute("SELECT id,plate FROM vehicles WHERE status != 'Inativo' ORDER BY plate").fetchall()
            return render_template("list.html",title="Abastecimentos",section="fuel",rows=rows,vehicles=vs,form_data=request.form)
        if f and f.filename:
            name=secure_filename(f.filename); receipt=f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{name}"
            f.save(os.path.join(UPLOAD_DIR,receipt))
        d.execute("""INSERT INTO fuel(vehicle_id,date,km,liters,amount,fuel_type,station,receipt,notes,created_by)
          VALUES(?,?,?,?,?,?,?,?,?,?)""",(vehicle_id,request.form["date"],fuel_km,
          liters,amount,request.form.get("fuel_type"),request.form.get("station"),receipt,request.form.get("notes"),session["user_id"]))
        d.execute("UPDATE vehicles SET current_km=CASE WHEN COALESCE(current_km,0) < ? THEN ? ELSE COALESCE(current_km,0) END WHERE id=?", (fuel_km,fuel_km,vehicle_id))
        fuel_id = d.execute("SELECT last_insert_rowid()").fetchone()[0]
        audit("Cadastro", "abastecimento", fuel_id, f"Valor R$ {amount:.2f}; {request.form.get('notes','')}")
        d.commit(); flash("Abastecimento registrado.","success"); return redirect(url_for("fuel"))
    if session.get("role")=="employee":
        rows=d.execute("SELECT f.*,v.plate FROM fuel f JOIN vehicles v ON v.id=f.vehicle_id WHERE f.created_by=? ORDER BY f.date DESC,f.id DESC", (session["user_id"],)).fetchall()
    else:
        rows=d.execute("SELECT f.*,v.plate FROM fuel f JOIN vehicles v ON v.id=f.vehicle_id ORDER BY f.date DESC,f.id DESC").fetchall()
    vs=d.execute("SELECT id,plate FROM vehicles WHERE status != 'Inativo' ORDER BY plate").fetchall()
    return render_template("list.html",title="Abastecimentos",section="fuel",rows=rows,vehicles=vs)

@app.route("/tolls", methods=["GET", "POST"])
@login_required
def tolls():
    if session.get("role") not in ("admin", "fleet", "employee"):
        abort(403)
    d = db()
    if request.method == "POST":
        vehicle_id = int(request.form["vehicle_id"])
        amount = float(request.form.get("amount") or 0)
        plaza = (request.form.get("plaza") or "").strip()
        if amount < 0 or not plaza:
            flash("Informe a praça de pedágio e um valor válido.", "error")
            return redirect(url_for("tolls"))
        receipt = None
        f = request.files.get("receipt")
        if f and f.filename:
            name = secure_filename(f.filename)
            receipt = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{name}"
            f.save(os.path.join(UPLOAD_DIR, receipt))
        d.execute("""INSERT INTO tolls(vehicle_id,date,plaza,direction,amount,payment_method,receipt,notes,created_by)
          VALUES(?,?,?,?,?,?,?,?,?)""", (vehicle_id, request.form["date"], plaza,
          request.form.get("direction"), amount, request.form.get("payment_method"), receipt,
          request.form.get("notes"), session["user_id"]))
        toll_id = d.execute("SELECT last_insert_rowid()").fetchone()[0]
        audit("Cadastro", "pedágio", toll_id, f"Veículo {vehicle_id}; praça {plaza}; valor R$ {amount:.2f}")
        d.commit()
        flash("Despesa de pedágio registrada.", "success")
        return redirect(url_for("tolls"))
    if session.get("role") == "employee":
        rows = d.execute("SELECT t.*,v.plate FROM tolls t JOIN vehicles v ON v.id=t.vehicle_id WHERE t.created_by=? ORDER BY t.date DESC,t.id DESC", (session["user_id"],)).fetchall()
    else:
        rows = d.execute("SELECT t.*,v.plate FROM tolls t JOIN vehicles v ON v.id=t.vehicle_id ORDER BY t.date DESC,t.id DESC").fetchall()
    vs = d.execute("SELECT id,plate FROM vehicles WHERE status != 'Inativo' ORDER BY plate").fetchall()
    return render_template("list.html", title="Despesas com pedágios", section="tolls", rows=rows, vehicles=vs)

@app.route("/maintenance", methods=["GET","POST"])
@roles("admin","fleet")
def maintenance():
    d=db()
    if request.method=="POST":
        d.execute("""INSERT INTO maintenance(vehicle_id,kind,date,km,description,workshop,amount,next_date,next_km,status)
          VALUES(?,?,?,?,?,?,?,?,?,?)""",(int(request.form["vehicle_id"]),request.form["kind"],request.form["date"],
          float(request.form["km"]),request.form.get("description"),request.form.get("workshop"),
          float(request.form.get("amount") or 0),request.form.get("next_date") or None,
          float(request.form["next_km"]) if request.form.get("next_km") else None,request.form.get("status","Concluída")))
        if request.form.get("status")=="Em andamento":
            d.execute("UPDATE vehicles SET status='Em manutenção' WHERE id=?",(int(request.form["vehicle_id"]),))
        elif request.form.get("status")=="Concluída":
            d.execute("UPDATE vehicles SET status='Disponível' WHERE id=? AND status='Em manutenção' AND NOT EXISTS (SELECT 1 FROM maintenance WHERE vehicle_id=? AND status='Em andamento')",(int(request.form["vehicle_id"]),int(request.form["vehicle_id"])))
        audit("Cadastro", "manutenção", d.execute("SELECT last_insert_rowid()").fetchone()[0], f"Valor R$ {float(request.form.get('amount') or 0):.2f}"); d.commit(); flash("Manutenção registrada.","success"); return redirect(url_for("maintenance"))
    rows=d.execute("SELECT m.*,v.plate FROM maintenance m JOIN vehicles v ON v.id=m.vehicle_id ORDER BY m.date DESC").fetchall()
    vs=d.execute("SELECT id,plate FROM vehicles ORDER BY plate").fetchall()
    return render_template("list.html",title="Manutenções",section="maintenance",rows=rows,vehicles=vs)

@app.route("/documents", methods=["GET","POST"])
@roles("admin","fleet")
def documents():
    d=db()
    if request.method=="POST":
        d.execute("INSERT INTO documents(vehicle_id,kind,due_date,status,notes) VALUES(?,?,?,?,?)",
          (int(request.form["vehicle_id"]),request.form["kind"],request.form["due_date"],request.form.get("status","Vigente"),request.form.get("notes")))
        audit("Cadastro", "documento", d.execute("SELECT last_insert_rowid()").fetchone()[0], request.form.get("kind")); d.commit(); flash("Documento cadastrado.","success"); return redirect(url_for("documents"))
    rows=d.execute("SELECT x.*,v.plate FROM documents x JOIN vehicles v ON v.id=x.vehicle_id ORDER BY x.due_date").fetchall()
    vs=d.execute("SELECT id,plate FROM vehicles ORDER BY plate").fetchall()
    return render_template("list.html",title="Documentos e vencimentos",section="documents",rows=rows,vehicles=vs)


@app.route("/fines", methods=["GET","POST"])
@roles("admin","fleet")
def fines():
    d=db()
    if request.method=="POST":
        d.execute("""INSERT INTO fines(vehicle_id,employee_id,date,description,amount,status,due_date,notes)
          VALUES(?,?,?,?,?,?,?,?)""",(int(request.form["vehicle_id"]),
          int(request.form["employee_id"]) if request.form.get("employee_id") else None,
          request.form["date"],request.form["description"],float(request.form.get("amount") or 0),
          request.form.get("status","Pendente"),request.form.get("due_date") or None,request.form.get("notes")))
        audit("Cadastro", "multa", d.execute("SELECT last_insert_rowid()").fetchone()[0], f"Valor R$ {float(request.form.get('amount') or 0):.2f}; {request.form['description']}"); d.commit(); flash("Multa/infração registrada.","success"); return redirect(url_for("fines"))
    rows=d.execute("""SELECT f.*,v.plate,e.name employee FROM fines f JOIN vehicles v ON v.id=f.vehicle_id
      LEFT JOIN employees e ON e.id=f.employee_id ORDER BY f.date DESC,f.id DESC""").fetchall()
    vs=d.execute("SELECT id,plate FROM vehicles ORDER BY plate").fetchall()
    es=d.execute("SELECT id,name FROM employees WHERE active=1 ORDER BY name").fetchall()
    return render_template("list.html",title="Multas e infrações",section="fines",rows=rows,vehicles=vs,employees=es)

@app.post("/fines/<int:item_id>/status")
@roles("admin","fleet")
def fine_status(item_id):
    status=request.form.get("status","Pendente")
    if status not in ("Pendente","Em contestação","Paga","Cancelada"): abort(400)
    db().execute("UPDATE fines SET status=? WHERE id=?",(status,item_id)); audit("Alteração de status", "multa", item_id, status); db().commit()
    flash("Situação da multa atualizada.","success"); return redirect(url_for("fines"))

@app.get("/service-worker.js")
def service_worker():
    response = send_from_directory(os.path.join(BASE_DIR, "static"), "sw.js", mimetype="application/javascript")
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"
    return response

@app.get("/tracking")
@login_required
def tracking_page():
    if session.get("role") == "employee":
        active_use = db().execute("""SELECT u.id usage_id,v.plate,v.model FROM usages u JOIN vehicles v ON v.id=u.vehicle_id
            WHERE u.employee_id=? AND u.status='Aprovada' AND u.return_at IS NULL ORDER BY u.id DESC LIMIT 1""",
            (session.get("employee_id"),)).fetchone()
        state = db().execute("SELECT enabled FROM tracking_sessions WHERE user_id=?", (session["user_id"],)).fetchone()
        return render_template("tracking_technician.html", active_use=active_use, tracking_enabled=bool(state and state["enabled"]))
    if session.get("role") not in ("admin", "fleet"):
        abort(403)
    if session.get("role") == "fleet" and "tracking" not in user_permissions():
        abort(403)
    return render_template("tracking_admin.html")

@app.post("/api/tracking/consent")
@login_required
def tracking_consent():
    if session.get("role") != "employee" or not session.get("employee_id"):
        return jsonify(ok=False, error="Recurso disponível somente para técnicos."), 403
    active_use = db().execute("SELECT id FROM usages WHERE employee_id=? AND status='Aprovada' AND return_at IS NULL ORDER BY id DESC LIMIT 1",
                              (session["employee_id"],)).fetchone()
    if not active_use:
        return jsonify(ok=False, error="Para compartilhar a localização, primeiro registre a retirada de um veículo."), 409
    payload = request.get_json(silent=True) or {}
    enabled = bool(payload.get("enabled"))
    d=db()
    if enabled:
        d.execute("""INSERT INTO tracking_sessions(user_id,enabled,consent_at,stopped_at,updated_at) VALUES(?,1,?,NULL,?)
            ON CONFLICT(user_id) DO UPDATE SET enabled=1,consent_at=excluded.consent_at,stopped_at=NULL,updated_at=excluded.updated_at""",
            (session["user_id"],datetime.now().isoformat(timespec="seconds"),datetime.now().isoformat(timespec="seconds")))
        audit("Consentimento de localização", "rastreamento", session["user_id"], "Técnico ativou o compartilhamento de localização")
    else:
        d.execute("UPDATE tracking_sessions SET enabled=0,stopped_at=?,updated_at=? WHERE user_id=?",
                  (datetime.now().isoformat(timespec="seconds"),datetime.now().isoformat(timespec="seconds"),session["user_id"]))
        audit("Localização desativada", "rastreamento", session["user_id"], "Técnico interrompeu o compartilhamento")
    d.commit()
    return jsonify(ok=True, enabled=enabled)

@app.post("/api/tracking/update")
@login_required
def tracking_update():
    if session.get("role") != "employee" or not session.get("employee_id"):
        return jsonify(ok=False, error="Acesso não permitido."), 403
    d=db()
    state=d.execute("SELECT enabled FROM tracking_sessions WHERE user_id=?",(session["user_id"],)).fetchone()
    if not state or not state["enabled"]:
        return jsonify(ok=False,error="Compartilhamento de localização desativado."),403
    active=d.execute("""SELECT u.id usage_id,u.vehicle_id FROM usages u WHERE u.employee_id=? AND u.status='Aprovada'
        AND u.return_at IS NULL ORDER BY u.id DESC LIMIT 1""",(session["employee_id"],)).fetchone()
    if not active:
        d.execute("UPDATE tracking_sessions SET enabled=0,stopped_at=?,updated_at=? WHERE user_id=?",
                   (datetime.now().isoformat(timespec="seconds"),datetime.now().isoformat(timespec="seconds"),session["user_id"]))
        d.commit()
        return jsonify(ok=False,error="Não há veículo em utilização. Rastreamento encerrado."),409
    try:
        data=request.get_json(force=True)
        lat=float(data.get("latitude")); lon=float(data.get("longitude"))
        if not (-90<=lat<=90 and -180<=lon<=180): raise ValueError()
        accuracy=float(data.get("accuracy") or 0)
        speed=float(data.get("speed") or 0) if data.get("speed") is not None else None
        heading=float(data.get("heading") or 0) if data.get("heading") is not None else None
    except (TypeError,ValueError,AttributeError):
        return jsonify(ok=False,error="Coordenadas inválidas."),400
    now=datetime.now().isoformat(timespec="seconds")
    d.execute("INSERT INTO tracking_points(user_id,employee_id,usage_id,vehicle_id,latitude,longitude,accuracy,speed,heading,recorded_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
              (session["user_id"],session["employee_id"],active["usage_id"],active["vehicle_id"],lat,lon,accuracy,speed,heading,now))
    d.execute("UPDATE tracking_sessions SET updated_at=? WHERE user_id=?",(now,session["user_id"]))
    # Mantém os 500 pontos mais recentes por técnico para controlar o crescimento do banco.
    d.execute("DELETE FROM tracking_points WHERE user_id=? AND id NOT IN (SELECT id FROM tracking_points WHERE user_id=? ORDER BY id DESC LIMIT 500)",(session["user_id"],session["user_id"]))
    d.commit()
    return jsonify(ok=True,recorded_at=now)

@app.get("/api/tracking/status")
@login_required
def tracking_status():
    if session.get("role") not in ("admin","fleet"):
        abort(403)
    rows=db().execute("""SELECT p.user_id,p.employee_id,e.name technician,v.id vehicle_id,v.plate,v.model,
        p.latitude,p.longitude,p.accuracy,p.speed,p.heading,p.recorded_at,ts.enabled
        FROM tracking_points p JOIN employees e ON e.id=p.employee_id JOIN vehicles v ON v.id=p.vehicle_id
        JOIN tracking_sessions ts ON ts.user_id=p.user_id
        WHERE p.id=(SELECT MAX(p2.id) FROM tracking_points p2 WHERE p2.user_id=p.user_id)
        ORDER BY p.recorded_at DESC""").fetchall()
    now=datetime.now()
    items=[]
    for r in rows:
        item=dict(r)
        try:
            age=(now-datetime.fromisoformat(item["recorded_at"])).total_seconds()
        except Exception: age=999999
        item["status"]="Em tempo real" if item["enabled"] and age<=90 else ("Sem atualização" if item["enabled"] else "Compartilhamento pausado")
        item["age_seconds"]=int(max(0,age))
        items.append(item)
    return jsonify(items=items,server_time=now.isoformat(timespec="seconds"))

@app.get("/api/tracking/history/<int:user_id>")
@login_required
def tracking_history(user_id):
    if session.get("role") not in ("admin","fleet"):
        abort(403)
    rows=db().execute("""SELECT p.latitude,p.longitude,p.recorded_at,p.speed,p.accuracy,v.plate FROM tracking_points p
        JOIN vehicles v ON v.id=p.vehicle_id WHERE p.user_id=? ORDER BY p.id DESC LIMIT 300""",(user_id,)).fetchall()
    return jsonify(points=[dict(r) for r in reversed(rows)])

@app.get("/notifications")
@roles("admin", "fleet")
def notifications_page():
    d=db()
    rows=d.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 200", (session["user_id"],)).fetchall()
    d.execute("UPDATE notifications SET read_at=CURRENT_TIMESTAMP WHERE user_id=? AND read_at IS NULL", (session["user_id"],))
    d.commit()
    return render_template("notifications.html", title="Notificações", rows=rows)

@app.post("/notifications/<int:item_id>/read")
@login_required
def notification_read(item_id):
    d=db(); d.execute("UPDATE notifications SET read_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?", (item_id, session["user_id"]))
    d.commit(); return redirect(url_for("notifications_page"))

@app.get("/vehicles/<int:item_id>/history")
@login_required
def vehicle_history(item_id):
    d=db(); v=d.execute("SELECT * FROM vehicles WHERE id=?",(item_id,)).fetchone()
    if not v: abort(404)
    uses=d.execute("""SELECT u.*,e.name employee FROM usages u JOIN employees e ON e.id=u.employee_id
      WHERE u.vehicle_id=? ORDER BY u.id DESC""",(item_id,)).fetchall()
    fuels=d.execute("SELECT * FROM fuel WHERE vehicle_id=? ORDER BY date DESC,id DESC",(item_id,)).fetchall()
    maint=d.execute("SELECT * FROM maintenance WHERE vehicle_id=? ORDER BY date DESC,id DESC",(item_id,)).fetchall()
    docs=d.execute("SELECT * FROM documents WHERE vehicle_id=? ORDER BY due_date",(item_id,)).fetchall()
    fines_rows=d.execute("SELECT f.*,e.name employee FROM fines f LEFT JOIN employees e ON e.id=f.employee_id WHERE f.vehicle_id=? ORDER BY f.date DESC",(item_id,)).fetchall()
    inspections_rows=d.execute("SELECT i.*,e.name employee FROM inspections i LEFT JOIN employees e ON e.id=i.employee_id WHERE i.vehicle_id=? ORDER BY i.date DESC,i.id DESC",(item_id,)).fetchall()
    return render_template("vehicle_history.html",v=v,uses=uses,fuels=fuels,maint=maint,docs=docs,fines=fines_rows,inspections=inspections_rows)

@app.get("/reports/advanced")
@roles("admin","fleet")
def advanced_reports():
    d=db()
    employees=d.execute("""SELECT e.id,e.name,COUNT(u.id) trips,
      COALESCE(SUM(CASE WHEN u.km_in IS NOT NULL THEN u.km_in-u.km_out ELSE 0 END),0) km
      FROM employees e LEFT JOIN usages u ON u.employee_id=e.id GROUP BY e.id ORDER BY e.name""").fetchall()
    vehicles=d.execute("""SELECT v.id,v.plate,v.brand,v.model,v.current_km,
      COALESCE((SELECT SUM(u.km_in-u.km_out) FROM usages u WHERE u.vehicle_id=v.id AND u.km_in IS NOT NULL),0) km_traveled,
      COALESCE((SELECT SUM(f.liters) FROM fuel f WHERE f.vehicle_id=v.id),0) liters,
      COALESCE((SELECT SUM(f.amount) FROM fuel f WHERE f.vehicle_id=v.id),0) fuel_cost,
      COALESCE((SELECT SUM(m.amount) FROM maintenance m WHERE m.vehicle_id=v.id),0) maintenance_cost,
      COALESCE((SELECT SUM(x.amount) FROM fines x WHERE x.vehicle_id=v.id AND x.status!='Cancelada'),0) fines_cost,
      COALESCE((SELECT SUM(t.amount) FROM tolls t WHERE t.vehicle_id=v.id),0) toll_cost
      FROM vehicles v ORDER BY v.plate""").fetchall()
    return render_template("advanced_reports.html",employees=employees,vehicles=vehicles)

@app.get("/reports/pdf")
@roles("admin","fleet")
def reports_pdf():
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.units import mm
    d=db(); start=request.args.get("start", "").strip(); end=request.args.get("end", "").strip()
    date_params=[start,start,end,end]
    rows=d.execute("""SELECT v.plate,v.brand,v.model,v.current_km,v.status,
      COALESCE((SELECT SUM(amount) FROM fuel f WHERE f.vehicle_id=v.id AND (?='' OR f.date>=?) AND (?='' OR f.date<=?)),0) fuel_total,
      COALESCE((SELECT SUM(amount) FROM maintenance m WHERE m.vehicle_id=v.id AND (?='' OR m.date>=?) AND (?='' OR m.date<=?)),0) maintenance_total,
      COALESCE((SELECT SUM(amount) FROM fines x WHERE x.vehicle_id=v.id AND x.status!='Cancelada' AND (?='' OR x.date>=?) AND (?='' OR x.date<=?)),0) fines_total,
      COALESCE((SELECT SUM(amount) FROM tolls t WHERE t.vehicle_id=v.id AND (?='' OR t.date>=?) AND (?='' OR t.date<=?)),0) toll_total,
      COALESCE((SELECT SUM(u.km_in-u.km_out) FROM usages u WHERE u.vehicle_id=v.id AND u.km_in IS NOT NULL AND (?='' OR substr(u.departure,1,10)>=?) AND (?='' OR substr(u.departure,1,10)<=?)),0) km_traveled,
      COALESCE((SELECT SUM(liters) FROM fuel f WHERE f.vehicle_id=v.id AND (?='' OR f.date>=?) AND (?='' OR f.date<=?)),0) liters
      FROM vehicles v ORDER BY v.plate""", date_params*6).fetchall()
    buf=io.BytesIO()
    doc=SimpleDocTemplate(buf,pagesize=landscape(A4),rightMargin=12*mm,leftMargin=12*mm,topMargin=12*mm,bottomMargin=12*mm)
    styles=getSampleStyleSheet(); story=[Paragraph("SEC.W FROTA — Relatório gerencial",styles["Title"]),
      Paragraph("Resumo por veículo • Gerado em "+date.today().strftime("%d/%m/%Y"),styles["Normal"]),Spacer(1,8*mm)]
    data=[["Placa","Veículo","KM atual","KM rodados","Consumo km/l","Combustível","Manutenção","Multas","Pedágios","Total"]]
    for r in rows:
        kmpl=(r["km_traveled"]/r["liters"]) if r["liters"] else 0
        data.append([r["plate"],f'{r["brand"] or ""} {r["model"] or ""}',f'{r["current_km"]:.0f}',
          f'{r["km_traveled"]:.0f}',f'{kmpl:.2f}' if kmpl else "—",f'R$ {r["fuel_total"]:.2f}',
          f'R$ {r["maintenance_total"]:.2f}',f'R$ {r["fines_total"]:.2f}',f'R$ {r["toll_total"]:.2f}',f'R$ {r["fuel_total"]+r["maintenance_total"]+r["fines_total"]+r["toll_total"]:.2f}'])
    table=Table(data,repeatRows=1)
    table.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#0B3C68")),
      ("TEXTCOLOR",(0,0),(-1,0),colors.white),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
      ("GRID",(0,0),(-1,-1),.35,colors.HexColor("#D9E4EC")),("FONTSIZE",(0,0),(-1,-1),8),
      ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,colors.HexColor("#F2F7FA")]),("PADDING",(0,0),(-1,-1),6)]))
    story.append(table); doc.build(story); buf.seek(0)
    return send_file(buf,mimetype="application/pdf",as_attachment=True,download_name="relatorio_secw_frota.pdf")

@app.get("/reports")
@roles("admin","fleet")
def reports():
    d=db()
    start=request.args.get("start", "").strip(); end=request.args.get("end", "").strip()
    period=request.args.get("period", "").strip()
    today=date.today()
    if period=="week":
        start=(today-timedelta(days=today.weekday())).isoformat(); end=(today+timedelta(days=6-today.weekday())).isoformat()
    elif period=="month":
        start=today.replace(day=1).isoformat()
        next_month=(today.replace(day=28)+timedelta(days=4)).replace(day=1)
        end=(next_month-timedelta(days=1)).isoformat()
    vehicle_id=request.args.get("vehicle_id", "").strip()
    params=[start,start,end,end]*4
    sql="""SELECT v.id,v.plate,v.brand,v.model,v.current_km,v.status,
      COALESCE((SELECT SUM(amount) FROM fuel f WHERE f.vehicle_id=v.id AND (?='' OR f.date>=?) AND (?='' OR f.date<=?)),0) fuel_total,
      COALESCE((SELECT SUM(amount) FROM maintenance m WHERE m.vehicle_id=v.id AND (?='' OR m.date>=?) AND (?='' OR m.date<=?)),0) maintenance_total,
      COALESCE((SELECT SUM(amount) FROM fines x WHERE x.vehicle_id=v.id AND x.status!='Cancelada' AND (?='' OR x.date>=?) AND (?='' OR x.date<=?)),0) fines_total,
      COALESCE((SELECT SUM(amount) FROM tolls t WHERE t.vehicle_id=v.id AND (?='' OR t.date>=?) AND (?='' OR t.date<=?)),0) toll_total
      FROM vehicles v WHERE (?='' OR v.id=?) ORDER BY v.plate"""
    rows=d.execute(sql, params+[vehicle_id,vehicle_id]).fetchall()
    vehicles=d.execute("SELECT id,plate,brand,model FROM vehicles ORDER BY plate").fetchall()
    totals={key:sum(float(r[key] or 0) for r in rows) for key in ("fuel_total","maintenance_total","fines_total","toll_total")}
    totals["grand_total"]=sum(totals.values())
    return render_template("reports.html",rows=rows,start=start,end=end,period=period,vehicle_id=vehicle_id,vehicles=vehicles,totals=totals)

@app.get("/reports.csv")
@roles("admin","fleet")
def reports_csv():
    start=request.args.get("start", "").strip(); end=request.args.get("end", "").strip(); params=[start,start,end,end]*4
    rows=db().execute("""SELECT v.plate,v.brand,v.model,v.current_km,v.status,
      COALESCE((SELECT SUM(amount) FROM fuel f WHERE f.vehicle_id=v.id AND (?='' OR f.date>=?) AND (?='' OR f.date<=?)),0) fuel_total,
      COALESCE((SELECT SUM(amount) FROM maintenance m WHERE m.vehicle_id=v.id AND (?='' OR m.date>=?) AND (?='' OR m.date<=?)),0) maintenance_total,
      COALESCE((SELECT SUM(amount) FROM fines x WHERE x.vehicle_id=v.id AND x.status!='Cancelada' AND (?='' OR x.date>=?) AND (?='' OR x.date<=?)),0) fines_total,
      COALESCE((SELECT SUM(amount) FROM tolls t WHERE t.vehicle_id=v.id AND (?='' OR t.date>=?) AND (?='' OR t.date<=?)),0) toll_total
      FROM vehicles v ORDER BY v.plate""", params).fetchall()
    output=io.StringIO(); w=csv.writer(output,delimiter=';')
    w.writerow(["Placa","Marca","Modelo","KM atual","Situação","Combustível (R$)","Manutenção (R$)","Multas (R$)","Pedágios (R$)","Total (R$)"])
    for r in rows: w.writerow([r["plate"],r["brand"],r["model"],r["current_km"],r["status"],r["fuel_total"],r["maintenance_total"],r["fines_total"],r["toll_total"],round(r["fuel_total"]+r["maintenance_total"]+r["fines_total"]+r["toll_total"],2)])
    mem=io.BytesIO(("\ufeff"+output.getvalue()).encode("utf-8"))
    return send_file(mem,mimetype="text/csv",as_attachment=True,download_name="relatorio_secw_frota.csv")

@app.route("/permissions", methods=["GET", "POST"])
@roles("admin")
def permissions_page():
    d=db()
    if request.method == "POST":
        user_id=int(request.form.get("user_id", "0"))
        target=d.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not target:
            flash("Usuário não encontrado.", "error"); return redirect(url_for("permissions_page"))
        if target["role"] == "admin":
            flash("O administrador sempre mantém acesso completo.", "error"); return redirect(url_for("permissions_page"))
        selected=[key for key in PERMISSION_LABELS if request.form.get("perm_"+key)=="on"]
        d.execute("UPDATE users SET permissions=? WHERE id=?", (json.dumps(selected), user_id))
        audit("Permissões atualizadas", "usuário", user_id, ", ".join(selected) or "Nenhuma permissão")
        d.commit(); flash("Permissões atualizadas com sucesso.", "success"); return redirect(url_for("permissions_page", user_id=user_id))
    rows=d.execute("SELECT id,username,full_name,role,active,permissions FROM users ORDER BY full_name").fetchall()
    selected_id=request.args.get("user_id", type=int)
    chosen=next((r for r in rows if r["id"]==selected_id), None) if selected_id else next((r for r in rows if r["role"]!="admin"), rows[0] if rows else None)
    try: selected=json.loads(chosen["permissions"] or "[]") if chosen else []
    except (ValueError,TypeError): selected=DEFAULT_EMPLOYEE_PERMISSIONS if chosen and chosen["role"]=="employee" else DEFAULT_FLEET_PERMISSIONS
    return render_template("permissions.html", title="Permissões de acesso", users=rows, chosen=chosen, selected=selected, permission_labels=PERMISSION_LABELS)

@app.route("/users", methods=["GET","POST"])
@roles("admin")
def users():
    d=db()
    if request.method=="POST":
        try:
            if request.form["role"] == "employee" and not request.form.get("employee_id"):
                flash("Para criar um acesso de colaborador, vincule um colaborador cadastrado.", "error")
                return redirect(url_for("users"))
            d.execute("INSERT INTO users(username,password_hash,full_name,role,employee_id,permissions) VALUES(?,?,?,?,?,?)",
              (request.form["username"].strip(),generate_password_hash(request.form["password"]),request.form["full_name"].strip(),request.form["role"],int(request.form["employee_id"]) if request.form.get("employee_id") else None,json.dumps(DEFAULT_EMPLOYEE_PERMISSIONS if request.form["role"]=="employee" else DEFAULT_FLEET_PERMISSIONS)))
            audit("Cadastro", "usuário", d.execute("SELECT last_insert_rowid()").fetchone()[0], request.form["username"].strip()); d.commit(); flash("Usuário criado.","success")
        except INTEGRITY_ERRORS: flash("Esse nome de usuário já existe.","error")
        return redirect(url_for("users"))
    rows=d.execute("SELECT u.id,u.username,u.full_name,u.role,u.active,u.employee_id,e.name employee_name FROM users u LEFT JOIN employees e ON e.id=u.employee_id ORDER BY u.full_name").fetchall()
    employee_options=d.execute("SELECT id,name FROM employees WHERE active=1 ORDER BY name").fetchall()
    return render_template("list.html",title="Cadastro de técnicos e usuários",section="users",rows=rows,employees=employee_options)

@app.route("/technicians", methods=["GET", "POST"])
@roles("admin")
def technicians():
    """Cadastro direto de técnico: cria colaborador e login restrito em uma única ação."""
    d = db()
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        department = request.form.get("department", "Técnico de campo").strip() or "Técnico de campo"
        phone = request.form.get("phone", "").strip()
        document_number = request.form.get("document_number", "").strip()
        license_number = request.form.get("license_number", "").strip()
        license_category = request.form.get("license_category", "").strip()
        license_due = request.form.get("license_due") or None
        if not full_name or not username or len(password) < 8:
            flash("Preencha nome, usuário e senha com pelo menos 8 caracteres.", "error")
            return redirect(url_for("technicians"))
        if d.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone():
            flash("Esse nome de usuário já está em uso. Escolha outro.", "error")
            return redirect(url_for("technicians"))
        try:
            cur = d.execute("INSERT INTO employees(name,department,phone,document_number,license_number,license_category,license_due) VALUES(?,?,?,?,?,?,?)", (full_name, department, phone, document_number, license_number, license_category, license_due))
            employee_id = cur.lastrowid
            cur = d.execute("INSERT INTO users(username,password_hash,full_name,role,employee_id,permissions) VALUES(?,?,?,?,?,?)",
                (username, generate_password_hash(password), full_name, "employee", employee_id, json.dumps(DEFAULT_EMPLOYEE_PERMISSIONS)))
            user_id = cur.lastrowid
            audit("Cadastro", "técnico", user_id, f"{full_name} / usuário {username}")
            d.commit()
            flash("Técnico cadastrado! O login já está vinculado ao colaborador e tem acesso apenas a Utilização e Abastecimentos.", "success")
        except INTEGRITY_ERRORS:
            d.rollback()
            flash("Não foi possível cadastrar. Verifique se o usuário já existe.", "error")
        return redirect(url_for("technicians"))
    rows = d.execute("SELECT u.id,u.username,u.full_name,u.role,u.active,u.employee_id,e.name employee_name FROM users u LEFT JOIN employees e ON e.id=u.employee_id WHERE u.role='employee' ORDER BY u.full_name").fetchall()
    return render_template("technicians.html", title="Cadastro de técnicos", rows=rows)

@app.route("/technicians/<int:item_id>/edit", methods=["GET", "POST"])
@roles("admin")
def technician_edit(item_id):
    d = db()
    row = d.execute("""SELECT u.id AS user_id,u.username,u.full_name,u.active,
                              e.id AS employee_id,e.name,e.department,e.phone,
                              e.document_number,e.license_number,e.license_category,e.license_due
                       FROM users u LEFT JOIN employees e ON e.id=u.employee_id
                       WHERE u.id=? AND u.role='employee'""", (item_id,)).fetchone()
    if not row:
        flash("Técnico não encontrado.", "error")
        return redirect(url_for("technicians"))
    if request.method == "POST":
        full_name = (request.form.get("full_name") or "").strip()
        department = (request.form.get("department") or "Técnico de campo").strip()
        phone = (request.form.get("phone") or "").strip()
        document_number = (request.form.get("document_number") or "").strip()
        license_number = (request.form.get("license_number") or "").strip()
        license_category = (request.form.get("license_category") or "").strip()
        license_due = request.form.get("license_due") or None
        if not full_name:
            flash("Informe o nome completo do técnico.", "error")
            return redirect(url_for("technician_edit", item_id=item_id))
        try:
            if row["employee_id"]:
                d.execute("""UPDATE employees SET name=?,department=?,phone=?,document_number=?,
                             license_number=?,license_category=?,license_due=? WHERE id=?""",
                          (full_name,department,phone,document_number,license_number,license_category,license_due,row["employee_id"]))
            else:
                cur = d.execute("""INSERT INTO employees(name,department,phone,document_number,license_number,license_category,license_due)
                                   VALUES(?,?,?,?,?,?,?)""",
                                (full_name,department,phone,document_number,license_number,license_category,license_due))
                d.execute("UPDATE users SET employee_id=? WHERE id=?", (cur.lastrowid,item_id))
            d.execute("UPDATE users SET full_name=? WHERE id=?", (full_name,item_id))
            audit("Cadastro de técnico atualizado", "técnico", item_id, full_name)
            d.commit()
            flash("Cadastro do técnico atualizado com sucesso.", "success")
            return redirect(url_for("technicians"))
        except INTEGRITY_ERRORS:
            d.rollback()
            flash("Não foi possível atualizar o cadastro. Confira os dados.", "error")
            return redirect(url_for("technician_edit", item_id=item_id))
    return render_template("technician_edit.html", title="Editar técnico", item=row)

@app.post("/users/<int:item_id>/link-employee")
@roles("admin")
def user_link_employee(item_id):
    employee_id=int(request.form["employee_id"]) if request.form.get("employee_id") else None
    if employee_id is not None:
        employee=db().execute("SELECT id FROM employees WHERE id=? AND active=1", (employee_id,)).fetchone()
        if not employee:
            flash("Selecione um colaborador ativo.", "error")
            return redirect(url_for("users"))
    db().execute("UPDATE users SET employee_id=? WHERE id=?", (employee_id,item_id))
    audit("Vínculo de colaborador", "usuário", item_id, f"Colaborador ID {employee_id or 'removido'}"); db().commit()
    flash("Vínculo do usuário atualizado.", "success")
    return redirect(url_for("users"))

@app.post("/users/<int:item_id>/toggle")
@roles("admin")
def user_toggle(item_id):
    if item_id==session["user_id"]: flash("Você não pode desativar seu próprio acesso.","error")
    else:
        db().execute("UPDATE users SET active=1-active WHERE id=?",(item_id,)); audit("Alteração de acesso", "usuário", item_id); db().commit()
        flash("Acesso atualizado.","success")
    return redirect(url_for("users"))

@app.route("/equipment", methods=["GET", "POST"])
@roles("admin")
def equipment_management():
    d = db()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            name = (request.form.get("name") or "").strip()
            if not name:
                flash("Informe o nome do equipamento.", "error")
            elif len(name) > 100:
                flash("O nome deve ter até 100 caracteres.", "error")
            else:
                try:
                    d.execute("INSERT INTO equipment_items(name,active) VALUES(?,1)", (name,))
                    audit("Equipamento adicionado", "equipamento", None, name)
                    d.commit()
                    flash("Equipamento adicionado à lista.", "success")
                except INTEGRITY_ERRORS:
                    flash("Esse equipamento já está cadastrado.", "error")
        elif action == "rename":
            item_id = int(request.form.get("item_id", "0"))
            name = (request.form.get("name") or "").strip()
            if not name or len(name) > 100:
                flash("Informe um nome válido de até 100 caracteres.", "error")
            else:
                try:
                    d.execute("UPDATE equipment_items SET name=? WHERE id=?", (name, item_id))
                    audit("Equipamento renomeado", "equipamento", item_id, name)
                    d.commit()
                    flash("Nome do equipamento atualizado. Conferências anteriores foram preservadas.", "success")
                except INTEGRITY_ERRORS:
                    flash("Já existe um item com esse nome.", "error")
        elif action == "toggle":
            item_id = int(request.form.get("item_id", "0"))
            current = d.execute("SELECT active,name FROM equipment_items WHERE id=?", (item_id,)).fetchone()
            if current:
                new_active = 0 if current["active"] else 1
                d.execute("UPDATE equipment_items SET active=? WHERE id=?", (new_active, item_id))
                audit("Equipamento ativado" if new_active else "Equipamento desativado", "equipamento", item_id, current["name"])
                d.commit()
                flash("Item ativado na vistoria." if new_active else "Item removido da lista ativa. O histórico foi preservado.", "success")
        return redirect(url_for("equipment_management"))
    items = d.execute("SELECT * FROM equipment_items ORDER BY active DESC,name COLLATE NOCASE").fetchall()
    return render_template("equipment.html", title="Equipamentos dos veículos", items=items)


@app.route("/inspections", methods=["GET", "POST"])
@roles("admin", "fleet")
def inspections():
    d=db()
    if request.method == "POST":
        if session.get("role") == "employee":
            if not session.get("employee_id"):
                flash("Peça ao administrador para vincular seu usuário a um colaborador.", "error"); return redirect(url_for("inspections"))
            request.form.get("employee_id")
        photo=None; f=request.files.get("photo")
        if f and f.filename:
            name=secure_filename(f.filename); ext=os.path.splitext(name)[1].lower()
            if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
                flash("A foto da vistoria deve ser JPG, PNG ou WEBP.", "error"); return redirect(url_for("inspections"))
            photo=f"vistoria_{uuid.uuid4().hex}{ext}"; f.save(os.path.join(UPLOAD_DIR, photo))
        d.execute("INSERT INTO inspections(vehicle_id,employee_id,date,km,tires,lights,bodywork,equipment,observations,photo,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (int(request.form["vehicle_id"]), int(session["employee_id"]) if session.get("role")=="employee" else (int(request.form["employee_id"]) if request.form.get("employee_id") else None),
             request.form["date"], float(request.form["km"]), request.form.get("tires"), request.form.get("lights"), request.form.get("bodywork"),
             request.form.get("equipment"), request.form.get("observations"), photo, session["user_id"]))
        audit("Cadastro", "vistoria", d.execute("SELECT last_insert_rowid()").fetchone()[0], request.form.get("observations", "")); d.commit()
        flash("Vistoria registrada.", "success"); return redirect(url_for("inspections"))
    if session.get("role")=="employee":
        rows=d.execute("SELECT i.*,v.plate,e.name employee FROM inspections i JOIN vehicles v ON v.id=i.vehicle_id LEFT JOIN employees e ON e.id=i.employee_id WHERE i.created_by=? ORDER BY i.date DESC,i.id DESC", (session["user_id"],)).fetchall()
        vs=d.execute("SELECT id,plate FROM vehicles WHERE status!='Inativo' AND status='Disponível' ORDER BY plate").fetchall()
        es=d.execute("SELECT id,name FROM employees WHERE id=? AND active=1", (session.get("employee_id") or -1,)).fetchall()
    else:
        rows=d.execute("SELECT i.*,v.plate,e.name employee FROM inspections i JOIN vehicles v ON v.id=i.vehicle_id LEFT JOIN employees e ON e.id=i.employee_id ORDER BY i.date DESC,i.id DESC").fetchall()
        vs=d.execute("SELECT id,plate FROM vehicles WHERE status!='Inativo' ORDER BY plate").fetchall()
        es=d.execute("SELECT id,name FROM employees WHERE active=1 ORDER BY name").fetchall()
    return render_template("inspections.html", rows=rows, vehicles=vs, employees=es)

@app.get("/audit")
@roles("admin")
def audit_log():
    rows=db().execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 500").fetchall()
    return render_template("audit.html", rows=rows)

@app.route("/account/password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        old=request.form.get("old_password", ""); new=request.form.get("new_password", "")
        row=db().execute("SELECT password_hash FROM users WHERE id=?", (session["user_id"],)).fetchone()
        if not row or not check_password_hash(row["password_hash"], old):
            flash("A senha atual não confere.", "error")
        elif len(new) < 8:
            flash("A nova senha precisa ter pelo menos 8 caracteres.", "error")
        elif new != request.form.get("confirm_password", ""):
            flash("A confirmação da nova senha não confere.", "error")
        else:
            db().execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(new), session["user_id"]))
            audit("Alteração de senha", "usuário", session["user_id"]); db().commit()
            flash("Senha alterada com sucesso.", "success"); return redirect(url_for("dashboard"))
    return render_template("change_password.html")

@app.get("/admin/backup")
@roles("admin")
def download_backup():
    """Baixa um pacote ZIP com snapshot do banco e fotos/comprovantes."""
    audit("Exportação de backup", "banco de dados", None, "Backup completo solicitado pelo administrador"); db().commit()
    buf=io.BytesIO()
    with tempfile.TemporaryDirectory() as tempdir:
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            if USE_POSTGRES:
                tables = ["users","vehicles","employees","usages","equipment_items","usage_equipment_checks","fuel","tolls","maintenance","documents","inspections","notifications","tracking_points","tracking_sessions","audit_log","fines"]
                snapshot = {}
                for table in tables:
                    rows = db().execute(f"SELECT * FROM {table}").fetchall()
                    snapshot[table] = [{key: row[key] for key in row.keys()} for row in rows]
                archive.writestr("postgres_snapshot.json", json.dumps(snapshot, ensure_ascii=False, default=str, indent=2))
                archive.writestr("LEIA-ME.txt", "Backup lógico SECW FROTA (PostgreSQL) e arquivos enviados. O JSON contém os registros exportados; restauração requer importação compatível no PostgreSQL.\n")
            else:
                backup_path=os.path.join(tempdir, "secw_frota.db")
                source=sqlite3.connect(DB_PATH); target=sqlite3.connect(backup_path)
                source.backup(target); target.close(); source.close()
                archive.write(backup_path, "secw_frota.db")
                archive.writestr("LEIA-ME.txt", "Backup SECW FROTA. Restaure o arquivo secw_frota.db e a pasta uploads/ com o sistema fechado. Guarde este arquivo em local seguro.\n")
            for root, _, files in os.walk(UPLOAD_DIR):
                for filename in files:
                    full=os.path.join(root, filename)
                    archive.write(full, os.path.join("uploads", os.path.relpath(full, UPLOAD_DIR)))
    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True, download_name=f"secw_frota_backup_{date.today().isoformat()}.zip")

@app.get("/uploads/<path:filename>")
@login_required
def uploaded_file(filename):
    safe_name = secure_filename(filename)
    path = os.path.join(UPLOAD_DIR, safe_name)
    if not os.path.isfile(path): abort(404)
    return send_file(path, as_attachment=not safe_name.lower().startswith(("veiculo_", "vistoria_", "colaborador_")))

@app.errorhandler(403)
def forbidden(e): return render_template("message.html",title="Acesso não permitido",message="Seu perfil não possui permissão para esta área."),403
@app.errorhandler(404)
def not_found(e): return render_template("message.html",title="Registro não encontrado",message="O item solicitado não existe ou não está mais disponível."),404

if __name__ == "__main__":
    app.run(host=os.environ.get("HOST","127.0.0.1"), port=int(os.environ.get("PORT",5000)), debug=False)
