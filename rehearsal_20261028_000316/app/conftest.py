"""Every test uses a disposable SQLite copy; diploma.db is never a test target."""
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event

PROJECT = Path(__file__).resolve().parent
TEST_ROOT = Path(tempfile.mkdtemp(prefix="meta-pytest-"))
os.environ["DATABASE_URL"] = "sqlite:///" + str(TEST_ROOT / "bootstrap.db")
os.environ["UPLOADS_DIR"] = str(TEST_ROOT / "uploads")
# Demo credentials are confined to disposable test databases.
os.environ["META_SEED_DEMO"] = "1"


@pytest.fixture(autouse=True)
def isolated_database(tmp_path):
    import main
    main.engine.dispose()
    test_db = tmp_path / "test.db"
    donor_db = tmp_path / "donor.db"
    with sqlite3.connect(f"file:{PROJECT / 'diploma.db'}?mode=ro", uri=True) as source, sqlite3.connect(donor_db) as target:
        source.backup(target)
        # A controlled test dataset keeps original signed OrderItems but starts
        # each application's workflow from scratch. Only this disposable copy changes.
        target.execute("PRAGMA foreign_keys=ON")
        target.execute("DELETE FROM checkpoint_submissions")
        target.execute("UPDATE checkpoints SET application_id=NULL, status='NOT_SUBMITTED', file_name=NULL")
        target.execute("DELETE FROM applications")
        target.execute("DELETE FROM preferences")
        target.execute("DELETE FROM sessions")
        target.execute("DELETE FROM notifications")
        target.execute("UPDATE topics SET quota_used=0, status='available'")
        target.commit()
    main.engine = create_engine("sqlite:///" + str(test_db), connect_args={"check_same_thread": False})
    event.listen(main.engine, "connect", main.set_sqlite_pragma)
    main.SessionLocal.configure(bind=main.engine)
    main.Base.metadata.create_all(main.engine)
    # Rebuild only the disposable fixture schema from the declared models. This
    # verifies all FKs, including legacy columns which have no physical FK in the
    # uploaded database. No migration of the user's database is performed.
    with sqlite3.connect(donor_db) as donor, sqlite3.connect(test_db) as target:
        target.execute("PRAGMA foreign_keys=ON")
        for table in main.Base.metadata.sorted_tables:
            available = {row[1] for row in donor.execute('PRAGMA table_info("' + table.name + '")')}
            columns = [column.name for column in table.columns if column.name in available]
            names = ','.join('"' + name + '"' for name in columns)
            rows = donor.execute('SELECT ' + names + ' FROM "' + table.name + '"').fetchall()
            if rows:
                target.executemany('INSERT INTO "' + table.name + '" (' + names + ') VALUES (' + ','.join('?' for _ in columns) + ')', rows)
        target.commit()
    # Refresh only disposable upload files so signed PDFs retain their original hashes.
    upload_root = Path(main.UPLOADS_DIR)
    if upload_root.exists():
        shutil.rmtree(upload_root)
    shutil.copytree(PROJECT / "uploads", upload_root)
    main.migrate_database()
    yield
    main.engine.dispose()


def pytest_sessionfinish(session, exitstatus):
    import main
    main.engine.dispose()
    shutil.rmtree(TEST_ROOT)
