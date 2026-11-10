"""Offline, row-preserving compatibility repair for a META_AUDITED_172 copy.

The original project/database is never a repair target. A byte-for-byte backup
is mandatory. Historic assignments outside the identified duplicate survive.
"""
import argparse
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path


ASSIGNED = ("APPROVED", "SUPERVISOR_APPROVED", "CONFIRMED_BY_HEAD", "ALLOCATED")
APP_ID = 6
KEEP_ID = 2
CAPACITY_REPAIRS = {1: 202, 3: 22, 4: 18, 5: 28}
REPAIR_REASON = "Compatibility repair: duplicate assignment; signed OfficialOrder preserves application #2/topic #1."


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def table_rows(connection):
    names = [r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    return {name: connection.execute('SELECT * FROM "' + name.replace('"', '""') + '" ORDER BY id').fetchall() for name in names}


def repair_demo(database):
    database = Path(database).resolve()
    if database.name != "diploma.db" or database.parent.name != "META_AUDITED_172":
        raise ValueError("Repair accepts only diploma.db inside a separate META_AUDITED_172 directory")
    backup = database.parent / "backups" / "diploma_before_compat.db"
    report_file = database.parent / "DB_REPAIR_REPORT.json"
    if backup.exists() or report_file.exists():
        raise ValueError("A repair/backup already exists; preserve it and inspect before retrying")
    original_hash = sha256(database)
    backup.parent.mkdir(exist_ok=True)
    shutil.copy2(database, backup)
    assert sha256(backup) == original_hash
    connection = sqlite3.connect(database, isolation_level=None)
    try:
        # SQLite cannot add an actual FK with ALTER COLUMN. Rebuild only the one
        # legacy table in an offline transaction, preserving rows/IDs and child FKs.
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("BEGIN IMMEDIATE")
        before = table_rows(connection)
        application = connection.execute("SELECT id,student_id,topic_id,status,priority FROM applications WHERE id=?", (APP_ID,)).fetchone()
        kept = connection.execute("SELECT id,student_id,topic_id,status,priority FROM applications WHERE id=?", (KEEP_ID,)).fetchone()
        if application != (6, 2, 2, "CONFIRMED_BY_HEAD", 1) or kept != (2, 2, 1, "APPROVED", 1):
            raise ValueError("Unexpected assignment records; manual review required")
        if not connection.execute("SELECT 1 FROM order_items oi JOIN official_orders o ON o.id=oi.order_id WHERE oi.student_id=2 AND oi.topic_id=1 AND o.status IN ('SIGNED','ACTIVE')").fetchone():
            raise ValueError("The retained assignment is not supported by a signed order")

        schema = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='checkpoints'").fetchone()[0]
        checkpoint_rows = before["checkpoints"]
        indexes = connection.execute("SELECT sql FROM sqlite_master WHERE tbl_name='checkpoints' AND type IN ('index','trigger') AND sql IS NOT NULL").fetchall()
        columns = [r[1] for r in connection.execute("PRAGMA table_info(checkpoints)")]
        has_student_fk = any(r[3] == "student_id" and r[2] == "users" and r[4] == "id" for r in connection.execute("PRAGMA foreign_key_list(checkpoints)"))
        if not has_student_fk:
            rebuilt = schema.replace("CREATE TABLE checkpoints", "CREATE TABLE checkpoints__compat", 1)
            if rebuilt == schema:
                raise ValueError("Unexpected checkpoints DDL; manual review required")
            end = rebuilt.rfind(")")
            rebuilt = rebuilt[:end] + ",\n FOREIGN KEY(student_id) REFERENCES users(id)\n" + rebuilt[end:]
            connection.execute(rebuilt)
            names = ','.join('"' + name.replace('"', '""') + '"' for name in columns)
            connection.execute(f"INSERT INTO checkpoints__compat ({names}) SELECT {names} FROM checkpoints")
            connection.execute("DROP TABLE checkpoints")
            connection.execute("ALTER TABLE checkpoints__compat RENAME TO checkpoints")
            for sql, in indexes:
                connection.execute(sql)
            assert connection.execute("SELECT * FROM checkpoints ORDER BY id").fetchall() == checkpoint_rows

        # Both observed conflicts are the same pair of applications. Preserve
        # the row/priority/timestamps and only mark the extra assignment rejected.
        connection.execute("UPDATE applications SET status='REJECTED', reject_reason=? WHERE id=?", (REPAIR_REASON, APP_ID))
        marks = ','.join('?' for _ in ASSIGNED)
        quota_changes = []
        for topic_id in range(1, 6):
            old_used, old_total, old_status = connection.execute("SELECT quota_used,quota_total,status FROM topics WHERE id=?", (topic_id,)).fetchone()
            used = connection.execute(f"SELECT COUNT(*) FROM applications WHERE topic_id=? AND status IN ({marks})", (topic_id, *ASSIGNED)).fetchone()[0]
            total = CAPACITY_REPAIRS.get(topic_id, old_total)
            if total < used:
                raise ValueError("Observed assignments exceed the explicitly approved demo capacity")
            expected = {1: 202, 2: 4, 3: 22, 4: 18, 5: 28}[topic_id]
            if used != expected:
                raise ValueError("Unexpected assignment count; manual review required")
            state = "full" if used >= total else "available"
            connection.execute("UPDATE topics SET quota_used=?,quota_total=?,status=? WHERE id=?", (used, total, state, topic_id))
            quota_changes.append({"topic_id": topic_id, "before": {"quota_used": old_used, "quota_total": old_total, "status": old_status},
                                  "after": {"quota_used": used, "quota_total": total, "status": state}, "row_changed": (old_used,old_total,old_status)!=(used,total,state)})

        # Add exactly the same protective indexes required by the audited backend.
        statements = [
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_application_assigned_student ON applications(student_id) WHERE status IN ('APPROVED','SUPERVISOR_APPROVED','CONFIRMED_BY_HEAD','ALLOCATED')",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_application_active_priority ON applications(student_id,priority) WHERE status!='REJECTED'",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_applications_student_topic ON applications(student_id,topic_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_application_custom_topic ON applications(student_id,custom_topic_id) WHERE custom_topic_id IS NOT NULL",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_checkpoint_version ON checkpoint_submissions(checkpoint_id,version)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_checkpoint_student_stage ON checkpoints(student_id,stage)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_order_student ON order_items(order_id,student_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_preferences_student_priority ON preferences(student_id,priority)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_preferences_student_topic ON preferences(student_id,topic_id)",
        ]
        for sql in statements:
            connection.execute(sql)
        if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)] or connection.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("SQLite integrity validation failed")
        for sql in (
            f"SELECT student_id FROM applications WHERE status IN ({marks}) GROUP BY student_id HAVING COUNT(*)>1",
            "SELECT student_id,priority FROM applications WHERE status!='REJECTED' GROUP BY student_id,priority HAVING COUNT(*)>1",
        ):
            params = ASSIGNED if marks in sql else ()
            if connection.execute(sql,params).fetchone():
                raise ValueError("Assignment conflict remains")
        after = table_rows(connection)
        assert {k: len(v) for k,v in before.items()} == {k: len(v) for k,v in after.items()}
        for table in before:
            if table not in ("applications", "topics"):
                assert after[table] == before[table], "Unexpected data change in " + table
        changed_app_ids = [a[0] for a,b in zip(before["applications"],after["applications"]) if a!=b]
        changed_topic_ids = [a[0] for a,b in zip(before["topics"],after["topics"]) if a!=b]
        assert changed_app_ids == [6] and changed_topic_ids == [1,3,4,5]
        connection.execute("COMMIT")
        connection.execute("PRAGMA foreign_keys=ON")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        report = {"original_sha256": original_hash, "backup_sha256": sha256(backup), "repaired_sha256": sha256(database),
                  "checkpoint_fk_added": not has_student_fk, "checkpoint_rows_preserved": len(checkpoint_rows),
                  "application_change": {"id": 6, "student_id": 2, "topic_id": 2, "status_before": "CONFIRMED_BY_HEAD", "status_after": "REJECTED", "priority_unchanged": 1, "reason": REPAIR_REASON},
                  "retained_application": {"id": 2, "topic_id": 1, "status": "APPROVED", "signed_order_evidence": True},
                  "quota_changes": quota_changes, "counts_preserved": {k:len(v) for k,v in before.items()},
                  "changed_application_ids": changed_app_ids, "changed_topic_ids": changed_topic_ids,
                  "integrity_check": "ok", "foreign_key_violations": 0}
        report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", nargs="?", default=Path(__file__).with_name("diploma.db"))
    args = parser.parse_args()
    report = repair_demo(args.database)
    print(json.dumps({key:report[key] for key in ("original_sha256","repaired_sha256","changed_application_ids","changed_topic_ids","integrity_check","foreign_key_violations")},indent=2))
