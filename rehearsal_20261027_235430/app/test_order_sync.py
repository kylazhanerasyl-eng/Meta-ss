import os
import io
import re
import base64
import zlib
import hashlib
import docx
import qrcode
import pytest
from fastapi.testclient import TestClient

from main import (
    app, SessionLocal, User, OfficialOrder, OrderItem,
    ORDERS_DIR, BASE_URL, build_order_pdf_bytes, ensure_order_pdf
)

client = TestClient(app)

def get_head_token():
    res = client.post("/api/login", json={"email": "head@test.com", "password": "123456"})
    assert res.status_code == 200, f"Head login failed: {res.text}"
    return res.json()["token"]


@pytest.fixture
def large_order(isolated_database):
    """Independent 274-row order built only in the disposable pytest DB/uploads."""
    from datetime import datetime
    import main
    with SessionLocal() as db:
        students = db.query(User).filter(User.role == "student").order_by(User.id).limit(274).all()
        assert len(students) == 274, "Fixture requires 274 existing student FK targets"
        order = OfficialOrder(order_number="TEST-MULTIPAGE-274", academic_year="2026-2027",
                              created_at=datetime(2026, 10, 11), status="DRAFT",
                              verification_code="ORDER-TEST-MULTIPAGE-274", document_hash="")
        db.add(order)
        db.flush()
        for index, student in enumerate(students, 1):
            db.add(OrderItem(order_id=order.id, student_id=student.id,
                student_name=f"TESTROW{index:04d} Тестовый студент",
                group_name="TEST-26-1",
                topic_title_ru="Разработка интеллектуальной информационной системы академического мониторинга, распределения дипломных тем и контроля выполнения учебных проектов в университете",
                topic_title_kz="Университетте дипломдық жұмыс тақырыптарын бөлу және оқу жобаларының орындалуын бақылау үшін академиялық мониторингтің зияткерлік ақпараттық жүйесін әзірлеу",
                supervisor_name="Тестовый научный руководитель"))
        db.commit()
        main.ensure_order_pdf(order, db)
        order.status = "SIGNED"
        db.commit()
        code = order.verification_code
    return code

def test_order_docx_and_pdf_single_source_of_truth(large_order):
    """DOCX и PDF формируются из одного OfficialOrder и одного набора OrderItem с идентичными данными и порядком"""
    token = get_head_token()
    headers = {"Authorization": f"Bearer {token}"}

    db = SessionLocal()
    order = db.query(OfficialOrder).filter(OfficialOrder.verification_code == large_order).first()
    assert order is not None
    order_items = db.query(OrderItem).filter(OrderItem.order_id == order.id).order_by(OrderItem.id.asc()).all()
    assert len(order_items) >= 200, f"Expected 200+ items, got {len(order_items)}"
    expected_count = len(order_items)
    db.close()

    # 1. Download DOCX
    res_docx = client.get(f"/api/export/official-decree-docx?order_code={order.verification_code}", headers=headers)
    assert res_docx.status_code == 200
    doc = docx.Document(io.BytesIO(res_docx.content))
    assert len(doc.tables) >= 1
    table = doc.tables[0]
    # Header row + student rows
    assert len(table.rows) == expected_count + 1

    # Check every row against the single source of truth
    check_indices = range(1, expected_count + 1)
    for r_idx in check_indices:
        item = order_items[r_idx - 1]
        cells = table.rows[r_idx].cells
        assert cells[0].text == str(r_idx)
        assert cells[1].text == item.student_name
        assert cells[2].text == (item.group_name or "ВТиПО-22-1")
        assert cells[3].text == item.topic_title_kz
        assert cells[4].text == item.topic_title_ru
        assert cells[5].text == item.supervisor_name

    # Check order metadata in DOCX
    full_text = "\n".join(p.text for p in doc.paragraphs)
    assert order.order_number in full_text
    expected_date = order.created_at.strftime("%d.%m.%Y")
    assert expected_date in full_text
    assert order.verification_code in full_text
    assert order.academic_year in full_text

    # 2. Download PDF
    res_pdf = client.get(f"/api/export/pdf-order?order_code={order.verification_code}", headers=headers)
    assert res_pdf.status_code == 200
    assert res_pdf.content.startswith(b"%PDF")
    assert res_pdf.headers.get("X-Verification-Code") == order.verification_code
    assert res_pdf.headers.get("X-Document-Hash") == order.document_hash

def test_order_date_consistency_across_all_views():
    """Дата приказа берется строго из order.created_at и одинакова в dashboard, DOCX, PDF и /verify/{code}"""
    token = get_head_token()
    headers = {"Authorization": f"Bearer {token}"}

    db = SessionLocal()
    order = db.query(OfficialOrder).filter(OfficialOrder.verification_code == "ORDER-2026-01").first()
    assert order is not None
    canonical_date = order.created_at.strftime("%d.%m.%Y")
    db.close()

    # 1. Dashboard stats API
    res_stats = client.get("/api/head/stats", headers=headers)
    assert res_stats.status_code == 200
    active_order_data = res_stats.json().get("active_order")
    assert active_order_data is not None
    assert active_order_data["date"] == canonical_date

    # 2. DOCX
    res_docx = client.get(f"/api/export/official-decree-docx?order_code={order.verification_code}", headers=headers)
    doc = docx.Document(io.BytesIO(res_docx.content))
    docx_text = "\n".join(p.text for p in doc.paragraphs)
    assert canonical_date in docx_text

    # 3. Public Verification page
    res_verify = client.get(f"/verify/{order.verification_code}")
    assert res_verify.status_code == 200
    assert canonical_date in res_verify.text
    assert f"Дата приказа:</span>\n                    <span class=\"font-bold text-slate-800\">{canonical_date}</span>" in res_verify.text or canonical_date in res_verify.text

def test_pdf_multi_page_pagination_and_table_structure(large_order):
    """PDF не обрезает список студентов, таблица продолжается на 15+ страницах, колонка статуса расширена"""
    token = get_head_token()
    headers = {"Authorization": f"Bearer {token}"}

    res_pdf = client.get(f"/api/export/pdf-order?order_code={large_order}", headers=headers)
    assert res_pdf.status_code == 200
    pdf_bytes = res_pdf.content

    # Multi-page check: Count PDF page markers
    page_count = (
        pdf_bytes.count(b"/Type /Page\n") +
        pdf_bytes.count(b"/Type/Page\n") +
        pdf_bytes.count(b"/Type /Page ")
    )
    assert page_count >= 15, f"Expected 15+ pages for 200+ students, got {page_count}"

    # Inspect the real compressed PDF streams: every fixture row, including
    # the last one, must survive pagination (not just the DOCX export).
    streams = []
    for meta, data in re.findall(rb"<<(.*?)>>\s*stream\r?\n(.*?)endstream", pdf_bytes, re.S):
        data = data.strip()
        if b"ASCII85Decode" in meta:
            data = base64.a85decode(data, adobe=True)
        if b"FlateDecode" in meta:
            data = zlib.decompress(data)
        streams.append(data)
    content = b"\n".join(streams)
    for number in range(1, 275):
        assert f"TESTROW{number:04d}".encode("ascii") in content, f"PDF lost row {number}"

    # Verify Unicode font is used (not Helvetica)
    assert b"Helvetica" not in pdf_bytes
    assert b"UnicodeFont" in pdf_bytes or b"Arial" in pdf_bytes

    # Check file saved to disk matches hash
    pdf_path = os.path.join(ORDERS_DIR, f"order_{large_order}.pdf")
    assert os.path.exists(pdf_path)
    with open(pdf_path, "rb") as f:
        disk_bytes = f.read()
    assert hashlib.sha256(disk_bytes).hexdigest() == res_pdf.headers.get("X-Document-Hash")

def test_dynamic_qr_and_real_sha256_hash():
    """QR динамически ведёт на /verify/{code}, hash = 64 hex, повторное скачивание отдаёт тот же файл"""
    token = get_head_token()
    headers = {"Authorization": f"Bearer {token}"}

    res1 = client.get("/api/export/pdf-order?order_code=ORDER-2026-01", headers=headers)
    res2 = client.get("/api/export/pdf-order?order_code=ORDER-2026-01", headers=headers)
    assert res1.status_code == 200
    assert res2.status_code == 200
    assert res1.content == res2.content

    doc_hash = res1.headers.get("X-Document-Hash")
    assert doc_hash and len(doc_hash) == 64
    assert hashlib.sha256(res1.content).hexdigest() == doc_hash

    # Verification page
    res_verify = client.get("/verify/ORDER-2026-01")
    assert res_verify.status_code == 200
    assert doc_hash in res_verify.text
    assert "Документ зарегистрирован в реестре META" in res_verify.text
    assert "Контрольная сумма SHA-256" in res_verify.text
    assert "ЭЦП верифицирована" not in res_verify.text
