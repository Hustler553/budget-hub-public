import re
import sqlite3
from datetime import datetime

from budget import config
from budget import db

_FILENAME = re.compile(r"^(\d{3})_([a-z0-9_]+)\.sql$")


def ensure_table(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
               version    INTEGER PRIMARY KEY,
               name       TEXT NOT NULL,
               applied_at TEXT NOT NULL DEFAULT (datetime('now'))
           )"""
    )


def available():
    found = []
    for path in config.MIGRATIONS_DIR.iterdir():
        match = _FILENAME.match(path.name)
        if match:
            found.append((int(match.group(1)), match.group(2), path))
    return sorted(found, key=lambda item: item[0])


def applied(conn):
    ensure_table(conn)
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {row["version"] for row in rows}


def pending(conn):
    done = applied(conn)
    return [item for item in available() if item[0] not in done]


class MigrationError(Exception):
    pass


_FK_OFF_MARKER = "-- requires: foreign_keys_off"

_DIRECTIVE = re.compile(r"--\s*requires?\s*:", re.IGNORECASE)


def _needs_fk_off(sql, source="the migration"):
    found = False
    for number, line in enumerate(sql.splitlines(), start=1):
        exact = line.strip() == _FK_OFF_MARKER
        if not exact and not _DIRECTIVE.search(line):
            continue
        if number > 5:
            raise MigrationError(
                f"{source} has a directive on line {number}, below the first five lines. "
                f"Directives are only read from those, so this one would do nothing: "
                f"{line.strip()!r}. It has to be in the first five lines, and the only "
                f"one understood is {_FK_OFF_MARKER!r}, on a line of its own. "
                f"Nothing was run."
            )
        if not exact:
            raise MigrationError(
                f"{source} has a line that reads as a directive but is not one this "
                f"runner knows (line {number}): {line.strip()!r}. The only one it "
                f"understands is {_FK_OFF_MARKER!r}, on a line of its own. "
                f"Nothing was run."
            )
        found = True
    return found


def _schema_copy(conn):
    copy = sqlite3.connect(":memory:")
    try:
        copy.execute("PRAGMA foreign_keys = OFF")
        objects = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL "
            "ORDER BY CASE type WHEN 'table' THEN 0 WHEN 'view' THEN 1 "
            "WHEN 'index' THEN 2 ELSE 3 END, rowid"
        ).fetchall()
        for row in objects:
            if not row[0].startswith("sqlite_"):
                copy.execute(row[1])
    except BaseException:
        copy.close()
        raise
    return copy


def _dropped_table(conn, sql, source="the migration"):
    dropped = []
    named_a_file = []

    def authorize(action, name, detail, database, trigger_or_view):
        if action == sqlite3.SQLITE_DROP_TABLE:
            dropped.append(name)
            return sqlite3.SQLITE_DENY
        if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH):
            named_a_file.append(name)
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    try:
        copy = _schema_copy(conn)
    except sqlite3.Error as exc:
        raise MigrationError(
            f"A copy of this database's schema could not be built, so no migration can "
            f"be checked for table drops and {source} was not run: {exc}. The copy is "
            f"replayed out of sqlite_master, where a virtual table's shadow tables are "
            f"listed as ordinary tables, so one live FTS5 or R-Tree table makes every "
            f"replay fail on a name that already exists. That is this runner's limit and "
            f"not a fault in the migration. Nothing was run."
        ) from exc
    try:
        copy.set_authorizer(authorize)
        copy.executescript(f"BEGIN;\n{sql}")
    except sqlite3.Error as exc:
        if not dropped:
            aside = ""
            if named_a_file:
                aside = (
                    " The check runs the script, so it denies ATTACH and DETACH: it must "
                    "not be able to reach a file. A migration that has to attach one "
                    "cannot be checked for drops by this runner."
                )
            raise MigrationError(
                f"{source} could not be prepared against a copy of this database's "
                f"schema, so whether it drops a table is unknown and none of it was "
                f"run: {exc}.{aside} Every migration is checked that way first, because "
                f"dropping a table fires every ON DELETE rule that points at it, and a "
                f"script that stops early has a tail nothing has read. Nothing was run."
            ) from exc
    finally:
        copy.close()
    return dropped[0] if dropped else None


def _fk_violations(conn):
    return {tuple(row) for row in conn.execute("PRAGMA foreign_key_check")}


def _apply(conn, version, name, sql):
    source = f"Migration {version:03d}_{name}.sql"
    fk_off = _needs_fk_off(sql, source)
    if not fk_off:
        dropped = _dropped_table(conn, sql, source)
        if dropped is not None:
            raise MigrationError(
                f"{source} drops the table {dropped!r} but does not declare "
                f"{_FK_OFF_MARKER!r}. A migration that drops a table must declare it, on a "
                f"line of its own in its first five lines, because dropping a table fires "
                f"every ON DELETE rule that points at it and, with enforcement live, that "
                f"deletes or blanks other tables' rows. If it is there already, compare it "
                f"with this one character by character. Nothing was run."
            )
    if fk_off:
        conn.execute("PRAGMA foreign_keys = OFF")
    began = False
    try:
        if fk_off:
            if conn.execute("PRAGMA foreign_keys").fetchone()[0]:
                raise MigrationError(
                    f"Migration {version:03d}_{name} needs foreign keys suspended, "
                    f"but the pragma was ignored. Is a transaction already open?"
                )
            broken_before = _fk_violations(conn)
        began = True
        conn.executescript(f"BEGIN;\n{sql}")
        conn.execute(
            "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
            (version, name),
        )
        if fk_off:
            introduced = _fk_violations(conn) - broken_before
            if introduced:
                shown = ", ".join(
                    f"{table} row {rowid} -> {parent}"
                    for table, rowid, parent, _ in sorted(introduced, key=repr)[:5]
                )
                raise sqlite3.IntegrityError(
                    f"it left {len(introduced)} dangling reference(s): {shown}"
                )
        conn.execute("COMMIT")
    except sqlite3.Error as exc:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise MigrationError(
            f"Migration {version:03d}_{name} failed and was rolled back: {exc}"
        ) from exc
    finally:
        if fk_off:
            if began and conn.in_transaction:
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
            conn.execute("PRAGMA foreign_keys = ON")


def run_pending():
    done = []
    with db.get_conn() as conn:
        ensure_table(conn)
        todo = pending(conn)
        if not todo:
            return done
        highest = todo[-1][0]
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
        try:
            snapshot = db.backup_db(label=f"premigration-{highest:03d}-{stamp}")
        except (OSError, sqlite3.Error) as exc:
            raise MigrationError(
                f"Could not snapshot before migrating to {highest:03d}: {exc}"
            ) from exc
        if snapshot is None:
            raise MigrationError(
                f"Refusing to migrate to {highest:03d}: a snapshot named "
                f"premigration-{highest:03d}-{stamp} already exists, so no "
                f"backup of the current database was taken."
            )
        for version, name, path in todo:
            try:
                sql = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise MigrationError(
                    f"Migration {version:03d}_{name} could not be read: {exc}"
                ) from exc
            _apply(conn, version, name, sql)
            done.append(version)
    return done
