"""Exercise the real targeted repair on independent copies of its pre-repair backup."""
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event

import main
import repair_demo_database as repair


ROOT = Path(__file__).resolve().parent


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def make_legacy_copy(tmp_path):
    folder = tmp_path / "META_AUDITED_172"
    folder.mkdir()
    target = folder / "diploma.db"
    shutil.copy2(ROOT / "backups" / "diploma_before_compat.db", target)
    return target


def snapshots(path):
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        return repair.table_rows(db)


def test_repair_preserves_all_rows_and_only_changes_authorized_ids(tmp_path):
    target = make_legacy_copy(tmp_path)
    before = snapshots(target)
    report = repair.repair_demo(target)
    after = snapshots(target)
    assert report["changed_application_ids"] == [6]
    assert report["changed_topic_ids"] == [1, 3, 4, 5]
    assert report["checkpoint_rows_preserved"] == 2836
    assert report["backup_sha256"] == report["original_sha256"]
    for name in before:
        assert len(before[name]) == len(after[name]), name
        assert [row[0] for row in before[name]] == [row[0] for row in after[name]], name
        if name not in ("topics", "applications"):
            unchanged = before[name] == after[name]
            assert unchanged, name
    with sqlite3.connect(target) as db:
        assert db.execute("SELECT status FROM applications WHERE id=2").fetchone() == ("APPROVED",)
        assert db.execute("SELECT status,priority FROM applications WHERE id=6").fetchone() == ("REJECTED",1)
        assert db.execute("SELECT quota_used,quota_total FROM topics WHERE id=2").fetchone() == (4,4)


def test_checkpoint_fk_child_references_and_columns_are_preserved(tmp_path):
    target = make_legacy_copy(tmp_path)
    with sqlite3.connect(target) as db:
        columns_before = db.execute("PRAGMA table_info(checkpoints)").fetchall()
        child_fk_before = db.execute("PRAGMA foreign_key_list(checkpoint_submissions)").fetchall()
    repair.repair_demo(target)
    with sqlite3.connect(target) as db:
        assert db.execute("PRAGMA table_info(checkpoints)").fetchall() == columns_before
        assert db.execute("PRAGMA foreign_key_list(checkpoint_submissions)").fetchall() == child_fk_before
        assert ("student_id","users","id") in {(r[3],r[2],r[4]) for r in db.execute("PRAGMA foreign_key_list(checkpoints)")}
        db.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO checkpoints(id,student_id,title) VALUES(999999,999999,'invalid')")
        db.rollback()
        assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_repaired_db_passes_real_backend_schema_validation(tmp_path, monkeypatch):
    target = make_legacy_copy(tmp_path)
    repair.repair_demo(target)
    before = digest(target)
    engine = create_engine("sqlite:///" + str(target),connect_args={"check_same_thread":False})
    event.listen(engine,"connect",main.set_sqlite_pragma)
    monkeypatch.setattr(main,"engine",engine)
    try:
        main.migrate_database()
        assert digest(target) == before
    finally:
        engine.dispose()


def test_repair_rolls_back_if_observations_do_not_match_approved_capacity(tmp_path, monkeypatch):
    target = make_legacy_copy(tmp_path)
    before_hash = digest(target)
    monkeypatch.setattr(repair,"CAPACITY_REPAIRS",{1:5,3:22,4:18,5:28})
    with pytest.raises(ValueError,match="approved demo capacity"):
        repair.repair_demo(target)
    assert digest(target) == before_hash
    assert digest(target.parent/"backups"/"diploma_before_compat.db") == before_hash
    assert not (target.parent/"DB_REPAIR_REPORT.json").exists()


def test_repair_refuses_original_project_and_repeated_execution(tmp_path):
    outside = tmp_path/"original"/"diploma.db"
    outside.parent.mkdir()
    shutil.copy2(ROOT/"backups"/"diploma_before_compat.db",outside)
    before = digest(outside)
    with pytest.raises(ValueError,match="separate META_AUDITED_172"):
        repair.repair_demo(outside)
    assert digest(outside) == before
    target = make_legacy_copy(tmp_path)
    repair.repair_demo(target)
    repaired_hash = digest(target)
    with pytest.raises(ValueError,match="already exists"):
        repair.repair_demo(target)
    assert digest(target) == repaired_hash


def test_historical_repair_reproduces_report_from_immutable_backup(tmp_path):
    report = json.loads((ROOT/"DB_REPAIR_REPORT.json").read_text())
    assert digest(ROOT/"backups"/"diploma_before_compat.db") == report["original_sha256"]
    target = make_legacy_copy(tmp_path)
    rebuilt = repair.repair_demo(target)
    # SQLite file bytes can differ across SQLite versions; compare every
    # historical repair observation and verify the new artifact's own checksum.
    assert digest(target) == rebuilt["repaired_sha256"]
    assert {k:v for k,v in rebuilt.items() if k != "repaired_sha256"} == {
        k:v for k,v in report.items() if k != "repaired_sha256"
    }


def test_current_database_integrity_and_signed_pdf_match_registry():
    with sqlite3.connect(f"file:{ROOT/'diploma.db'}?mode=ro",uri=True) as db:
        assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        for code,expected in db.execute("SELECT verification_code,document_hash FROM official_orders"):
            assert digest(ROOT/"uploads"/"orders"/f"order_{code}.pdf") == expected
