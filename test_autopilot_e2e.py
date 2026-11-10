import os
import sys
import io
import hashlib
import docx
import pytest

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from fastapi.testclient import TestClient

# Ensure root dir in path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
from main import app, get_db, SessionLocal, User, Topic, Application, Checkpoint, CheckpointSubmission, OfficialOrder

@pytest.fixture(autouse=True)
def preserve_topic_quotas():
    db_init = SessionLocal()
    saved = {t.id: (t.quota_used, t.status) for t in db_init.query(Topic).all()}
    db_init.close()
    yield
    db_cleanup = SessionLocal()
    for t_id, (q_used, q_status) in saved.items():
        top = db_cleanup.query(Topic).filter(Topic.id == t_id).first()
        if top:
            top.quota_used = q_used
            top.status = q_status
    db_cleanup.commit()
    db_cleanup.close()

def test_full_autopilot_e2e():
    client = TestClient(app)

    print("\n=======================================================")
    print("▶ STARTING FULL AUTOPILOT E2E VERIFICATION SUITE")
    print("=======================================================")

    # -------------------------------------------------------------------------
    # STAGE 1: Authentication & Role Sessions
    # -------------------------------------------------------------------------
    print("\n--- Testing Stage 1: Auth & API Integration ---")
    
    # 1.1 Login as student
    res_student = client.post("/api/login", json={"email": "student@test.com", "password": "123456"})
    assert res_student.status_code == 200, f"Student login failed: {res_student.text}"
    student_token = res_student.json()["token"]
    student_user = res_student.json()["user"]
    assert student_user["role"] == "student"
    print(f"  [PASS] Student login successful: {student_user['full_name']} (token obtained)")

    # 1.2 Verify /api/auth/me session restore
    res_me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {student_token}"})
    assert res_me.status_code == 200
    assert res_me.json()["id"] == student_user["id"]
    print(f"  [PASS] Session restore /api/auth/me verified for student ID {student_user['id']}")

    # 1.3 Login as teacher
    res_teacher = client.post("/api/login", json={"email": "teacher@test.com", "password": "123456"})
    assert res_teacher.status_code == 200, f"Teacher login failed: {res_teacher.text}"
    teacher_token = res_teacher.json()["token"]
    teacher_user = res_teacher.json()["user"]
    assert teacher_user["role"] == "teacher"
    print(f"  [PASS] Teacher login successful: {teacher_user['full_name']}")

    # 1.4 Login as head
    res_head = client.post("/api/login", json={"email": "head@test.com", "password": "123456"})
    assert res_head.status_code == 200, f"Head login failed: {res_head.text}"
    head_token = res_head.json()["token"]
    head_user = res_head.json()["user"]
    assert head_user["role"] == "head"
    print(f"  [PASS] Head login successful: {head_user['full_name']}")

    # 1.5 Access without token -> 401
    res_unauth = client.get("/api/head/stats")
    assert res_unauth.status_code == 401
    print("  [PASS] Protected endpoint returns 401 without Bearer token")

    # -------------------------------------------------------------------------
    # STAGE 2: Topics Catalog & Quota Indication
    # -------------------------------------------------------------------------
    print("\n--- Testing Stage 2: Topics Catalog & Multi-language Quota ---")
    res_topics = client.get("/api/topics")
    assert res_topics.status_code == 200
    topics = res_topics.json()
    assert len(topics) > 0
    print(f"  [PASS] Loaded {len(topics)} topics from catalog")

    # Check topic fields for multilingual support
    t0 = topics[0]
    assert "title" in t0 or "title_ru" in t0
    assert "quota_total" in t0
    assert "quota_used" in t0
    print(f"  [PASS] Topic structure verified: quota {t0['quota_used']}/{t0['quota_total']}")

    # -------------------------------------------------------------------------
    # STAGE 3: Student & Teacher Workflow
    # -------------------------------------------------------------------------
    print("\n--- Testing Stage 3: Student & Teacher UX Workflow ---")

    # 3.1 Pick a topic supervised by teacher1 with available quota
    db = SessionLocal()
    target_topic = db.query(Topic).filter(
        Topic.supervisor_id == teacher_user["id"],
        Topic.quota_used < Topic.quota_total
    ).first()
    if not target_topic:
        target_topic = db.query(Topic).filter(Topic.supervisor_id == teacher_user["id"]).first()
        if target_topic:
            target_topic.quota_used = max(0, target_topic.quota_total - 2)
            db.commit()
    assert target_topic is not None, f"Teacher {teacher_user['id']} has no topics"
    assert target_topic.supervisor_id == teacher_user["id"]
    topic_id = target_topic.id
    db.close()

    # Clean existing application for this student to allow fresh apply in test
    db = SessionLocal()
    existing_apps = db.query(Application).filter(Application.student_id == student_user["id"]).all()
    for app_item in existing_apps:
        db.delete(app_item)
    db.commit()
    db.close()

    # 3.2 Student applies for topic with Priority 1
    res_apply = client.post(
        "/api/applications/apply",
        json={"topic_id": target_topic.id, "priority": 1, "motivation": "E2E Autopilot verification application"},
        headers={"Authorization": f"Bearer {student_token}"}
    )
    assert res_apply.status_code in [200, 201], f"Apply failed: {res_apply.text}"
    app_data = res_apply.json()
    app_id = app_data.get("id") or app_data.get("application_id")
    print(f"  [PASS] Student applied for Topic #{target_topic.id} (Application #{app_id}, Status: SUBMITTED)")

    # 3.3 Teacher decides on application -> APPROVE
    res_decide = client.post(
        "/api/applications/decide",
        json={"application_id": app_id, "decision": "APPROVE", "reason": "Motivation and skills approved"},
        headers={"Authorization": f"Bearer {teacher_token}"}
    )
    assert res_decide.status_code == 200, f"Teacher approve failed: {res_decide.text}"
    print(f"  [PASS] Teacher approved Application #{app_id} (Status: APPROVED)")

    # 3.4 Student checks status & statement export
    res_student_apps = client.get(
        f"/api/student-applications/{student_user['id']}",
        headers={"Authorization": f"Bearer {student_token}"}
    )
    assert res_student_apps.status_code == 200
    my_apps = res_student_apps.json()
    assert any(a["id"] == app_id and a["status"] == "APPROVED" for a in my_apps)
    print("  [PASS] Student dashboard reflects APPROVED status")

    res_stmt = client.get(f"/api/export/statement/{app_id}", headers={"Authorization": f"Bearer {student_token}"})
    assert res_stmt.status_code == 200
    assert "text/html" in res_stmt.headers.get("content-type", "")
    print("  [PASS] Student statement (.html) generated successfully")

    # 3.5 Student Checkpoints: upload stage 25% report
    res_cps = client.get(f"/api/checkpoints/{student_user['id']}", headers={"Authorization": f"Bearer {student_token}"})
    assert res_cps.status_code == 200
    cps_data = res_cps.json()
    checkpoints = cps_data.get("checkpoints", [])
    assert len(checkpoints) >= 4, "Expected 4 thesis checkpoints"
    cp_25 = next((c for c in checkpoints if c["stage"] == 25), checkpoints[0])
    print(f"  [PASS] Thesis Checkpoints loaded: {len(checkpoints)} stages (25%, 50%, 75%, 100%)")

    # Upload dummy docx report for 25%
    dummy_doc = docx.Document()
    dummy_doc.add_heading("Отчет по этапу 25%", 0)
    dummy_doc.add_paragraph("Литературный обзор и постановка задачи дипломного проекта.")
    doc_io = io.BytesIO()
    dummy_doc.save(doc_io)
    doc_bytes = doc_io.getvalue()

    res_up1 = client.post(
        "/api/checkpoints/upload",
        data={"checkpoint_id": cp_25["id"]},
        files={"file": ("report_v1.docx", doc_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        headers={"Authorization": f"Bearer {student_token}"}
    )
    assert res_up1.status_code == 200, f"Checkpoint upload v1 failed: {res_up1.text}"
    print(f"  [PASS] Student uploaded report v1 for checkpoint #{cp_25['id']}")

    # 3.6 Teacher reviews checkpoint -> REVISION
    res_rev = client.post(
        "/api/checkpoints/review",
        json={"checkpoint_id": cp_25["id"], "status": "REVISION", "comment": "Уточнить раздел 1.2 по ГОСТ"},
        headers={"Authorization": f"Bearer {teacher_token}"}
    )
    assert res_rev.status_code == 200, f"Teacher review failed: {res_rev.text}"
    print("  [PASS] Teacher submitted revision request with comment")

    # 3.7 Student uploads report v2
    res_up2 = client.post(
        "/api/checkpoints/upload",
        data={"checkpoint_id": cp_25["id"]},
        files={"file": ("report_v2.docx", doc_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        headers={"Authorization": f"Bearer {student_token}"}
    )
    assert res_up2.status_code == 200
    print("  [PASS] Student uploaded revised version 2")

    # Check history
    res_hist = client.get(f"/api/checkpoints/{cp_25['id']}/history", headers={"Authorization": f"Bearer {student_token}"})
    assert res_hist.status_code == 200
    history_items = res_hist.json()
    assert len(history_items) >= 2
    print(f"  [PASS] Version history tracked: {len(history_items)} revisions")

    # 3.8 Teacher accepts checkpoint -> ACCEPTED
    res_acc = client.post(
        "/api/checkpoints/review",
        json={"checkpoint_id": cp_25["id"], "status": "ACCEPTED", "comment": "Замечания устранены, этап зачтен"},
        headers={"Authorization": f"Bearer {teacher_token}"}
    )
    assert res_acc.status_code == 200
    print("  [PASS] Teacher accepted 25% checkpoint (Status: ACCEPTED)")

    # -------------------------------------------------------------------------
    # STAGE 4: Head of Department, Allocation, Decree & Norm Control
    # -------------------------------------------------------------------------
    print("\n--- Testing Stage 4: Head Dashboard, Gale-Shapley, Decree & Norm Control ---")

    # 4.1 Head stats
    res_stats = client.get("/api/head/stats", headers={"Authorization": f"Bearer {head_token}"})
    assert res_stats.status_code == 200
    stats = res_stats.json()
    assert "students_total" in stats or "total_students" in stats
    assert "active_order" in stats
    print(f"  [PASS] Head stats retrieved: {stats.get('students_total', 0)} students, active order: {stats['active_order']['order_number'] if stats['active_order'] else 'None'}")

    # 4.2 Gale-Shapley Dry Run (preview)
    res_gs_dry = client.post("/api/allocation/gale-shapley?dry_run=true", headers={"Authorization": f"Bearer {head_token}"})
    assert res_gs_dry.status_code == 200
    gs_preview = res_gs_dry.json()
    assert "stats" in gs_preview
    print(f"  [PASS] Gale-Shapley preview calculated: satisfaction {gs_preview['stats'].get('satisfaction_percent', 0)}%")

    # 4.3 Gale-Shapley Apply
    res_gs_apply = client.post("/api/allocation/gale-shapley?apply=true", headers={"Authorization": f"Bearer {head_token}"})
    assert res_gs_apply.status_code == 200
    print("  [PASS] Gale-Shapley matching committed to database")

    # 4.4 Confirm all stream to decree
    res_conf_all = client.post("/api/head/confirm-all", headers={"Authorization": f"Bearer {head_token}"})
    assert res_conf_all.status_code == 200
    print("  [PASS] Stream confirmed into official decree (POST /api/head/confirm-all)")

    # 4.5 Official Decree DOCX export
    res_docx = client.get("/api/export/official-decree-docx", headers={"Authorization": f"Bearer {head_token}"})
    assert res_docx.status_code == 200
    assert len(res_docx.content) > 1000
    print(f"  [PASS] Official decree DOCX generated ({len(res_docx.content)} bytes)")

    # 4.6 Official Decree PDF with QR & SHA-256 hash
    res_pdf = client.get("/api/export/pdf-order", headers={"Authorization": f"Bearer {head_token}"})
    assert res_pdf.status_code == 200
    assert res_pdf.content.startswith(b"%PDF"), "Response is not a valid PDF file"
    pdf_hash = res_pdf.headers.get("X-Document-Hash")
    ver_code = res_pdf.headers.get("X-Verification-Code")
    assert pdf_hash and len(pdf_hash) == 64, f"Invalid SHA-256 hash in header: {pdf_hash}"
    calculated_hash = hashlib.sha256(res_pdf.content).hexdigest()
    assert pdf_hash == calculated_hash, f"Hash mismatch: header {pdf_hash} != bytes {calculated_hash}"
    print(f"  [PASS] Official decree PDF generated ({len(res_pdf.content)} bytes)")
    print(f"         SHA-256 Hash: {pdf_hash}")
    print(f"         Verification Code: {ver_code}")

    # 4.7 Public Verification by QR-code endpoint
    res_verify = client.get(f"/verify/{ver_code}")
    assert res_verify.status_code == 200
    assert pdf_hash in res_verify.text, "Document SHA-256 hash not shown on verification page"
    assert "Зарегистрирован в реестре META" in res_verify.text or "Реестр" in res_verify.text
    print(f"  [PASS] Public QR verification page /verify/{ver_code} displays verified hash & registry status")

    # 4.8 Norm-control (.docx) check
    # Create docx with standard formatting
    norm_doc = docx.Document()
    sec = norm_doc.sections[0]
    sec.left_margin = docx.shared.Mm(30)
    sec.right_margin = docx.shared.Mm(10)
    sec.top_margin = docx.shared.Mm(20)
    sec.bottom_margin = docx.shared.Mm(20)

    p = norm_doc.add_paragraph("Тема дипломного проекта: Разработка интеллектуальной системы META.")
    p.paragraph_format.line_spacing = 1.5
    run = p.runs[0]
    run.font.name = "Times New Roman"
    run.font.size = docx.shared.Pt(14)

    norm_io = io.BytesIO()
    norm_doc.save(norm_io)
    norm_bytes = norm_io.getvalue()

    res_norm = client.post(
        "/api/norm-control/check-docx",
        files={"file": ("thesis_test.docx", norm_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
    )
    assert res_norm.status_code == 200
    norm_result = res_norm.json()
    assert "score" in norm_result
    assert "checks" in norm_result
    print(f"  [PASS] Norm control check completed: Score {norm_result['score']}/100, Status: {norm_result['status']}")

    print("\n=======================================================")
    print("✅ ALL 5 AUTOPILOT STAGES PASSED E2E VERIFICATION")
    print("=======================================================\n")

if __name__ == "__main__":
    test_full_autopilot_e2e()
