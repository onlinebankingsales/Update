import hashlib
import hmac
import math
import os
import re
import secrets
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_DATABASE_PATH = Path.home() / ".field_operations" / "records.db"
DATABASE_PATH = Path(
    os.environ.get("DATA_DB_PATH", str(DEFAULT_DATABASE_PATH))
).expanduser()
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
SUPER_ADMIN_USERNAME = "JeevanReddy"
PASSWORD_ITERATIONS = 600_000
USERNAME_PATTERN = re.compile(r"[A-Za-z0-9_.-]{3,32}\Z")
DUMMY_SALT = b"streamlit-field-app-login-salt"
REMARK_OPTIONS = ("Interested", "Not interested")
EMPLOYEE_TAGS = ("RM", "SO")
PROPOSAL_STAGES = (
    "Yet to login",
    "Login",
    "Pending for Sanction",
    "SUD",
    "Rejected",
    "Disbursed",
)
EMPLOYEE_STAGE_TRANSITIONS = {
    "Yet to login": {"Login"},
    "Login": {"Pending for Sanction"},
    "Pending for Sanction": {"Rejected", "SUD"},
    "SUD": {"Disbursed"},
    "Rejected": set(),
    "Disbursed": set(),
}
COMMITMENT_TYPES = ("Login", "Disbursement")
APP_TIMEZONE = timezone(timedelta(hours=5, minutes=30), name="UTC+05:30")


@contextmanager
def _connection(write=False):
    if DATABASE_URL:
        import psycopg
        from psycopg.rows import dict_row

        raw_connection = psycopg.connect(DATABASE_URL, row_factory=dict_row)
        connection = _PostgresConnection(raw_connection)
    else:
        DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        if write:
            _backup_sqlite_database()
        raw_connection = sqlite3.connect(DATABASE_PATH, timeout=30)
        raw_connection.row_factory = sqlite3.Row
        raw_connection.execute("PRAGMA foreign_keys = ON")
        connection = raw_connection
    try:
        yield connection
        raw_connection.commit()
    except Exception:
        raw_connection.rollback()
        raise
    finally:
        raw_connection.close()


def _sqlite_table_counts(path):
    if not path.is_file():
        return 0, 0

    with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as db:
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        user_count = (
            db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            if "users" in tables
            else 0
        )
        record_count = (
            db.execute("SELECT COUNT(*) FROM records").fetchone()[0]
            if "records" in tables
            else 0
        )
    return user_count, record_count


def _migrate_legacy_sqlite_path():
    if DATABASE_URL or os.environ.get("DATA_DB_PATH"):
        return

    legacy_path = Path(__file__).with_name("records.db")
    marker_path = DATABASE_PATH.with_name(
        f"{DATABASE_PATH.name}.legacy-imported"
    )
    if marker_path.exists():
        return
    if legacy_path.resolve() == DATABASE_PATH.resolve() or not legacy_path.is_file():
        return

    legacy_counts = _sqlite_table_counts(legacy_path)
    if legacy_counts == (0, 0):
        return

    current_counts = _sqlite_table_counts(DATABASE_PATH)
    if current_counts != (0, 0):
        raise RuntimeError(
            "Both the legacy and current SQLite databases contain accounts or "
            "records. Neither was changed. Set DATA_DB_PATH to the database "
            "containing the data you want to use, or merge the databases before "
            "starting the app."
        )

    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = DATABASE_PATH.with_name(
        f"{DATABASE_PATH.name}.migration-{secrets.token_hex(8)}"
    )
    try:
        with closing(sqlite3.connect(legacy_path)) as source:
            with closing(sqlite3.connect(temporary_path)) as destination:
                source.backup(destination)
                integrity = destination.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity != "ok":
                    raise RuntimeError(
                        f"Legacy SQLite database failed integrity check: {integrity}"
                    )
        os.replace(temporary_path, DATABASE_PATH)
        marker_path.write_text(
            str(legacy_path.resolve()),
            encoding="utf-8",
        )
    except Exception:
        if temporary_path.exists():
            temporary_path.unlink()
        raise


def _backup_sqlite_database():
    if DATABASE_URL or not DATABASE_PATH.is_file():
        return

    timestamp = datetime.now(APP_TIMEZONE).strftime("%Y%m%d-%H%M%S-%f")
    backup_path = DATABASE_PATH.with_name(
        f"{DATABASE_PATH.name}.backup-{timestamp}.sqlite"
    )
    try:
        with closing(sqlite3.connect(DATABASE_PATH)) as source:
            with closing(sqlite3.connect(backup_path)) as backup:
                source.backup(backup)
                integrity = backup.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity != "ok":
                    raise RuntimeError(
                        f"SQLite backup failed integrity check: {integrity}"
                    )
    except Exception:
        if backup_path.exists():
            backup_path.unlink()
        raise


class _PostgresConnection:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, query, parameters=()):
        return self.connection.execute(query.replace("?", "%s"), parameters)


def _password_digest(password, salt):
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )


def _set_user_password(connection, user_id, new_password):
    salt = secrets.token_bytes(16)
    password_hash = _password_digest(new_password, salt)
    connection.execute(
        "UPDATE users SET password_salt = ?, password_hash = ? WHERE id = ?",
        (salt, password_hash, user_id),
    )


def _effective_role(user):
    if user["username"].casefold() == SUPER_ADMIN_USERNAME.casefold():
        return "super_admin"
    return user["role"]


def _insert_user(
    connection,
    username,
    employee_name,
    password,
    role,
    created_by_user_id=None,
    phone_number=None,
    employee_tag=None,
):
    salt = secrets.token_bytes(16)
    password_hash = _password_digest(password, salt)
    cursor = connection.execute(
        """
        INSERT INTO users
            (username, employee_name, password_salt, password_hash, role,
             created_at, created_by_user_id, phone_number, employee_tag)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (
            username,
            employee_name,
            salt,
            password_hash,
            role,
            datetime.now(APP_TIMEZONE).isoformat(timespec="seconds"),
            created_by_user_id,
            phone_number,
            employee_tag,
        ),
    )
    return cursor.fetchone()["id"]


def _initialize_sqlite_schema(connection):
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL COLLATE NOCASE UNIQUE,
            employee_name TEXT NOT NULL,
            password_salt BLOB NOT NULL,
            password_hash BLOB NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('admin', 'user')),
            created_at TEXT NOT NULL,
            created_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
            phone_number TEXT,
            employee_tag TEXT CHECK (employee_tag IN ('RM', 'SO') OR employee_tag IS NULL),
            assigned_rm_id INTEGER REFERENCES users(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            admin_owner_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            employee_name TEXT NOT NULL,
            entry_date TEXT NOT NULL,
            logged_time TEXT NOT NULL,
            customer_name TEXT NOT NULL,
            contact_number TEXT NOT NULL,
            acres REAL NOT NULL CHECK (acres >= 0),
            village TEXT NOT NULL,
            mandal TEXT NOT NULL,
            district TEXT NOT NULL,
            remarks TEXT NOT NULL DEFAULT 'Not interested'
            CHECK (remarks IN ('Interested', 'Not interested')),
            stage TEXT NOT NULL DEFAULT 'Yet to login',
            loan_amount REAL NOT NULL DEFAULT 0 CHECK (loan_amount >= 0),
            stage_updated_at TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS proposal_stage_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            record_id INTEGER NOT NULL REFERENCES records(id) ON DELETE CASCADE,
            stage TEXT NOT NULL,
            changed_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            changed_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS commitments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            record_id INTEGER NOT NULL REFERENCES records(id) ON DELETE CASCADE,
            commitment_type TEXT NOT NULL CHECK (commitment_type IN ('Login', 'Disbursement')),
            commitment_date TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(employee_user_id, record_id, commitment_type, commitment_date)
        );
        CREATE TABLE IF NOT EXISTS commitment_responses (
            employee_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            commitment_type TEXT NOT NULL CHECK (commitment_type IN ('Login', 'Disbursement')),
            commitment_date TEXT NOT NULL,
            answer TEXT NOT NULL CHECK (answer IN ('Yes', 'No')),
            updated_at TEXT NOT NULL,
            PRIMARY KEY(employee_user_id, commitment_type, commitment_date)
        );
        CREATE TABLE IF NOT EXISTS whatsapp_sessions (
            employee_user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            state TEXT NOT NULL,
            payload TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS whatsapp_report_snapshots (
            admin_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            report_date TEXT NOT NULL,
            report_type TEXT NOT NULL CHECK (report_type IN ('morning', 'evening')),
            report_text TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(admin_user_id, report_date, report_type)
        );
        CREATE INDEX IF NOT EXISTS records_user_date_idx
            ON records(user_id, entry_date, id);
        CREATE INDEX IF NOT EXISTS proposal_stage_history_mtd_idx
            ON proposal_stage_history(changed_at, stage, record_id);
        """
    )
    user_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(users)")
    }
    if "created_by_user_id" not in user_columns:
        connection.execute(
            """ALTER TABLE users ADD COLUMN created_by_user_id
               INTEGER REFERENCES users(id) ON DELETE SET NULL"""
        )
    if "is_active" not in user_columns:
        connection.execute(
            "ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1"
        )
        connection.execute(
            "UPDATE users SET is_active = 1 WHERE is_active IS NULL"
        )
    for column, definition in (
        ("phone_number", "TEXT"),
        ("employee_tag", "TEXT"),
        ("assigned_rm_id", "INTEGER REFERENCES users(id) ON DELETE SET NULL"),
    ):
        if column not in user_columns:
            connection.execute(f"ALTER TABLE users ADD COLUMN {column} {definition}")
    record_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(records)")
    }
    if "admin_owner_id" not in record_columns:
        connection.execute(
            """ALTER TABLE records ADD COLUMN admin_owner_id
               INTEGER REFERENCES users(id) ON DELETE SET NULL"""
        )
    if "remarks" not in record_columns:
        connection.execute(
            "ALTER TABLE records ADD COLUMN remarks TEXT NOT NULL DEFAULT 'Not interested'"
        )
        connection.execute(
            "UPDATE records SET remarks = 'Not interested' WHERE remarks IS NULL"
        )
    for column, definition in (
        ("stage", "TEXT NOT NULL DEFAULT 'Yet to login'"),
        ("loan_amount", "REAL NOT NULL DEFAULT 0"),
        ("stage_updated_at", "TEXT NOT NULL DEFAULT ''"),
    ):
        if column not in record_columns:
            connection.execute(f"ALTER TABLE records ADD COLUMN {column} {definition}")
    connection.execute(
        "UPDATE records SET stage = 'Disbursed' WHERE stage = 'Disbursement'"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS users_assigned_rm_idx ON users(assigned_rm_id)"
    )
    connection.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS users_active_phone_idx
           ON users(phone_number)
           WHERE phone_number IS NOT NULL AND is_active = 1"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS commitments_day_idx "
        "ON commitments(employee_user_id, commitment_date, commitment_type)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS users_creator_idx ON users(created_by_user_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS records_admin_owner_idx "
        "ON records(admin_owner_id, entry_date, id)"
    )


def _initialize_postgres_schema(connection):
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id BIGSERIAL PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            employee_name TEXT NOT NULL,
            password_salt BYTEA NOT NULL,
            password_hash BYTEA NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('admin', 'user')),
            created_at TEXT NOT NULL,
            created_by_user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
            is_active SMALLINT NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
            phone_number TEXT,
            employee_tag TEXT CHECK (employee_tag IN ('RM', 'SO') OR employee_tag IS NULL),
            assigned_rm_id BIGINT REFERENCES users(id) ON DELETE SET NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS records (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
            admin_owner_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
            employee_name TEXT NOT NULL,
            entry_date TEXT NOT NULL,
            logged_time TEXT NOT NULL,
            customer_name TEXT NOT NULL,
            contact_number TEXT NOT NULL,
            acres DOUBLE PRECISION NOT NULL CHECK (acres >= 0),
            village TEXT NOT NULL,
            mandal TEXT NOT NULL,
            district TEXT NOT NULL,
            remarks TEXT NOT NULL DEFAULT 'Not interested',
            stage TEXT NOT NULL DEFAULT 'Yet to login',
            loan_amount DOUBLE PRECISION NOT NULL DEFAULT 0 CHECK (loan_amount >= 0),
            stage_updated_at TEXT NOT NULL DEFAULT ''
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS proposal_stage_history (
            id BIGSERIAL PRIMARY KEY,
            record_id BIGINT NOT NULL REFERENCES records(id) ON DELETE CASCADE,
            stage TEXT NOT NULL,
            changed_by_user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
            changed_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """ALTER TABLE users ADD COLUMN IF NOT EXISTS created_by_user_id
           BIGINT REFERENCES users(id) ON DELETE SET NULL"""
    )
    connection.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active SMALLINT NOT NULL DEFAULT 1"
    )
    connection.execute(
        "UPDATE users SET is_active = 1 WHERE is_active IS NULL"
    )
    connection.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS phone_number TEXT")
    connection.execute(
        """ALTER TABLE users ADD COLUMN IF NOT EXISTS employee_tag TEXT
           CHECK (employee_tag IN ('RM', 'SO') OR employee_tag IS NULL)"""
    )
    connection.execute(
        """ALTER TABLE users ADD COLUMN IF NOT EXISTS assigned_rm_id BIGINT
           REFERENCES users(id) ON DELETE SET NULL"""
    )
    connection.execute(
        """ALTER TABLE records ADD COLUMN IF NOT EXISTS admin_owner_id
           BIGINT REFERENCES users(id) ON DELETE SET NULL"""
    )
    connection.execute(
        "ALTER TABLE records ADD COLUMN IF NOT EXISTS remarks TEXT NOT NULL DEFAULT 'Not interested'"
    )
    connection.execute(
        "UPDATE records SET remarks = 'Not interested' WHERE remarks IS NULL"
    )
    connection.execute(
        "ALTER TABLE records ADD COLUMN IF NOT EXISTS stage TEXT NOT NULL DEFAULT 'Yet to login'"
    )
    connection.execute(
        "ALTER TABLE records ADD COLUMN IF NOT EXISTS loan_amount DOUBLE PRECISION NOT NULL DEFAULT 0"
    )
    connection.execute(
        "ALTER TABLE records ADD COLUMN IF NOT EXISTS stage_updated_at TEXT NOT NULL DEFAULT ''"
    )
    connection.execute(
        "UPDATE records SET stage = 'Disbursed' WHERE stage = 'Disbursement'"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS commitments (
            id BIGSERIAL PRIMARY KEY,
            employee_user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            record_id BIGINT NOT NULL REFERENCES records(id) ON DELETE CASCADE,
            commitment_type TEXT NOT NULL CHECK (commitment_type IN ('Login', 'Disbursement')),
            commitment_date TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(employee_user_id, record_id, commitment_type, commitment_date)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS commitment_responses (
            employee_user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            commitment_type TEXT NOT NULL CHECK (commitment_type IN ('Login', 'Disbursement')),
            commitment_date TEXT NOT NULL,
            answer TEXT NOT NULL CHECK (answer IN ('Yes', 'No')),
            updated_at TEXT NOT NULL,
            PRIMARY KEY(employee_user_id, commitment_type, commitment_date)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS whatsapp_sessions (
            employee_user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            state TEXT NOT NULL,
            payload TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS whatsapp_report_snapshots (
            admin_user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            report_date TEXT NOT NULL,
            report_type TEXT NOT NULL CHECK (report_type IN ('morning', 'evening')),
            report_text TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(admin_user_id, report_date, report_type)
        )
        """
    )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS users_username_lower_idx "
        "ON users (LOWER(username))"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS users_creator_idx ON users(created_by_user_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS users_assigned_rm_idx ON users(assigned_rm_id)"
    )
    connection.execute(
        """CREATE INDEX IF NOT EXISTS proposal_stage_history_mtd_idx
           ON proposal_stage_history(changed_at, stage, record_id)"""
    )
    connection.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS users_active_phone_idx
           ON users(phone_number)
           WHERE phone_number IS NOT NULL AND is_active = 1"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS commitments_day_idx "
        "ON commitments(employee_user_id, commitment_date, commitment_type)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS records_user_date_idx "
        "ON records(user_id, entry_date, id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS records_admin_owner_idx "
        "ON records(admin_owner_id, entry_date, id)"
    )


def _ensure_super_admin(connection):
    super_admin = connection.execute(
        "SELECT id FROM users WHERE LOWER(username) = LOWER(?) AND is_active = 1",
        (SUPER_ADMIN_USERNAME,),
    ).fetchone()
    if super_admin:
        super_admin_id = super_admin["id"]
        connection.execute(
            "UPDATE users SET role = 'admin' WHERE id = ?", (super_admin_id,)
        )
        connection.execute(
            """UPDATE users SET created_by_user_id = ?
               WHERE id != ? AND created_by_user_id IS NULL""",
            (super_admin_id, super_admin_id),
        )
        connection.execute(
            "UPDATE records SET admin_owner_id = ? WHERE admin_owner_id IS NULL",
            (super_admin_id,),
        )
        return True

    username = os.environ.get("INITIAL_ADMIN_USERNAME", "").strip()
    password = os.environ.get("INITIAL_ADMIN_PASSWORD", "")
    if username.casefold() != SUPER_ADMIN_USERNAME.casefold():
        return False
    if not USERNAME_PATTERN.fullmatch(username) or len(password) < 8:
        return False

    super_admin_id = _insert_user(
        connection, username, "Administrator", password, "admin"
    )
    connection.execute(
        "UPDATE users SET created_by_user_id = ? WHERE id != ?",
        (super_admin_id, super_admin_id),
    )
    connection.execute(
        "UPDATE records SET admin_owner_id = ? WHERE admin_owner_id IS NULL",
        (super_admin_id,),
    )
    return True


def initialize_database():
    _migrate_legacy_sqlite_path()
    with _connection() as connection:
        if DATABASE_URL:
            _initialize_postgres_schema(connection)
        else:
            _initialize_sqlite_schema(connection)
        return _ensure_super_admin(connection)


def change_own_password(user_id, current_password, new_password):
    if len(new_password) < 8:
        raise ValueError("Password must be at least 8 characters.")

    with _connection(write=True) as connection:
        user = connection.execute(
            "SELECT id, password_salt, password_hash FROM users WHERE id = ? AND is_active = 1",
            (user_id,),
        ).fetchone()
        if not user:
            raise ValueError("The account is no longer available.")
        current_hash = _password_digest(current_password, user["password_salt"])
        if not hmac.compare_digest(current_hash, user["password_hash"]):
            raise ValueError("The current password is incorrect.")
        _set_user_password(connection, user_id, new_password)


def reset_user_password(requester_id, target_user_id, new_password):
    if len(new_password) < 8:
        raise ValueError("Password must be at least 8 characters.")

    with _connection(write=True) as connection:
        requester = connection.execute(
            "SELECT id, username, role, created_by_user_id FROM users WHERE id = ? AND is_active = 1",
            (requester_id,),
        ).fetchone()
        if not requester:
            raise ValueError("The administrator account is no longer available.")
        target = connection.execute(
            "SELECT id, username, role, created_by_user_id, is_active FROM users WHERE id = ?",
            (target_user_id,),
        ).fetchone()
        if not target:
            raise ValueError("That account no longer exists.")
        if target["is_active"] == 0:
            raise ValueError("That account is already inactive.")

        requester_role = _effective_role(requester)
        if requester_role == "super_admin":
            if target["username"].casefold() == SUPER_ADMIN_USERNAME.casefold():
                raise ValueError("The super administrator password cannot be reset here.")
        elif requester_role == "admin":
            if target["role"] != "user" or target["created_by_user_id"] != requester_id:
                raise ValueError("You can only reset employee passwords you created.")
        else:
            raise ValueError("Only administrators can reset passwords.")

        _set_user_password(connection, target_user_id, new_password)


def export_sqlite_database():
    if DATABASE_URL:
        raise RuntimeError("SQLite backups are only available when using SQLite.")
    if not DATABASE_PATH.is_file():
        raise FileNotFoundError(f"SQLite database not found: {DATABASE_PATH}")

    source_uri = f"{DATABASE_PATH.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(source_uri, uri=True)) as source:
        with closing(sqlite3.connect(":memory:")) as backup:
            source.backup(backup)
            return backup.serialize()


def authenticate(username, password):
    normalized_username = username.strip()
    with _connection() as connection:
        user = connection.execute(
            "SELECT * FROM users WHERE LOWER(username) = LOWER(?) AND is_active = 1",
            (normalized_username,),
        ).fetchone()

    salt = user["password_salt"] if user else DUMMY_SALT
    expected_hash = user["password_hash"] if user else bytes(32)
    actual_hash = _password_digest(password, salt)
    if user and hmac.compare_digest(actual_hash, expected_hash):
        public_user = {
            key: user[key] for key in ("id", "username", "employee_name", "role")
        }
        public_user["role"] = _effective_role(user)
        return public_user
    return None


def get_user(user_id):
    with _connection() as connection:
        user = connection.execute(
            """SELECT id, username, employee_name, role, employee_tag, phone_number
               FROM users WHERE id = ? AND is_active = 1""",
            (user_id,),
        ).fetchone()
    if not user:
        return None
    public_user = dict(user)
    public_user["role"] = _effective_role(user)
    return public_user


def create_account(
    requester_id,
    username,
    employee_name,
    password,
    role="user",
    phone_number="",
    employee_tag=None,
):
    username = username.strip()
    employee_name = employee_name.strip()
    if not USERNAME_PATTERN.fullmatch(username):
        raise ValueError("Username must be 3-32 letters, numbers, dots, dashes, or underscores.")
    if username.casefold() == SUPER_ADMIN_USERNAME.casefold():
        raise ValueError("That username is reserved for the super administrator.")
    if not employee_name:
        raise ValueError("Employee name is required.")
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")
    if role not in ("admin", "user"):
        raise ValueError("Choose a valid account role.")
    if role == "user":
        if employee_tag not in EMPLOYEE_TAGS:
            raise ValueError("Choose RM or SO as the employee role.")
    else:
        employee_tag = None
    normalized_phone = _normalize_phone(phone_number, required=(role == "user"))

    with _connection(write=True) as connection:
        requester = connection.execute(
            "SELECT id, username, role FROM users WHERE id = ? AND is_active = 1",
            (requester_id,),
        ).fetchone()
        if not requester:
            raise ValueError("The administrator account is no longer available.")
        requester_role = _effective_role(requester)
        if requester_role != "super_admin" and requester_role != "admin":
            raise ValueError("Only administrators can create accounts.")
        if requester_role != "super_admin" and role != "user":
            raise ValueError("Only the super administrator can create administrators.")
        if normalized_phone and connection.execute(
            "SELECT 1 FROM users WHERE phone_number = ? AND is_active = 1",
            (normalized_phone,),
        ).fetchone():
            raise ValueError("That WhatsApp number is already assigned to an account.")
        try:
            user_id = _insert_user(
                connection,
                username,
                employee_name,
                password,
                role,
                requester_id,
                normalized_phone,
                employee_tag,
            )
        except Exception as error:
            if isinstance(error, sqlite3.IntegrityError) or getattr(
                error, "sqlstate", None
            ) == "23505":
                raise ValueError("That username is already in use.") from error
            raise
    return user_id


def list_users(requester_id):
    with _connection() as connection:
        requester = connection.execute(
            "SELECT id, username, role FROM users WHERE id = ? AND is_active = 1",
            (requester_id,),
        ).fetchone()
        if not requester:
            raise ValueError("The account is no longer available.")
        requester_role = _effective_role(requester)
        if requester_role == "super_admin":
            where_clause = "WHERE account.is_active = 1"
            parameters = ()
        elif requester_role == "admin":
            where_clause = "WHERE account.created_by_user_id = ? AND account.role = 'user' AND account.is_active = 1"
            parameters = (requester_id,)
        else:
            raise ValueError("Only administrators can manage accounts.")
        users = connection.execute(
            f"""
                 SELECT account.id, account.username, account.employee_name, account.role,
                     account.created_at, account.created_by_user_id,
                     account.employee_tag, account.phone_number, account.assigned_rm_id,
                     assigned_rm.employee_name AS assigned_rm_name,
                   creator.username AS created_by_username
                 FROM users AS account
                 LEFT JOIN users AS creator ON creator.id = account.created_by_user_id
                 LEFT JOIN users AS assigned_rm ON assigned_rm.id = account.assigned_rm_id
            {where_clause}
                 ORDER BY account.role DESC, LOWER(account.username)
            """,
            parameters,
        ).fetchall()
    public_users = []
    for user in users:
        public_user = dict(user)
        public_user["role"] = _effective_role(user)
        public_users.append(public_user)
    return public_users


def _normalize_phone(phone_number, required=False):
    phone_number = (phone_number or "").strip()
    if not phone_number:
        if required:
            raise ValueError("Employee WhatsApp phone number is required.")
        return None
    if not re.fullmatch(r"\+[1-9]\d{7,14}", phone_number):
        raise ValueError("Use an international phone number, for example +919876543210.")
    return phone_number


def update_employee_profile(requester_id, target_user_id, employee_tag=None, phone_number=None):
    if employee_tag is not None and employee_tag not in EMPLOYEE_TAGS:
        raise ValueError("Choose RM or SO as the employee role.")

    with _connection(write=True) as connection:
        requester = connection.execute(
            "SELECT id, username, role FROM users WHERE id = ? AND is_active = 1",
            (requester_id,),
        ).fetchone()
        target = connection.execute(
            """SELECT id, username, role, created_by_user_id, employee_tag,
                      phone_number FROM users WHERE id = ? AND is_active = 1""",
            (target_user_id,),
        ).fetchone()
        if not requester:
            raise ValueError("The administrator account is no longer available.")
        if not target or target["role"] != "user":
            raise ValueError("Only active employee accounts can be updated.")

        requester_role = _effective_role(requester)
        if requester_role == "super_admin":
            allowed = True
        elif requester_role == "admin":
            allowed = target["created_by_user_id"] == requester_id
        else:
            allowed = False
        if not allowed:
            raise ValueError("You can only update employees in your administration scope.")

        new_phone = (
            _normalize_phone(phone_number, required=True)
            if phone_number is not None
            else target["phone_number"]
        )
        if new_phone and connection.execute(
            """SELECT 1 FROM users
               WHERE phone_number = ? AND is_active = 1 AND id != ?""",
            (new_phone, target_user_id),
        ).fetchone():
            raise ValueError("That WhatsApp number is already assigned to another account.")
        new_tag = employee_tag or target["employee_tag"]
        if requester_role != "super_admin" and phone_number is not None:
            raise ValueError("Only the super administrator can update phone numbers.")
        if new_tag == "SO" and target["employee_tag"] == "RM":
            assigned_employees = connection.execute(
                """SELECT 1 FROM users
                   WHERE assigned_rm_id = ? AND is_active = 1 LIMIT 1""",
                (target_user_id,),
            ).fetchone()
            if assigned_employees:
                raise ValueError(
                    "Reassign this RM's SO employees before changing the employee tag."
                )
        connection.execute(
            """UPDATE users SET employee_tag = ?, phone_number = ?,
               assigned_rm_id = CASE WHEN ? = 'SO' THEN assigned_rm_id ELSE NULL END
               WHERE id = ?""",
            (new_tag, new_phone, new_tag, target_user_id),
        )


def assign_so_to_rm(requester_id, so_user_id, rm_user_id):
    with _connection(write=True) as connection:
        requester = connection.execute(
            "SELECT id, username, role FROM users WHERE id = ? AND is_active = 1",
            (requester_id,),
        ).fetchone()
        if not requester or _effective_role(requester) not in ("admin", "super_admin"):
            raise ValueError("Only administrators can assign an SO to an RM.")
        so_user = connection.execute(
            """SELECT id, role, employee_tag, created_by_user_id
               FROM users WHERE id = ? AND is_active = 1""",
            (so_user_id,),
        ).fetchone()
        rm_user = connection.execute(
            """SELECT id, role, employee_tag, created_by_user_id
               FROM users WHERE id = ? AND is_active = 1""",
            (rm_user_id,),
        ).fetchone()
        if not so_user or so_user["role"] != "user" or so_user["employee_tag"] != "SO":
            raise ValueError("Select an active SO employee.")
        if not rm_user or rm_user["role"] != "user" or rm_user["employee_tag"] != "RM":
            raise ValueError("Select an active RM employee.")
        if _effective_role(requester) != "super_admin":
            if (
                so_user["created_by_user_id"] != requester_id
                or rm_user["created_by_user_id"] != requester_id
            ):
                raise ValueError("Both employees must belong to your administration scope.")
        connection.execute(
            "UPDATE users SET assigned_rm_id = ? WHERE id = ?",
            (rm_user_id, so_user_id),
        )


def get_available_records(employee_id, stages=None):
    with _connection() as connection:
        employee = connection.execute(
            """SELECT id, role, employee_tag FROM users
               WHERE id = ? AND is_active = 1""",
            (employee_id,),
        ).fetchone()
        if not employee or employee["role"] != "user":
            raise ValueError("The employee account is no longer available.")
        query = f"SELECT {RECORD_COLUMNS} FROM records WHERE user_id = ?"
        parameters = [employee_id]
        if employee["employee_tag"] == "RM":
            query = (
                f"SELECT {RECORD_COLUMNS} FROM records WHERE "
                "(user_id = ? OR user_id IN "
                "(SELECT id FROM users WHERE assigned_rm_id = ? AND is_active = 1))"
            )
            parameters = [employee_id, employee_id]
        if stages:
            placeholders = ", ".join("?" for _ in stages)
            query += f" AND stage IN ({placeholders})"
            parameters.extend(stages)
        query += " ORDER BY entry_date DESC, id DESC"
        records = connection.execute(query, parameters).fetchall()
    return [dict(record) for record in records]


def get_owned_records(employee_id, stages=None):
    with _connection() as connection:
        employee = connection.execute(
            """SELECT id, role FROM users
               WHERE id = ? AND is_active = 1""",
            (employee_id,),
        ).fetchone()
        if not employee or employee["role"] != "user":
            raise ValueError("The employee account is no longer available.")
        query = f"SELECT {RECORD_COLUMNS} FROM records WHERE user_id = ?"
        parameters = [employee_id]
        if stages:
            placeholders = ", ".join("?" for _ in stages)
            query += f" AND stage IN ({placeholders})"
            parameters.extend(stages)
        query += " ORDER BY entry_date DESC, id DESC"
        records = connection.execute(query, parameters).fetchall()
    return [dict(record) for record in records]


def filter_proposals_by_text(records, search_text):
    search_text = (search_text or "").strip().casefold()
    if not search_text:
        return list(records)
    searchable_fields = ("Customer Name", "Contact Number")
    return [
        record
        for record in records
        if any(
            search_text in str(record.get(field) or "").casefold()
            for field in searchable_fields
        )
    ]


def update_proposal(requester_id, record_id, stage=None, loan_amount=None):
    if stage is not None and stage not in PROPOSAL_STAGES:
        raise ValueError("Choose a valid proposal stage.")
    if loan_amount is not None and (
        not math.isfinite(float(loan_amount)) or loan_amount < 0
    ):
        raise ValueError("Loan amount must be a finite non-negative number.")
    with _connection(write=True) as connection:
        requester = connection.execute(
            """SELECT id, username, role FROM users
               WHERE id = ? AND is_active = 1""",
            (requester_id,),
        ).fetchone()
        record = connection.execute(
            "SELECT id, user_id, admin_owner_id, stage FROM records WHERE id = ?",
            (record_id,),
        ).fetchone()
        if not requester:
            raise ValueError("The account is no longer available.")
        if not record:
            raise ValueError("That proposal no longer exists.")
        requester_role = _effective_role(requester)
        is_admin = requester_role in ("admin", "super_admin")
        if is_admin:
            if (
                requester_role != "super_admin"
                and record["admin_owner_id"] != requester_id
            ):
                raise ValueError("You can only update proposals in your own cluster.")
        elif requester["role"] == "user":
            if record["user_id"] != requester_id:
                raise ValueError("You can only update your own proposals.")
        else:
            raise ValueError("Only employees and administrators can update proposals.")

        if stage is not None and stage != record["stage"] and not is_admin:
            if stage not in EMPLOYEE_STAGE_TRANSITIONS.get(record["stage"], set()):
                raise ValueError("Choose a valid next proposal stage.")
        assignments = []
        parameters = []
        if stage is not None and stage != record["stage"]:
            changed_at = datetime.now(APP_TIMEZONE).isoformat(timespec="seconds")
            if is_admin:
                if stage == "Yet to login":
                    connection.execute(
                        """DELETE FROM proposal_stage_history
                           WHERE record_id = ? AND stage IN ('Login', 'Disbursed')""",
                        (record_id,),
                    )
                elif stage != "Disbursed":
                    connection.execute(
                        """DELETE FROM proposal_stage_history
                           WHERE record_id = ? AND stage = 'Disbursed'""",
                        (record_id,),
                    )
            assignments.extend(("stage = ?", "stage_updated_at = ?"))
            parameters.extend((stage, changed_at))
            connection.execute(
                """INSERT INTO proposal_stage_history
                   (record_id, stage, changed_by_user_id, changed_at)
                   VALUES (?, ?, ?, ?)""",
                (record_id, stage, requester_id, changed_at),
            )
        if loan_amount is not None:
            assignments.append("loan_amount = ?")
            parameters.append(loan_amount)
        if assignments:
            parameters.append(record_id)
            connection.execute(
                f"UPDATE records SET {', '.join(assignments)} WHERE id = ?",
                parameters,
            )


def get_mtd_performance(admin_id, start_date, end_date):
    with _connection() as connection:
        admin = connection.execute(
            """SELECT id, username, role FROM users
               WHERE id = ? AND is_active = 1""",
            (admin_id,),
        ).fetchone()
        if not admin or _effective_role(admin) not in ("admin", "super_admin"):
            raise ValueError("Only administrators can view MTD performance.")

        record_scope = ""
        employee_scope = ""
        parameters = []
        if _effective_role(admin) != "super_admin":
            record_scope = " AND proposal.admin_owner_id = ?"
            employee_scope = " AND employee.created_by_user_id = ?"
            parameters.append(admin_id)
        parameters.extend((start_date, end_date))
        if employee_scope:
            parameters.append(admin_id)
        rows = connection.execute(
            f"""
            SELECT employee.id AS employee_id,
                   employee.employee_name AS employee_name,
                   employee.employee_tag AS employee_role,
                   proposal.id AS record_id,
                   proposal.entry_date AS entry_date,
                   proposal.logged_time AS logged_time,
                   proposal.customer_name AS customer_name,
                   proposal.contact_number AS contact_number,
                   proposal.acres AS acres,
                   proposal.village AS village,
                   proposal.mandal AS mandal,
                   proposal.district AS district,
                   proposal.remarks AS remarks,
                   proposal.stage AS current_stage,
                   proposal.loan_amount AS loan_amount,
                   proposal.stage_updated_at AS stage_updated_at,
                   history.stage AS achieved_stage,
                   history.changed_at AS achieved_at
            FROM users AS employee
            LEFT JOIN records AS proposal
              ON proposal.user_id = employee.id {record_scope}
            LEFT JOIN proposal_stage_history AS history
              ON history.record_id = proposal.id
             AND SUBSTR(history.changed_at, 1, 10) BETWEEN ? AND ?
             AND history.stage IN ('Login', 'Disbursed')
            WHERE employee.role = 'user' AND employee.is_active = 1
              {employee_scope}
            ORDER BY employee.employee_name, proposal.id, history.changed_at
            """,
            parameters,
        ).fetchall()

    employees = {}
    details = {}
    for row in rows:
        employee_id = row["employee_id"]
        if row["employee_role"] not in EMPLOYEE_TAGS:
            continue
        employees.setdefault(
            employee_id,
            {
                "Employee ID": employee_id,
                "Employee Name": row["employee_name"],
                "Employee Role": row["employee_role"],
                "Logins (count)": 0,
                "Login Value": 0.0,
                "Disb no": 0,
                "Disb Volume": 0.0,
            },
        )
        employee_details = details.setdefault(
            employee_id, {"Login": {}, "Disbursed": {}}
        )
        if row["record_id"] is None or row["achieved_stage"] not in (
            "Login",
            "Disbursed",
        ):
            continue
        stage_details = employee_details[row["achieved_stage"]]
        stage_details.setdefault(
            row["record_id"],
            {
                "Proposal ID": row["record_id"],
                "Employee Name": row["employee_name"],
                "Employee Role": row["employee_role"],
                "Entry Date": row["entry_date"],
                "Logged Time": row["logged_time"],
                "Customer Name": row["customer_name"],
                "Contact Number": row["contact_number"],
                "Acres": row["acres"],
                "Village": row["village"],
                "Mandal": row["mandal"],
                "District": row["district"],
                "Remarks": row["remarks"],
                "Stage": row["current_stage"],
                "Loan Amount": row["loan_amount"],
                "Stage Updated At": row["stage_updated_at"],
                "Reached Date": row["achieved_at"][:10],
            },
        )

    for employee_id, employee in employees.items():
        login_records = details[employee_id]["Login"].values()
        disbursed_records = details[employee_id]["Disbursed"].values()
        employee["Logins (count)"] = len(details[employee_id]["Login"])
        employee["Login Value"] = sum(
            float(record["Loan Amount"] or 0) for record in login_records
        )
        employee["Disb no"] = len(details[employee_id]["Disbursed"])
        employee["Disb Volume"] = sum(
            float(record["Loan Amount"] or 0) for record in disbursed_records
        )

    return {
        "employees": sorted(
            employees.values(), key=lambda item: item["Employee Name"].casefold()
        ),
        "details": {
            employee_id: {
                stage: sorted(
                    records.values(), key=lambda item: item["Proposal ID"]
                )
                for stage, records in stage_details.items()
            }
            for employee_id, stage_details in details.items()
        },
    }


def save_commitment_response(employee_id, commitment_type, answer, record_ids=()):
    if commitment_type not in COMMITMENT_TYPES or answer not in ("Yes", "No"):
        raise ValueError("Select a valid commitment type and Yes/No answer.")
    today = datetime.now(APP_TIMEZONE).date().isoformat()
    now = datetime.now(APP_TIMEZONE).isoformat(timespec="seconds")
    with _connection(write=True) as connection:
        employee = connection.execute(
            "SELECT id, role, employee_tag FROM users WHERE id = ? AND is_active = 1",
            (employee_id,),
        ).fetchone()
        if not employee or employee["role"] != "user":
            raise ValueError("The employee account is no longer available.")
        if answer == "No" and record_ids:
            raise ValueError("A No response cannot include proposal selections.")
        if answer == "Yes" and not record_ids:
            raise ValueError("Select at least one proposal for a Yes commitment.")

        eligible_stages = (
            ("Yet to login",) if commitment_type == "Login" else ("SUD",)
        )
        for record_id in record_ids:
            if commitment_type == "Login":
                eligible = connection.execute(
                    """SELECT id FROM records
                       WHERE id = ? AND stage = ? AND user_id = ?""",
                    (record_id, eligible_stages[0], employee_id),
                ).fetchone()
                if not eligible:
                    raise ValueError(
                        "Login commitments can only use your own Yet to login proposals."
                    )
                continue
            eligible = connection.execute(
                """SELECT r.id FROM records AS r
                   LEFT JOIN users AS so ON so.id = r.user_id
                   WHERE r.id = ? AND r.stage IN ({})
                     AND (r.user_id = ? OR
                       (? = 'RM' AND so.assigned_rm_id = ?))""".format(
                    ", ".join("?" for _ in eligible_stages)
                ),
                (record_id, *eligible_stages, employee_id, employee["employee_tag"], employee_id),
            ).fetchone()
            if not eligible:
                raise ValueError("A selected proposal is unavailable or at the wrong stage.")

        connection.execute(
            """INSERT INTO commitment_responses
               (employee_user_id, commitment_type, commitment_date, answer, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(employee_user_id, commitment_type, commitment_date)
               DO UPDATE SET answer = excluded.answer, updated_at = excluded.updated_at""",
            (employee_id, commitment_type, today, answer, now),
        )
        connection.execute(
            """DELETE FROM commitments
               WHERE employee_user_id = ? AND commitment_type = ? AND commitment_date = ?""",
            (employee_id, commitment_type, today),
        )
        if answer == "Yes":
            for record_id in dict.fromkeys(record_ids):
                connection.execute(
                    """INSERT INTO commitments
                       (employee_user_id, record_id, commitment_type, commitment_date, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (employee_id, record_id, commitment_type, today, now),
                )


def get_daily_commitments(admin_id, commitment_date=None):
    commitment_date = commitment_date or datetime.now(APP_TIMEZONE).date().isoformat()
    with _connection() as connection:
        admin = connection.execute(
            "SELECT id, username, role FROM users WHERE id = ? AND is_active = 1",
            (admin_id,),
        ).fetchone()
        if not admin or _effective_role(admin) not in ("admin", "super_admin"):
            raise ValueError("Only administrators can view commitment reports.")
        parameters = [commitment_date]
        scope = ""
        if _effective_role(admin) != "super_admin":
            scope = "AND (employee.created_by_user_id = ? OR employee.assigned_rm_id IN (SELECT id FROM users WHERE created_by_user_id = ?))"
            parameters.extend((admin_id, admin_id))
        rows = connection.execute(
            f"""
            SELECT employee.employee_name AS "Employee name",
                   employee.employee_tag AS "Role",
                   response.commitment_type AS "Commitment type",
                   response.answer AS "Answer",
                   record.customer_name AS "Customer",
                   record.stage AS "Stage",
                   record.loan_amount AS "Loan amount",
                   record.employee_name AS "Proposal owner"
            FROM commitment_responses AS response
            JOIN users AS employee ON employee.id = response.employee_user_id
            LEFT JOIN commitments AS commitment
              ON commitment.employee_user_id = response.employee_user_id
             AND commitment.commitment_type = response.commitment_type
             AND commitment.commitment_date = response.commitment_date
            LEFT JOIN records AS record ON record.id = commitment.record_id
            WHERE response.commitment_date = ? {scope}
            ORDER BY employee.employee_name, response.commitment_type, record.customer_name
            """,
            parameters,
        ).fetchall()
    return [dict(row) for row in rows]


def list_whatsapp_employees():
    with _connection() as connection:
        rows = connection.execute(
            """SELECT id, employee_name, phone_number FROM users
               WHERE role = 'user' AND is_active = 1 AND phone_number IS NOT NULL
               ORDER BY employee_name"""
        ).fetchall()
    return [dict(row) for row in rows]


def get_employee_by_phone(phone_number):
    normalized_phone = _normalize_phone(phone_number, required=True)
    with _connection() as connection:
        row = connection.execute(
            """SELECT id, employee_name, employee_tag FROM users
               WHERE phone_number = ? AND role = 'user' AND is_active = 1""",
            (normalized_phone,),
        ).fetchone()
    return dict(row) if row else None


def set_whatsapp_session(employee_id, state, payload=None):
    import json

    with _connection(write=True) as connection:
        connection.execute(
            """INSERT INTO whatsapp_sessions
               (employee_user_id, state, payload, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(employee_user_id) DO UPDATE SET
               state = excluded.state, payload = excluded.payload,
               updated_at = excluded.updated_at""",
            (
                employee_id,
                state,
                json.dumps(payload or {}),
                datetime.now(APP_TIMEZONE).isoformat(timespec="seconds"),
            ),
        )


def get_whatsapp_session(employee_id):
    import json

    with _connection() as connection:
        row = connection.execute(
            """SELECT state, payload FROM whatsapp_sessions
               WHERE employee_user_id = ?""",
            (employee_id,),
        ).fetchone()
    if not row:
        return None
    return {"state": row["state"], "payload": json.loads(row["payload"])}


def clear_whatsapp_session(employee_id):
    with _connection(write=True) as connection:
        connection.execute(
            "DELETE FROM whatsapp_sessions WHERE employee_user_id = ?",
            (employee_id,),
        )


def get_whatsapp_report_snapshot(admin_id, report_date, report_type):
    if report_type not in ("morning", "evening"):
        raise ValueError("Choose a valid WhatsApp report period.")
    with _connection() as connection:
        row = connection.execute(
            """SELECT report_text FROM whatsapp_report_snapshots
               WHERE admin_user_id = ? AND report_date = ? AND report_type = ?""",
            (admin_id, report_date, report_type),
        ).fetchone()
    return row["report_text"] if row else None


def save_whatsapp_report_snapshot(admin_id, report_date, report_type, report_text):
    if report_type not in ("morning", "evening"):
        raise ValueError("Choose a valid WhatsApp report period.")
    with _connection(write=True) as connection:
        connection.execute(
            """INSERT INTO whatsapp_report_snapshots
               (admin_user_id, report_date, report_type, report_text, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(admin_user_id, report_date, report_type)
               DO UPDATE SET report_text = excluded.report_text,
                             updated_at = excluded.updated_at""",
            (
                admin_id,
                report_date,
                report_type,
                report_text,
                datetime.now(APP_TIMEZONE).isoformat(timespec="seconds"),
            ),
        )


def update_admin_phone(requester_id, target_admin_id, phone_number):
    normalized_phone = _normalize_phone(phone_number, required=True)
    with _connection(write=True) as connection:
        requester = connection.execute(
            "SELECT id, username, role FROM users WHERE id = ? AND is_active = 1",
            (requester_id,),
        ).fetchone()
        target = connection.execute(
            "SELECT id, role FROM users WHERE id = ? AND is_active = 1",
            (target_admin_id,),
        ).fetchone()
        if not requester or _effective_role(requester) != "super_admin":
            raise ValueError("Only the super administrator can update administrator numbers.")
        if not target or target["role"] != "admin":
            raise ValueError("Select an active administrator account.")
        if connection.execute(
            """SELECT 1 FROM users
               WHERE phone_number = ? AND is_active = 1 AND id != ?""",
            (normalized_phone, target_admin_id),
        ).fetchone():
            raise ValueError("That WhatsApp number is already assigned to another account.")
        connection.execute(
            "UPDATE users SET phone_number = ? WHERE id = ?",
            (normalized_phone, target_admin_id),
        )


def list_whatsapp_admins():
    with _connection() as connection:
        admins = connection.execute(
            """SELECT id, employee_name, phone_number FROM users
               WHERE role = 'admin' AND is_active = 1 AND phone_number IS NOT NULL
               ORDER BY id"""
        ).fetchall()
    return [dict(admin) for admin in admins]


def get_super_admin_id():
    with _connection() as connection:
        admin = connection.execute(
            "SELECT id FROM users WHERE LOWER(username) = LOWER(?) AND is_active = 1",
            (SUPER_ADMIN_USERNAME,),
        ).fetchone()
    return admin["id"] if admin else None


def delete_user(user_id, requester_id):
    if user_id == requester_id:
        raise ValueError("You cannot delete the account currently in use.")

    with _connection(write=True) as connection:
        requester = connection.execute(
            "SELECT id, username, role FROM users WHERE id = ? AND is_active = 1",
            (requester_id,),
        ).fetchone()
        if not requester:
            raise ValueError("The administrator account is no longer available.")
        user = connection.execute(
            "SELECT id, username, role, created_by_user_id, is_active FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not user:
            raise ValueError("That account no longer exists.")
        if user["is_active"] == 0:
            raise ValueError("That account is already inactive.")
        requester_role = _effective_role(requester)
        if requester_role == "super_admin":
            if user["username"].casefold() == SUPER_ADMIN_USERNAME.casefold():
                raise ValueError("The super administrator account cannot be deleted.")
        elif not (
            requester_role == "admin"
            and user["role"] == "user"
            and user["created_by_user_id"] == requester_id
        ):
            raise ValueError("You can only delete employee accounts you created.")

        if user["role"] == "admin":
            admin_count = connection.execute(
                "SELECT COUNT(*) AS account_count FROM users WHERE role = 'admin' AND is_active = 1"
            ).fetchone()["account_count"]
            if admin_count <= 1:
                raise ValueError("The last administrator account cannot be deleted.")
            connection.execute(
                "UPDATE users SET created_by_user_id = ? WHERE created_by_user_id = ? AND is_active = 1",
                (requester_id, user_id),
            )
            connection.execute(
                "UPDATE records SET admin_owner_id = ? WHERE admin_owner_id = ?",
                (requester_id, user_id),
            )
        connection.execute(
            "UPDATE users SET is_active = 0 WHERE id = ?",
            (user_id,),
        )


def add_record(
    user_id,
    entry_date,
    logged_time,
    customer_name,
    contact_number,
    acres,
    village,
    mandal,
    district,
    remarks,
    loan_amount=0,
):
    if remarks not in REMARK_OPTIONS:
        raise ValueError("Please select a valid customer remark.")
    if not math.isfinite(float(loan_amount)) or loan_amount < 0:
        raise ValueError("Loan amount must be a finite non-negative number.")
    with _connection(write=True) as connection:
        user = connection.execute(
            """SELECT employee_name, created_by_user_id
               FROM users WHERE id = ? AND role = 'user'""",
            (user_id,),
        ).fetchone()
        if not user:
            raise ValueError("The employee account is no longer available.")
        connection.execute(
            """
            INSERT INTO records (
                user_id, admin_owner_id, employee_name, entry_date, logged_time,
                customer_name, contact_number, acres, village, mandal, district,
                remarks, loan_amount
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                user["created_by_user_id"],
                user["employee_name"],
                entry_date,
                logged_time,
                customer_name,
                contact_number,
                acres,
                village,
                mandal,
                district,
                remarks,
                loan_amount,
            ),
        )


RECORD_COLUMNS = """
    records.id AS "Proposal ID",
    entry_date AS "Entry Date",
    logged_time AS "Logged Time",
    employee_name AS "Employee Name",
    customer_name AS "Customer Name",
    contact_number AS "Contact Number",
    acres AS "Acres",
    village AS "Village",
    mandal AS "Mandal",
    district AS "District",
    remarks AS "Remarks",
    stage AS "Stage",
    loan_amount AS "Loan Amount",
    stage_updated_at AS "Stage Updated At"
"""


def get_user_records(user_id):
    return get_available_records(user_id)


def get_admin_records(admin_id):
    with _connection() as connection:
        admin = connection.execute(
            "SELECT id, username, role FROM users WHERE id = ? AND is_active = 1",
            (admin_id,),
        ).fetchone()
        if not admin:
            raise ValueError("The administrator account is no longer available.")
        if _effective_role(admin) == "super_admin":
            query = (
                f"SELECT {RECORD_COLUMNS} FROM records "
                "ORDER BY entry_date DESC, id DESC"
            )
            parameters = ()
        elif admin["role"] == "admin":
            query = (
                f"SELECT {RECORD_COLUMNS} FROM records "
                "WHERE admin_owner_id = ? ORDER BY entry_date DESC, id DESC"
            )
            parameters = (admin_id,)
        else:
            raise ValueError("Only administrators can view team records.")
        records = connection.execute(
            query, parameters
        ).fetchall()
    return [dict(record) for record in records]


def migrate_sqlite_to_postgres(source_path=None):
    if not DATABASE_URL:
        raise RuntimeError("Set DATABASE_URL before migrating to PostgreSQL.")

    source_path = Path(source_path or DATABASE_PATH).expanduser()
    if not source_path.is_file():
        raise FileNotFoundError(f"SQLite database not found: {source_path}")

    with sqlite3.connect(source_path) as source:
        source.row_factory = sqlite3.Row
        user_columns = {
            row["name"] for row in source.execute("PRAGMA table_info(users)")
        }
        record_columns = {
            row["name"] for row in source.execute("PRAGMA table_info(records)")
        }
        source_tables = {
            row["name"]
            for row in source.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        users = source.execute("SELECT * FROM users ORDER BY id").fetchall()
        records = source.execute("SELECT * FROM records ORDER BY id").fetchall()
        commitments = (
            source.execute("SELECT * FROM commitments ORDER BY id").fetchall()
            if "commitments" in source_tables
            else ()
        )
        proposal_stage_history = (
            source.execute(
                "SELECT * FROM proposal_stage_history ORDER BY id"
            ).fetchall()
            if "proposal_stage_history" in source_tables
            else ()
        )
        commitment_responses = (
            source.execute("SELECT * FROM commitment_responses").fetchall()
            if "commitment_responses" in source_tables
            else ()
        )

    super_admin = next(
        (
            user
            for user in users
            if user["username"].casefold() == SUPER_ADMIN_USERNAME.casefold()
        ),
        None,
    )
    if not super_admin:
        raise ValueError("The SQLite database has no super administrator account.")
    super_admin_id = super_admin["id"]

    with _connection() as connection:
        _initialize_postgres_schema(connection)
        target_user_count = connection.execute(
            "SELECT COUNT(*) AS total FROM users"
        ).fetchone()["total"]
        target_record_count = connection.execute(
            "SELECT COUNT(*) AS total FROM records"
        ).fetchone()["total"]
        if target_record_count or target_user_count > 1:
            raise ValueError("The PostgreSQL database must be empty before migration.")
        if target_user_count == 1:
            bootstrap_user = connection.execute(
                "SELECT username FROM users"
            ).fetchone()
            if bootstrap_user["username"].casefold() != SUPER_ADMIN_USERNAME.casefold():
                raise ValueError(
                    "The PostgreSQL database only supports replacing its initial super-admin account."
                )
            connection.execute("DELETE FROM users")

        for user in users:
            is_super_admin = (
                user["username"].casefold() == SUPER_ADMIN_USERNAME.casefold()
            )
            created_by_user_id = (
                user["created_by_user_id"]
                if "created_by_user_id" in user_columns
                else None if is_super_admin else super_admin_id
            )
            connection.execute(
                """
                INSERT INTO users (
                    id, username, employee_name, password_salt, password_hash,
                    role, created_at, created_by_user_id, is_active, phone_number,
                    employee_tag, assigned_rm_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user["id"],
                    user["username"],
                    user["employee_name"],
                    user["password_salt"],
                    user["password_hash"],
                    "admin" if is_super_admin else user["role"],
                    user["created_at"],
                    created_by_user_id,
                    user["is_active"] if "is_active" in user_columns else 1,
                    user["phone_number"] if "phone_number" in user_columns else None,
                    user["employee_tag"] if "employee_tag" in user_columns else None
                    if user["role"] == "user"
                    else None,
                    user["assigned_rm_id"]
                    if "assigned_rm_id" in user_columns
                    else None,
                ),
            )

        for record in records:
            admin_owner_id = (
                record["admin_owner_id"]
                if "admin_owner_id" in record_columns
                else super_admin_id
            )
            remark = (
                record["remarks"]
                if "remarks" in record_columns
                else "Not interested"
            )
            if remark not in REMARK_OPTIONS:
                remark = "Not interested"
            stage = (
                record["stage"]
                if "stage" in record_columns
                else "Yet to login"
            )
            if stage == "Disbursement":
                stage = "Disbursed"
            if stage not in PROPOSAL_STAGES:
                stage = "Yet to login"
            connection.execute(
                """
                INSERT INTO records (
                    id, user_id, admin_owner_id, employee_name, entry_date,
                    logged_time, customer_name, contact_number, acres, village,
                    mandal, district, remarks, stage, loan_amount, stage_updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["id"],
                    record["user_id"],
                    admin_owner_id,
                    record["employee_name"],
                    record["entry_date"],
                    record["logged_time"],
                    record["customer_name"],
                    record["contact_number"],
                    record["acres"],
                    record["village"],
                    record["mandal"],
                    record["district"],
                    remark,
                    stage,
                    record["loan_amount"]
                    if "loan_amount" in record_columns
                    else 0,
                    record["stage_updated_at"]
                    if "stage_updated_at" in record_columns
                    else "",
                ),
            )

        for history in proposal_stage_history:
            history_stage = (
                "Disbursed"
                if history["stage"] == "Disbursement"
                else history["stage"]
            )
            connection.execute(
                """INSERT INTO proposal_stage_history
                   (id, record_id, stage, changed_by_user_id, changed_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    history["id"],
                    history["record_id"],
                    history_stage,
                    history["changed_by_user_id"],
                    history["changed_at"],
                ),
            )

        for commitment in commitments:
            connection.execute(
                """INSERT INTO commitments
                   (id, employee_user_id, record_id, commitment_type,
                    commitment_date, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                tuple(
                    commitment[key]
                    for key in (
                        "id",
                        "employee_user_id",
                        "record_id",
                        "commitment_type",
                        "commitment_date",
                        "created_at",
                    )
                ),
            )
        for response in commitment_responses:
            connection.execute(
                """INSERT INTO commitment_responses
                   (employee_user_id, commitment_type, commitment_date, answer, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                tuple(
                    response[key]
                    for key in (
                        "employee_user_id",
                        "commitment_type",
                        "commitment_date",
                        "answer",
                        "updated_at",
                    )
                ),
            )

        connection.execute(
            """SELECT setval(pg_get_serial_sequence('users', 'id'),
               COALESCE(MAX(id), 1), COUNT(*) > 0) FROM users"""
        )
        connection.execute(
            """SELECT setval(pg_get_serial_sequence('records', 'id'),
               COALESCE(MAX(id), 1), COUNT(*) > 0) FROM records"""
        )
        connection.execute(
            """SELECT setval(pg_get_serial_sequence('proposal_stage_history', 'id'),
               COALESCE(MAX(id), 1), COUNT(*) > 0) FROM proposal_stage_history"""
        )

    return {
        "users": len(users),
        "records": len(records),
        "proposal_stage_history": len(proposal_stage_history),
    }
