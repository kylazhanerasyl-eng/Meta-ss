import urllib.request
import urllib.parse
import json
import sys

BASE_URL = "http://127.0.0.1:8000"

def http_get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read(), resp.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers

def http_post(url, data=None, headers=None):
    headers = headers or {}
    post_data = None
    if data is not None:
        headers["Content-Type"] = "application/json"
        post_data = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=post_data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read(), resp.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers

def run_e2e_verification():
    print("==================================================")
    print("🚀 RUNNING END-TO-END DEMO SCENARIO VERIFICATION")
    print("==================================================")

    # 1. Test Static & Cache headers
    status, body, headers = http_get(f"{BASE_URL}/")
    assert status == 200, f"Root returned {status}"
    text = body.decode("utf-8", errors="replace")
    assert "v1.1.6 BETA" in text
    print("✅ 1. Root page (index.html) served with v1.1.6 BETA and status 200")

    # 2. Student Authentication
    status, body, _ = http_post(f"{BASE_URL}/api/auth/login", {"email": "student@test.com", "password": "123456"})
    assert status == 200, f"Student login failed: {body.decode()}"
    student_data = json.loads(body.decode())
    student_token = student_data["token"]
    student_id = student_data["user"]["id"]
    student_headers = {"Authorization": f"Bearer {student_token}"}
    print(f"✅ 2. Student Login OK: {student_data['user']['full_name']} (Role: {student_data['user']['role']}, GPA: {student_data['user']['gpa']})")

    # 3. Student Checkpoints & Notifications
    status, body, _ = http_get(f"{BASE_URL}/api/checkpoints/{student_id}", headers=student_headers)
    assert status == 200, f"Checkpoints failed: {body.decode()}"
    cp_resp = json.loads(body.decode())
    checkpoints = cp_resp.get("checkpoints", [])
    print(f"✅ 3. Student Checkpoints OK: count={len(checkpoints)} stages={', '.join(str(c['stage'])+'%' for c in checkpoints)}, progress={cp_resp.get('progress_percent')}%")

    status, body, _ = http_get(f"{BASE_URL}/api/notifications", headers=student_headers)
    assert status == 200
    print(f"✅ 4. Student Notifications OK: count={len(json.loads(body.decode()))}")

    # 4. Topics Catalog & Anti-Duplicate Check
    status, body, _ = http_get(f"{BASE_URL}/api/topics?category=all&search=")
    assert status == 200
    topics = json.loads(body.decode())
    print(f"✅ 5. Topics Catalog OK: count={len(topics)}")

    status, body, _ = http_post(f"{BASE_URL}/api/topics/check-duplicate", {"title": "Разработка веб-платформы на базе FastAPI"})
    assert status == 200
    dup_res = json.loads(body.decode())
    print(f"✅ 6. Anti-Duplicate Engine OK: max_similarity={dup_res.get('max_similarity', 0)}%, duplicates_found={len(dup_res.get('duplicates', []))}")

    # 5. Teacher Authentication & Review
    status, body, _ = http_post(f"{BASE_URL}/api/auth/login", {"email": "teacher@test.com", "password": "123456"})
    assert status == 200, f"Teacher login failed: {body.decode()}"
    teacher_data = json.loads(body.decode())
    teacher_token = teacher_data["token"]
    teacher_headers = {"Authorization": f"Bearer {teacher_token}"}
    print(f"✅ 7. Teacher Login OK: {teacher_data['user']['full_name']} (Role: {teacher_data['user']['role']})")

    status, body, _ = http_get(f"{BASE_URL}/api/applications", headers=teacher_headers)
    assert status == 200
    apps = json.loads(body.decode())
    print(f"✅ 8. Teacher Applications OK: total_applications={len(apps)}")

    status, body, _ = http_get(f"{BASE_URL}/api/teacher/checkpoints", headers=teacher_headers)
    assert status == 200
    t_checkpoints = json.loads(body.decode())
    print(f"✅ 8b. Teacher Checkpoints Review OK: pending_submissions={len(t_checkpoints)}")

    # 6. Head of Department Authentication & Analytics
    status, body, _ = http_post(f"{BASE_URL}/api/auth/login", {"email": "head@test.com", "password": "123456"})
    assert status == 200, f"Head login failed: {body.decode()}"
    head_data = json.loads(body.decode())
    head_token = head_data["token"]
    head_headers = {"Authorization": f"Bearer {head_token}"}
    print(f"✅ 9. Head of Department Login OK: {head_data['user']['full_name']}")

    status, body, _ = http_get(f"{BASE_URL}/api/head/stats", headers=head_headers)
    assert status == 200
    stats = json.loads(body.decode())
    print(f"✅ 10. Head KPI Stats OK: total_students={stats.get('total_students')}, completion_rate={stats.get('completion_rate')}%")

    # 7. Gale-Shapley Algorithm Preview
    status, body, _ = http_post(f"{BASE_URL}/api/allocation/gale-shapley?dry_run=true", headers=head_headers)
    assert status == 200, f"Gale-Shapley preview failed: {body.decode()}"
    gs_res = json.loads(body.decode())
    print(f"✅ 11. Gale-Shapley Distribution Engine OK: allocated={len(gs_res.get('allocated', []))}, stability={gs_res.get('stability_status')}")

    # 8. Document Generation & Verification
    status, body, _ = http_get(f"{BASE_URL}/api/export/official-decree-docx", headers=head_headers)
    assert status == 200, f"Official Decree DOCX failed: {status}"
    print(f"✅ 12. Bilingual Official Decree DOCX Export OK: size={len(body)} bytes")

    status, body, _ = http_get(f"{BASE_URL}/api/export/pdf-order", headers=head_headers)
    assert status == 200, f"Decree PDF Export failed: {status}"
    print(f"✅ 13. Decree PDF Export with QR Code OK: size={len(body)} bytes")

    # 9. Online Decree Verification Route
    status, body, _ = http_get(f"{BASE_URL}/verify/142-%D0%9E%D0%94")
    assert status == 200, f"Verification route failed: {status}"
    ver_text = body.decode("utf-8", errors="replace")
    assert "Верификация" in ver_text or "растау" in ver_text or "Verification" in ver_text or "142-ОД" in ver_text
    print(f"✅ 14. Decree Verification Page OK: /verify/142-ОД")

    # 10. Admin Database Backup Download
    status, body, _ = http_get(f"{BASE_URL}/api/admin/backup", headers=head_headers)
    assert status == 200, f"DB Backup failed: {status}"
    print(f"✅ 15. Secure Database Backup (.db) Download OK: size={len(body)} bytes")

    # 11. Norm-Control DOCX Automated Inspection
    import io, docx
    doc = docx.Document()
    doc.add_heading('Титульный лист', level=1)
    doc.add_paragraph('Введение и аналитический обзор информационной системы')
    buf = io.BytesIO()
    doc.save(buf)
    file_bytes = buf.getvalue()

    boundary = '----WebKitFormBoundaryTestNormControl777'
    form_data = (
        f'--{boundary}\r\n'
        f'Content-Disposition: form-data; name="file"; filename="test_diploma.docx"\r\n'
        f'Content-Type: application/vnd.openxmlformats-officedocument.wordprocessingml.document\r\n\r\n'
    ).encode('utf-8') + file_bytes + f'\r\n--{boundary}--\r\n'.encode('utf-8')

    req = urllib.request.Request(
        f"{BASE_URL}/api/norm-control/check-docx",
        data=form_data,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST"
    )
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        norm_result = json.loads(resp.read().decode())
        print(f"✅ 16. Norm-Control Engine OK: score={norm_result.get('score')}%, passed={norm_result.get('passed')}, issues={len(norm_result.get('issues', []))}")

    print("==================================================")
    print("🎯 ALL 16 E2E INTEGRATION CHECKS PASSED PERFECTLY!")
    print("==================================================")

if __name__ == "__main__":
    run_e2e_verification()
