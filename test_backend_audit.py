"""Security, transactional and document-integrity regressions on disposable databases."""
import asyncio
import hashlib
import io
import sqlite3
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import docx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text, event
from sqlalchemy.exc import IntegrityError
from starlette.websockets import WebSocketDisconnect

import main as m


TEACHER_ID = HEAD_ID = DEFAULT_CHECKPOINT = 0


@pytest.fixture(autouse=True)
def audit_ids(isolated_database):
    global TEACHER_ID, HEAD_ID, DEFAULT_CHECKPOINT
    with m.SessionLocal() as db:
        TEACHER_ID = db.query(m.User).filter_by(email="teacher@test.com").one().id
        HEAD_ID = db.query(m.User).filter_by(email="head@test.com").one().id
        m.ensure_student_checkpoints(db, 1)
        db.commit()
        DEFAULT_CHECKPOINT = db.query(m.Checkpoint).filter_by(student_id=1).order_by(m.Checkpoint.stage).first().id


def resolve_path(path):
    return path.replace("/checkpoints/download/1", f"/checkpoints/download/{DEFAULT_CHECKPOINT}").replace("/checkpoints/1/history", f"/checkpoints/{DEFAULT_CHECKPOINT}/history")


@pytest.fixture
def client():
    return TestClient(m.app)


def auth(client, email="student@test.com"):
    response = client.post("/api/login", json={"email": email, "password": "123456"})
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["token"]}


def student(client, suffix="audit", **fields):
    response = client.post("/api/register", json={"full_name": "Audit Student", "email": suffix + "@audit.test", "password": "123456", **fields})
    assert response.status_code == 200, response.text
    return response.json()["user"]["id"], {"Authorization": "Bearer " + response.json()["token"]}


def make_topic(supervisor=None, quota=3, **fields):
    supervisor = TEACHER_ID if supervisor is None else supervisor
    with m.SessionLocal() as db:
        topic = m.Topic(title="Audit Topic", title_ru="Audit Topic", title_kz="Audit KZ", supervisor_id=supervisor,
                        supervisor_name="Доц. Ахметов К.А.", quota_total=quota, quota_used=0, status="available", **fields)
        db.add(topic)
        db.commit()
        return topic.id


def apply(client, headers, topic_id=1, priority=1):
    response = client.post("/api/applications/apply", headers=headers, json={"topic_id": topic_id, "priority": priority})
    assert response.status_code == 200, response.text
    return response.json()["application_id"]


def decide(client, headers, application_id, decision="APPROVE", **fields):
    return client.post("/api/applications/decide", headers=headers,
                       json={"application_id": application_id, "decision": decision, **fields})


PRIVATE_READS = [
    "/api/applications", "/api/student-applications/1", "/api/preferences?student_id=1",
    "/api/recommendations?student_id=1", "/api/checkpoints/1", "/api/checkpoints?student_id=1",
    "/api/checkpoints/download/1", "/api/checkpoints/1/history", "/api/teacher/checkpoints",
    "/api/risks", "/api/teachers/load", "/api/company/bids?company_id=8", "/uploads/known.pdf",
]


@pytest.mark.parametrize("path", PRIVATE_READS)
def test_private_reads_require_auth(client, path):
    assert client.get(resolve_path(path)).status_code == 401


@pytest.mark.parametrize("path,body", [
    ("/api/applications/apply", {"student_id": 1, "topic_id": 1}),
    ("/api/preferences", {"student_id": 1, "topic_ids": [1]}),
    ("/api/checkpoints/review", {"checkpoint_id": 1, "status": "ACCEPTED"}),
    ("/api/checkpoints/done", {"checkpoint_id": 1}),
    ("/api/company/topics", {"company_id": 8, "title": "Forged", "supervisor_name": "Fake"}),
])
def test_mutations_require_auth(client, path, body):
    assert client.post(path, json=body).status_code == 401
    with m.SessionLocal() as db:
        assert db.query(m.Application).count() == 0
        assert db.query(m.Preference).count() == 0
        assert db.get(m.Checkpoint, DEFAULT_CHECKPOINT).status == "NOT_SUBMITTED"


def test_upload_requires_auth_without_side_effects(client):
    before = set(Path(m.UPLOADS_DIR).rglob("*"))
    response = client.post("/api/checkpoints/upload", data={"checkpoint_id": DEFAULT_CHECKPOINT}, files={"file": ("report.pdf", b"%PDF-1.4 data")})
    assert response.status_code == 401
    assert set(Path(m.UPLOADS_DIR).rglob("*")) == before


@pytest.mark.parametrize("path", ["/api/student-applications/1", "/api/checkpoints/1", "/api/checkpoints?application_id=999999",
                                     "/api/checkpoints/1/history", "/api/checkpoints/download/1", "/api/preferences?student_id=1", "/api/recommendations?student_id=1"])
def test_student_idor_reads(client, path):
    _, headers = student(client)
    response = client.get(resolve_path(path), headers=headers)
    if "999999" in path:
        assert response.status_code in (200, 404) and response.json() == []
    else:
        assert response.status_code == 403


def test_student_and_role_spoofing(client):
    sid, headers = student(client)
    assert client.post("/api/applications/apply", headers=headers, json={"student_id": 1, "topic_id": 1}).status_code == 403
    assert client.post("/api/preferences", headers=headers, json={"student_id": 1, "topic_ids": [1]}).status_code == 403
    assert client.post("/api/checkpoints/upload", headers=headers, data={"checkpoint_id": DEFAULT_CHECKPOINT}, files={"file": ("report.pdf", b"%PDF-1.4 test")}).status_code == 403
    assert client.post("/api/checkpoints/done", headers=headers, json={"checkpoint_id": DEFAULT_CHECKPOINT}).status_code == 403
    app_id = apply(client, headers)
    own = client.get("/api/applications", headers=headers)
    assert [item["id"] for item in own.json()] == [app_id]
    assert all(item["student_id"] == sid for item in own.json())


def test_teacher_ownership_uses_id_not_name(client):
    _, headers = student(client)
    application = apply(client, headers, make_topic(supervisor=HEAD_ID))
    teacher = auth(client, "teacher@test.com")
    # The topic deliberately has teacher@test.com's display name but a different owner ID.
    assert decide(client, teacher, application).status_code == 403
    assert client.get(f"/api/export/statement/{application}", headers=teacher).status_code == 403
    assert client.get("/api/applications", headers=teacher).json() == []


def test_unrelated_teacher_cannot_review_or_read_report(client):
    sid, headers = student(client)
    app_id = apply(client, headers, make_topic(supervisor=HEAD_ID))
    assert decide(client, auth(client, "head@test.com"), app_id).status_code == 200
    checkpoint = client.get(f"/api/checkpoints/{sid}", headers=headers).json()["checkpoints"][0]["id"]
    uploaded = client.post("/api/checkpoints/upload", headers=headers, data={"checkpoint_id": checkpoint}, files={"file": ("report.pdf", b"%PDF-1.4 data")})
    assert uploaded.status_code == 200
    unrelated = auth(client, "teacher@test.com")
    for url in (f"/api/checkpoints/{sid}", f"/api/checkpoints/download/{checkpoint}", f"/api/checkpoints/{checkpoint}/history", "/uploads/" + uploaded.json()["file_name"]):
        assert client.get(url, headers=unrelated).status_code == 403
    assert client.post("/api/checkpoints/review", headers=unrelated, json={"checkpoint_id": checkpoint, "status": "ACCEPTED"}).status_code == 403
    assert client.get("/api/teacher/checkpoints", headers=unrelated).json() == []
    own_download = client.get("/uploads/" + uploaded.json()["file_name"], headers=headers)
    assert own_download.status_code == 200
    assert own_download.headers["X-Content-Type-Options"] == "nosniff"
    assert "attachment" in own_download.headers["Content-Disposition"]


def test_public_signup_cannot_inflate_ranking(client):
    sid, _ = student(client, gpa=4.0, rating=4.0)
    with m.SessionLocal() as db:
        assert db.get(m.User, sid).gpa == 3.5


@pytest.mark.parametrize("extra", [{"full_name": " "}, {"email": "invalid"}, {"password": "x"}, {"gpa": 5}, {"gpa": -1}])
def test_invalid_registration(client, extra):
    assert client.post("/api/register", json={"full_name": "Test", "email": "valid@audit.test", "password": "123456", **extra}).status_code == 422


def test_company_identity_and_quota(client):
    company = auth(client, "company@test.com")
    with m.SessionLocal() as db:
        cid = db.query(m.User).filter_by(role="company").first().id
    data = {"company_id": cid, "title": "Legitimate", "supervisor_name": "Employee", "quota_total": 0}
    assert client.post("/api/company/topics", headers=company, json=data).status_code == 422
    assert client.post("/api/company/topics", headers=company, json={**data, "company_id": cid + 1, "quota_total": 2}).status_code == 403
    assert client.get(f"/api/company/bids?company_id={cid+1}", headers=company).status_code == 403
    response = client.post("/api/company/topics", headers=company, json={**data, "quota_total": 2})
    assert response.status_code == 200
    with m.SessionLocal() as db:
        assert db.get(m.Topic, response.json()["topic_id"]).supervisor_id == cid


def test_preferences_validation_is_atomic(client):
    sid, headers = student(client)
    good = {"student_id": sid, "topic_ids": [1, 3]}
    assert client.post("/api/preferences", headers=headers, json=good).status_code == 200
    for topic_ids, status in (([1, 1], 400), ([1, 2, 3, 4], 400), ([1, 99999999], 404)):
        assert client.post("/api/preferences", headers=headers, json={**good, "topic_ids": topic_ids}).status_code == status
        assert [p["topic_id"] for p in client.get(f"/api/preferences?student_id={sid}", headers=headers).json()] == [1, 3]


@pytest.mark.parametrize("body,status", [({}, 400), ({"topic_id": -1}, 400), ({"topic_id": 99999999}, 404),
                                         ({"topic_id": 1, "custom_topic_id": 1}, 400), ({"topic_id": "bad"}, 422)])
def test_invalid_application_inputs(client, body, status):
    _, headers = student(client)
    assert client.post("/api/applications/apply", headers=headers, json=body).status_code == status
    with m.SessionLocal() as db:
        assert db.query(m.Application).count() == 0


def concurrent(function, items):
    barrier = threading.Barrier(len(items))
    def run(item):
        barrier.wait(timeout=10)
        return function(item)
    with ThreadPoolExecutor(max_workers=len(items)) as executor:
        return list(executor.map(run, items))


def test_concurrent_duplicate_application(client):
    sid, headers = student(client)
    responses = concurrent(lambda _: client.post("/api/applications/apply", headers=headers, json={"topic_id": 1, "priority": 1}), [1, 2])
    assert sorted(r.status_code for r in responses) == [200, 400]
    with m.SessionLocal() as db:
        assert db.query(m.Application).filter_by(student_id=sid).count() == 1
        assert db.query(m.Preference).filter_by(student_id=sid).count() == 1


def test_concurrent_double_approval(client):
    _, headers = student(client)
    topic = make_topic(quota=3)
    app_id = apply(client, headers, topic)
    teacher = auth(client, "teacher@test.com")
    results = concurrent(lambda _: decide(client, teacher, app_id), [1, 2])
    assert sorted(r.status_code for r in results) == [200, 400]
    with m.SessionLocal() as db:
        assert db.get(m.Topic, topic).quota_used == 1
        assert db.get(m.Application, app_id).status == "APPROVED"


def test_concurrent_two_topics_one_student(client):
    sid, headers = student(client)
    topics = [make_topic(), make_topic()]
    apps = [apply(client, headers, t, i+1) for i, t in enumerate(topics)]
    teacher = auth(client, "teacher@test.com")
    results = concurrent(lambda a: decide(client, teacher, a), apps)
    assert sorted(r.status_code for r in results) == [200, 400]
    with m.SessionLocal() as db:
        assert db.query(m.Application).filter(m.Application.student_id == sid, m.Application.status.in_(m.ASSIGNED_STATUSES)).count() == 1
        assert sum(db.get(m.Topic, t).quota_used for t in topics) == 1


def test_concurrent_last_quota_place(client):
    topic = make_topic(quota=1)
    apps = [apply(client, student(client, suffix=f"race{i}")[1], topic) for i in range(3)]
    teacher = auth(client, "teacher@test.com")
    results = concurrent(lambda a: decide(client, teacher, a), apps)
    assert sorted(r.status_code for r in results) == [200, 400, 400]
    with m.SessionLocal() as db:
        assert db.get(m.Topic, topic).quota_used == 1
        assert db.get(m.Topic, topic).status == "full"


def test_approval_rolls_back_quota_on_secondary_failure(client, monkeypatch):
    _, headers = student(client)
    topic = make_topic(quota=1)
    app_id = apply(client, headers, topic)
    teacher = auth(client, "teacher@test.com")
    def fail(*args, **kwargs):
        raise RuntimeError("Injected notification failure")
    monkeypatch.setattr(m, "create_notification", fail)
    response = decide(TestClient(m.app, raise_server_exceptions=False), teacher, app_id)
    assert response.status_code == 500
    with m.SessionLocal() as db:
        assert db.get(m.Application, app_id).status == "SUBMITTED"
        assert db.get(m.Topic, topic).quota_used == 0


def test_rejection_releases_quota_and_does_not_duplicate_fallback(client):
    _, headers = student(client)
    teacher = auth(client, "teacher@test.com")
    topics = [make_topic(quota=1), make_topic()]
    first = apply(client, headers, topics[0])
    second = apply(client, headers, topics[1], 2)
    rejected = decide(client, teacher, first, "REJECT", reason="Needs work")
    assert rejected.status_code == 200
    with m.SessionLocal() as db:
        assert db.query(m.Application).count() == 2
        assert db.get(m.Application, second).status == "SUBMITTED"
    assert decide(client, teacher, second).status_code == 200
    assert decide(client, teacher, second, "REJECT", reason="Withdrawn").status_code == 200
    with m.SessionLocal() as db:
        assert db.get(m.Topic, topics[1]).quota_used == 0
    assert decide(client, teacher, second).status_code == 400


def test_database_blocks_second_assignment(client):
    sid, _ = student(client)
    topics = [make_topic(), make_topic()]
    with m.SessionLocal() as db:
        db.add(m.Application(student_id=sid, topic_id=topics[0], priority=1, status="APPROVED"))
        db.commit()
        db.add(m.Application(student_id=sid, topic_id=topics[1], priority=2, status="CONFIRMED_BY_HEAD"))
        with pytest.raises(IntegrityError):
            db.commit()


def test_migration_refuses_conflicting_data_without_deleting_rows(client):
    sid, _ = student(client)
    topics = [make_topic(), make_topic()]
    with m.engine.begin() as connection:
        connection.execute(text("DROP INDEX uq_application_assigned_student"))
    with m.SessionLocal() as db:
        db.add_all([m.Application(student_id=sid, topic_id=t, priority=i+1, status="APPROVED") for i, t in enumerate(topics)])
        db.commit()
    with pytest.raises(IntegrityError):
        m.migrate_database()
    with m.SessionLocal() as db:
        assert db.query(m.Application).filter_by(student_id=sid).count() == 2


def test_gale_shapley_dry_apply_repeat_and_teacher_approval(client):
    sid, headers = student(client)
    topic = make_topic(quota=1)
    app_id = apply(client, headers, topic)
    head = auth(client, "head@test.com")
    with m.SessionLocal() as db:
        before = (db.query(m.Application).count(), db.query(m.AuditLog).count(), db.get(m.Topic, topic).quota_used)
    preview = client.post("/api/allocation/gale-shapley?dry_run=true", headers=head)
    assert preview.status_code == 200
    assert [p["student_id"] for p in preview.json()["assigned_pairs"]] == [sid]
    with m.SessionLocal() as db:
        assert (db.query(m.Application).count(), db.query(m.AuditLog).count(), db.get(m.Topic, topic).quota_used) == before
    applied = client.post("/api/allocation/gale-shapley?apply=true", headers=head)
    assert applied.status_code == 200
    assert applied.json()["assigned_pairs"] == preview.json()["assigned_pairs"]
    assert client.post("/api/head/confirm-all", headers=head).json()["count"] == 0
    assert decide(client, head, app_id).status_code == 400
    assert client.post("/api/allocation/gale-shapley?apply=true", headers=head).status_code == 200
    with m.SessionLocal() as db:
        assert db.get(m.Application, app_id).status == "ALLOCATED"
        assert db.get(m.Topic, topic).quota_used == 1
        assert db.query(m.Application).filter_by(student_id=sid).count() == 1
    teacher = auth(client, "teacher@test.com")
    assert decide(client, teacher, app_id).status_code == 200
    assert client.post("/api/head/confirm-all", headers=head).json()["count"] == 1
    with m.SessionLocal() as db:
        assert db.get(m.Application, app_id).status == "CONFIRMED_BY_HEAD"
        assert db.get(m.Topic, topic).quota_used == 1
    assert decide(client, teacher, app_id, "REJECT", reason="Too late").status_code == 400


def test_matching_preserves_confirmed_topic_and_respects_full_capacity(client):
    sid, headers = student(client, "existing")
    topic = make_topic(quota=1)
    app_id = apply(client, headers, topic)
    teacher = auth(client, "teacher@test.com")
    assert decide(client, teacher, app_id).status_code == 200
    head = auth(client, "head@test.com")
    assert decide(client, head, app_id).status_code == 200
    other_sid, other = student(client, "candidate")
    assert client.post("/api/preferences", headers=other, json={"student_id": other_sid, "topic_ids": [topic]}).status_code == 200
    result = client.post("/api/allocation/gale-shapley?apply=true", headers=head)
    assert result.status_code == 200
    assert result.json()["assigned_count"] == 0
    with m.SessionLocal() as db:
        assert db.get(m.Application, app_id).topic_id == topic
        assert db.get(m.Application, app_id).status == "CONFIRMED_BY_HEAD"
        assert db.get(m.Topic, topic).quota_used == 1


def test_custom_approval_requires_supervisor_and_is_single_transaction(client, monkeypatch):
    sid, headers = student(client)
    head = auth(client, "head@test.com")
    teacher = auth(client, "teacher@test.com")
    created = client.post("/api/custom-topics", headers=headers, json={"title_raw": "Audit Custom", "supervisor_id": TEACHER_ID})
    assert created.status_code == 200
    proposal = created.json()["proposal_id"]
    url = f"/api/custom-topics/{proposal}/head-decision"
    assert client.post(url, headers=head, json={"status": "HEAD_APPROVED"}).status_code == 400
    assert client.post(f"/api/custom-topics/{proposal}/supervisor-decision", headers=teacher, json={"status": "SUPERVISOR_APPROVED"}).status_code == 200
    with m.SessionLocal() as db:
        topics_before = db.query(m.Topic).count()
    original = m.create_default_checkpoints
    def fail(*args, **kwargs):
        raise RuntimeError("Injected checkpoint failure")
    monkeypatch.setattr(m, "create_default_checkpoints", fail)
    assert TestClient(m.app, raise_server_exceptions=False).post(url, headers=head, json={"status": "HEAD_APPROVED"}).status_code == 500
    with m.SessionLocal() as db:
        assert db.query(m.Topic).count() == topics_before
        assert db.get(m.CustomTopicProposal, proposal).status == "SUPERVISOR_APPROVED"
        assert db.query(m.Application).filter_by(student_id=sid).count() == 0
    monkeypatch.setattr(m, "create_default_checkpoints", original)
    accepted = client.post(url, headers=head, json={"status": "HEAD_APPROVED", "quota_total": 1})
    assert accepted.status_code == 200
    assert client.post(url, headers=head, json={"status": "HEAD_APPROVED"}).status_code == 409
    with m.SessionLocal() as db:
        assert db.query(m.Application).filter_by(student_id=sid).count() == 1
        assert db.get(m.Topic, accepted.json()["official_topic_id"]).status == "full"


def test_custom_cannot_give_student_second_topic(client):
    _, headers = student(client)
    teacher = auth(client, "teacher@test.com")
    assert decide(client, teacher, apply(client, headers)).status_code == 200
    proposal = client.post("/api/custom-topics", headers=headers, json={"title_raw": "Second topic", "supervisor_id": TEACHER_ID}).json()["proposal_id"]
    assert client.post(f"/api/custom-topics/{proposal}/supervisor-decision", headers=teacher, json={"status": "SUPERVISOR_APPROVED"}).status_code == 200
    assert client.post(f"/api/custom-topics/{proposal}/head-decision", headers=auth(client, "head@test.com"), json={"status": "HEAD_APPROVED"}).status_code == 409


def test_cors_rejects_untrusted_origin(client):
    response = client.options("/api/auth/me", headers={"Origin": "https://untrusted.test", "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization"})
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
    allowed = client.options("/api/auth/me", headers={"Origin": m.CORS_ORIGINS[0], "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization"})
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == m.CORS_ORIGINS[0]


@pytest.mark.parametrize("change", ["expired", "disabled", "wrong_user", "origin", "missing"])
def test_websocket_auth_rejections(client, change):
    headers = auth(client)
    token = headers["Authorization"].split()[1]
    user_id = 1
    if change in ("expired", "disabled"):
        with m.SessionLocal() as db:
            if change == "expired":
                db.query(m.SessionModel).filter_by(token=token).first().expires_at = datetime.utcnow() - timedelta(seconds=1)
            else:
                db.get(m.User, 1).is_active = False
            db.commit()
    if change == "wrong_user":
        user_id = 2
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(f"/ws/{user_id}" + ("" if change == "missing" else f"?token={token}"), headers={"Origin": "https://untrusted.test"} if change == "origin" else {}):
            pass
    assert exc.value.code == 1008
    assert m.ws_manager.active_connections == {}


def test_websocket_revocation_and_session_lifetime(client):
    headers = auth(client)
    token = headers["Authorization"].split()[1]
    with client.websocket_connect(f"/ws/1?token={token}") as websocket:
        # Auth queries must not hold a pooled DB connection for the socket lifetime.
        assert m.engine.pool.checkedout() == 0
        with m.SessionLocal() as db:
            db.query(m.SessionModel).filter_by(token=token).delete()
            db.commit()
        websocket.send_text("check revoked session")
        with pytest.raises(WebSocketDisconnect) as exc:
            websocket.receive_text()
        assert exc.value.code == 1008
    assert m.ws_manager.active_connections == {}


def test_workflow_events_are_not_broadcast_to_other_students(client, monkeypatch):
    sid, headers = student(client)
    events = []
    async def sent(user_id, message):
        events.append((user_id, message))
    async def forbidden(message):
        raise AssertionError("Private application event was broadcast")
    monkeypatch.setattr(m.ws_manager, "send_to_user", sent)
    monkeypatch.setattr(m.ws_manager, "broadcast", forbidden)
    apply(client, headers)
    assert sid in {event[0] for event in events}
    assert 1 not in {event[0] for event in events}
    assert TEACHER_ID in {event[0] for event in events}


def test_legacy_password_upgraded_on_success_only(client):
    with m.SessionLocal() as db:
        db.get(m.User, 1).password_hash = hashlib.sha256(b"123456").hexdigest()
        db.commit()
    assert client.post("/api/login", json={"email": "student@test.com", "password": "wrong"}).status_code == 401
    with m.SessionLocal() as db:
        assert not db.get(m.User, 1).password_hash.startswith("pbkdf2")
    auth(client)
    with m.SessionLocal() as db:
        assert db.get(m.User, 1).password_hash.startswith("pbkdf2:sha256:")


def test_demo_seeding_never_reactivates_or_resets_credentials(client):
    with m.SessionLocal() as db:
        user = db.get(m.User, 1)
        user.is_active = False
        user.password_hash = hashlib.sha256(b"private-existing-password").hexdigest()
        db.commit()
        expected = user.password_hash
        m.seed_database(db)
        assert not db.get(m.User, 1).is_active
        assert db.get(m.User, 1).password_hash == expected


def test_startup_does_not_seed_default_privileged_accounts(monkeypatch):
    monkeypatch.delenv("META_SEED_DEMO", raising=False)
    with m.SessionLocal() as db:
        before = db.query(m.User).count()
    def forbidden(*args):
        raise AssertionError("Demo accounts seeded without opt-in")
    monkeypatch.setattr(m, "seed_database", forbidden)
    with TestClient(m.app):
        pass
    with m.SessionLocal() as db:
        assert db.query(m.User).count() == before


@pytest.mark.parametrize("email", ["teacher@test.com", "head@test.com", "admin@test.com", "company@test.com"])
def test_default_privileged_password_blocked_outside_demo(client, monkeypatch, email):
    monkeypatch.delenv("META_SEED_DEMO", raising=False)
    response = client.post("/api/login", json={"email": email, "password": "123456"})
    assert response.status_code == 403
    with m.SessionLocal() as db:
        user = db.query(m.User).filter_by(email=email).one()
        assert db.query(m.SessionModel).filter_by(user_id=user.id).count() == 0


def test_existing_demo_privileged_tokens_blocked_in_http_and_websocket(client, monkeypatch):
    headers = auth(client, "teacher@test.com")
    token = headers["Authorization"].split()[1]
    monkeypatch.delenv("META_SEED_DEMO", raising=False)
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(f"/ws/{TEACHER_ID}?token={token}"):
            pass
    assert exc.value.code == 1008


def test_unauthorized_response_preserves_bearer_challenge(client):
    response = client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize("filename,content,status", [("spoofed.pdf", b"not a PDF", 400), ("spoofed.docx", b"not a zip", 400), ("../unsafe.pdf", b"%PDF-1.4", 400), ("large.pdf", b"x"*(20*1024*1024+1), 413)], ids=["pdf-content", "docx-content", "traversal", "size-limit"])
def test_upload_invalid_content(client, filename, content, status):
    headers = auth(client)
    before = set(Path(m.UPLOADS_DIR).rglob("*"))
    response = client.post("/api/checkpoints/upload", headers=headers, data={"checkpoint_id": DEFAULT_CHECKPOINT}, files={"file": (filename, content)})
    assert response.status_code == status
    assert set(Path(m.UPLOADS_DIR).rglob("*")) == before


def test_docx_zip_bomb_rejected_before_parsing(client):
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "x")
        archive.writestr("word/document.xml", b"x"*(65*1024*1024))
    response = client.post("/api/norm-control/check-docx", files={"file": ("bomb.docx", content.getvalue())})
    assert response.status_code == 413


def test_upload_failure_removes_only_new_orphan(client, monkeypatch):
    headers = auth(client)
    before = set(Path(m.UPLOADS_DIR).rglob("*"))
    def fail(*args, **kwargs):
        raise RuntimeError("Injected audit failure")
    monkeypatch.setattr(m, "log_audit", fail)
    response = TestClient(m.app, raise_server_exceptions=False).post("/api/checkpoints/upload", headers=headers,
        data={"checkpoint_id": DEFAULT_CHECKPOINT}, files={"file": ("report.pdf", b"%PDF-1.4 data")})
    assert response.status_code == 500
    assert set(Path(m.UPLOADS_DIR).rglob("*")) == before
    with m.SessionLocal() as db:
        assert db.get(m.Checkpoint, DEFAULT_CHECKPOINT).status == "NOT_SUBMITTED"
        assert db.query(m.CheckpointSubmission).count() == 0


def test_symlink_upload_cannot_escape_root(client, tmp_path):
    external = tmp_path / "private.txt"
    external.write_text("PRIVATE")
    link = Path(m.UPLOADS_DIR) / "escape.txt"
    try:
        link.symlink_to(external)
    except OSError as e:
        pytest.skip(f"Symlink creation requires elevated privileges on Windows: {e}")
    with m.SessionLocal() as db:
        db.get(m.Checkpoint, DEFAULT_CHECKPOINT).file_name = link.name
        db.commit()
    assert client.get(f"/api/checkpoints/download/{DEFAULT_CHECKPOINT}", headers=auth(client)).status_code == 403


def test_unsubmitted_report_cannot_be_accepted(client):
    response = client.post("/api/checkpoints/done", headers=auth(client, "head@test.com"), json={"checkpoint_id": DEFAULT_CHECKPOINT})
    assert response.status_code == 400
    with m.SessionLocal() as db:
        assert db.get(m.Checkpoint, DEFAULT_CHECKPOINT).status == "NOT_SUBMITTED"


def test_signed_order_is_immutable_after_confirm_all(client):
    with m.SessionLocal() as db:
        order = db.query(m.OfficialOrder).first()
        before = [(i.student_id, i.topic_id, i.topic_title_ru) for i in order.items]
        expected_hash = order.document_hash
        order_id = order.id
    _, headers = student(client)
    app_id = apply(client, headers)
    assert decide(client, auth(client, "teacher@test.com"), app_id).status_code == 200
    head = auth(client, "head@test.com")
    assert client.post("/api/head/confirm-all", headers=head).status_code == 200
    with m.SessionLocal() as db:
        order = db.get(m.OfficialOrder, order_id)
        assert [(i.student_id, i.topic_id, i.topic_title_ru) for i in order.items] == before
        assert order.document_hash == expected_hash
        assert [(i.student_id, i.topic_id, i.topic_title_ru) for i in m.sync_order_items(order, db)] == before


def test_draft_order_has_only_confirmed_rows_and_no_fabricated_fallback(client):
    sid, headers = student(client)
    app_id = apply(client, headers)
    teacher = auth(client, "teacher@test.com")
    assert decide(client, teacher, app_id).status_code == 200
    with m.SessionLocal() as db:
        order = m.OfficialOrder(order_number="AUDIT-DRAFT", verification_code="AUDIT-DRAFT", status="DRAFT", document_hash="")
        db.add(order)
        db.commit()
        assert m.sync_order_items(order, db) == []
        oid = order.id
    assert decide(client, auth(client, "head@test.com"), app_id).status_code == 200
    with m.SessionLocal() as db:
        items = m.sync_order_items(db.get(m.OfficialOrder, oid), db)
        assert len(items) == 1 and items[0].student_id == sid


def test_order_lookup_missing_is_404_and_does_not_create_order(client):
    headers = auth(client, "head@test.com")
    with m.SessionLocal() as db:
        count = db.query(m.OfficialOrder).count()
    for url in ("/api/export/pdf-order?order_code=MISSING", "/api/export/official-decree-docx?order_id=999999"):
        assert client.get(url, headers=headers).status_code == 404
    with m.SessionLocal() as db:
        assert db.query(m.OfficialOrder).count() == count


def test_pdf_tampering_is_not_silently_blessed(client):
    head = auth(client, "head@test.com")
    with m.SessionLocal() as db:
        order = db.query(m.OfficialOrder).first()
        code, digest = order.verification_code, order.document_hash
    path = Path(m.ORDERS_DIR) / f"order_{code}.pdf"
    path.write_bytes(b"%PDF-1.4 TAMPERED")
    assert client.get(f"/api/export/pdf-order?order_code={code}", headers=head).status_code == 409
    verification = client.get("/verify/" + code)
    assert "Документ зарегистрирован в реестре META" not in verification.text
    with m.SessionLocal() as db:
        assert db.query(m.OfficialOrder).filter_by(verification_code=code).one().document_hash == digest
    assert path.read_bytes() == b"%PDF-1.4 TAMPERED"


def test_signed_pdf_missing_is_not_regenerated(client):
    with m.SessionLocal() as db:
        order = db.query(m.OfficialOrder).first()
        code = order.verification_code
    path = Path(m.ORDERS_DIR) / f"order_{code}.pdf"
    path.unlink()
    assert client.get(f"/api/export/pdf-order?order_code={code}", headers=auth(client, "head@test.com")).status_code == 409
    assert not path.exists()


def test_html_exports_escape_untrusted_text(client):
    sid, headers = student(client, full_name="<script>alert(1)</script>")
    app_id = apply(client, headers)
    with m.SessionLocal() as db:
        db.get(m.Application, app_id).motivation = "<img src=x onerror=alert(2)>"
        db.commit()
    response = client.get(f"/api/export/statement/{app_id}", headers=headers)
    assert response.status_code == 200
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
    assert "<img src=x onerror=" not in response.text
    verify = client.get("/verify/%3Cimg%20src=x%20onerror=alert(1)%3E")
    assert verify.status_code == 200
    assert "<img src=x onerror=alert(1)>" not in verify.text
    assert "&lt;img src=x onerror=alert(1)&gt;" in verify.text


def test_generated_pdf_and_qr_escape_data_and_use_current_order(client, monkeypatch):
    captured = []
    original = m.qrcode.QRCode.add_data
    def capture(self, data, *args, **kwargs):
        captured.append(data)
        return original(self, data, *args, **kwargs)
    monkeypatch.setattr(m.qrcode.QRCode, "add_data", capture)
    with m.SessionLocal() as db:
        user = db.get(m.User, 1)
        order = m.OfficialOrder(order_number="AUDIT-PDF", verification_code="AUDIT-PDF", document_hash="", status="DRAFT")
        db.add(order)
        db.flush()
        payload = '<img src="file:///must-not-be-read.png"/>'
        db.add(m.OrderItem(order_id=order.id, student_id=user.id, student_name=payload, group_name="A&B",
            topic_title_ru=payload, topic_title_kz="<b>text</b>", supervisor_name=payload))
        db.commit()
        pdf = m.build_order_pdf_bytes(order, db)
        assert pdf.startswith(b"%PDF")
        assert f"{m.BASE_URL}/verify/AUDIT-PDF" in captured
        path, digest = m.ensure_order_pdf(order, db)
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest == order.document_hash


def test_backup_is_consistent_sqlite_snapshot(client, tmp_path):
    response = client.get("/api/admin/backup", headers=auth(client, "head@test.com"))
    assert response.status_code == 200
    snapshot = tmp_path / "backup.db"
    snapshot.write_bytes(response.content)
    with sqlite3.connect(snapshot) as db:
        assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0


@pytest.mark.parametrize("content", [b"x"*200, b"SQLite format 3\x00"+b"x"*500])
def test_restore_invalid_does_not_touch_live_db(client, content):
    headers = auth(client, "head@test.com")
    path = Path(m.get_active_sqlite_db_path())
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    response = client.post("/api/admin/restore", headers=headers, files={"file": ("bad.db", content)})
    assert response.status_code == 400
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_restore_valid_snapshot_revokes_old_sessions(client):
    head = auth(client, "head@test.com")
    snapshot = client.get("/api/admin/backup", headers=head).content
    _, temporary_user = student(client, "after_backup")
    response = client.post("/api/admin/restore", headers=head, files={"file": ("valid.db", snapshot)})
    assert response.status_code == 200
    assert client.get("/api/auth/me", headers=head).status_code == 401
    assert client.get("/api/auth/me", headers=temporary_user).status_code == 401
    with m.SessionLocal() as db:
        assert db.query(m.User).filter_by(email="after_backup@audit.test").count() == 0
    assert auth(client, "head@test.com")


def test_sessions_return_connections_after_errors(client):
    headers = auth(client)
    for _ in range(3):
        assert client.get("/api/checkpoints/999999", headers=headers).status_code == 404
        assert client.post("/api/applications/apply", headers=headers, json={"topic_id": -1}).status_code == 400
    assert m.engine.pool.checkedout() == 0


def test_commit_conflict_is_reported_before_success_response(client):
    sid, headers = student(client)
    def fail_commit(session):
        raise IntegrityError("commit", {}, RuntimeError("Injected conflict"))
    event.listen(m.SessionLocal, "before_commit", fail_commit)
    try:
        response = TestClient(m.app, raise_server_exceptions=False).post("/api/preferences", headers=headers,
            json={"student_id": sid, "topic_ids": [1]})
        assert response.status_code == 409
    finally:
        event.remove(m.SessionLocal, "before_commit", fail_commit)
    with m.SessionLocal() as db:
        assert db.query(m.Preference).filter_by(student_id=sid).count() == 0
    assert m.engine.pool.checkedout() == 0


def test_registration_secondary_failure_rolls_back_user_and_session(client, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("Injected registration audit failure")
    monkeypatch.setattr(m, "log_audit", fail)
    with m.SessionLocal() as db:
        checkpoints_before = db.query(m.Checkpoint).count()
    response = TestClient(m.app, raise_server_exceptions=False).post("/api/register",
        json={"full_name": "Atomic registration", "email": "rollback@audit.test", "password": "123456"})
    assert response.status_code == 500
    with m.SessionLocal() as db:
        assert db.query(m.User).filter_by(email="rollback@audit.test").count() == 0
        assert db.query(m.SessionModel).count() == 0
        assert db.query(m.Checkpoint).count() == checkpoints_before


def test_failed_login_audit_remains_durable(client):
    with m.SessionLocal() as db:
        before = db.query(m.AuditLog).filter_by(action="LOGIN_FAILED", user_email="student@test.com").count()
    response = client.post("/api/login", json={"email": "student@test.com", "password": "WRONG"})
    assert response.status_code == 401
    with m.SessionLocal() as db:
        assert db.query(m.AuditLog).filter_by(action="LOGIN_FAILED", user_email="student@test.com").count() == before + 1
        assert db.query(m.SessionModel).count() == 0


def test_matching_keeps_original_rank_after_rejection(client):
    sid, headers = student(client)
    teacher = auth(client, "teacher@test.com")
    first_topic, second_topic = make_topic(), make_topic()
    first = apply(client, headers, first_topic, 1)
    assert decide(client, teacher, first, "REJECT", reason="Not suitable").status_code == 200
    assert client.post("/api/preferences", headers=headers,
                       json={"student_id": sid, "topic_ids": [first_topic, second_topic]}).status_code == 200
    response = client.post("/api/allocation/gale-shapley?apply=true", headers=auth(client, "head@test.com"))
    assert response.status_code == 200
    matched = [item for item in response.json()["assigned_pairs"] if item["student_id"] == sid]
    assert len(matched) == 1 and matched[0]["topic_id"] == second_topic and matched[0]["priority_assigned"] == 2
    with m.SessionLocal() as db:
        allocated = db.query(m.Application).filter_by(student_id=sid, status="ALLOCATED").one()
        assert allocated.priority == 2


def test_migration_never_rebuilds_legacy_table_with_missing_fk(tmp_path, monkeypatch):
    # This separate tiny database has a legacy checkpoints table without student FK.
    # Migration must report the missing FK without dropping the table or its row.
    from sqlalchemy import create_engine
    legacy = create_engine("sqlite:///" + str(tmp_path / "legacy.db"))
    with legacy.begin() as db:
        db.execute(text("CREATE TABLE checkpoints (id INTEGER PRIMARY KEY, student_id INTEGER, application_id INTEGER REFERENCES applications(id), title VARCHAR, deadline VARCHAR, status VARCHAR)"))
        db.execute(text("INSERT INTO checkpoints(id,student_id,title) VALUES(123,1,'PRESERVE')"))
    monkeypatch.setattr(m, "engine", legacy)
    try:
        with pytest.raises(RuntimeError, match="Missing foreign keys"):
            m.migrate_database()
        with legacy.connect() as db:
            assert db.execute(text("SELECT title FROM checkpoints WHERE id=123")).scalar_one() == "PRESERVE"
    finally:
        legacy.dispose()
