#!/usr/bin/env python3
"""Репетиция на КОПИИ: копирует базу и uploads в рабочую папку, применяет скрипт, поднимает сервер на порту 8100
и прогоняет всю цепочку через API с проверкой кодов. Оригинальные diploma.db и uploads только читаются.

  python rehearse.py --src C:\\...\\проект --python C:\\...\\python.exe [--port 8100]
(--python по умолчанию <src>/.venv/...; --src: папка, где лежат main.py, diploma.db, uploads; рабочая папка - rehearsal_<время> рядом с этим файлом)
"""
import argparse, hashlib, json, os, shutil, subprocess, sys, time, urllib.error, urllib.request
ap = argparse.ArgumentParser()
ap.add_argument("--src", required=True); ap.add_argument("--port", type=int, default=8100)
ap.add_argument("--python", default=None)
a = ap.parse_args()
for v in (".venv/Scripts/python.exe", ".venv/bin/python"):
    if not a.python and os.path.isfile(os.path.join(a.src, v)): a.python = os.path.join(a.src, v)
a.src = os.path.abspath(a.src)
a.python = a.python or sys.executable
here = os.path.dirname(os.path.abspath(__file__))
w = os.path.join(here, "rehearsal_" + time.strftime("%Y%m%d_%H%M%S")); os.makedirs(w)
db, up = os.path.join(w, "diploma.db"), os.path.join(w, "uploads")
import sqlite3
s = sqlite3.connect("file:" + os.path.join(a.src, "diploma.db").replace("\\", "/") + "?mode=ro", uri=True); d = sqlite3.connect(db); s.backup(d); d.close(); s.close()
shutil.copytree(os.path.join(a.src, "uploads"), up)
# сервер запускается из КОПИИ кода: migrate_database() создаёт uploads/ и backups/ относительно cwd, проект не должен меняться
app = os.path.join(w, "app")
shutil.copytree(a.src, app, ignore=shutil.ignore_patterns(".venv", "venv", ".git", "__pycache__", "rehearsal_*", "backups", "uploads", "*.db", "*.db-*", "node_modules"))
r = subprocess.run([a.python, os.path.join(here, "seed_demo_slots.py"), "--db", db, "--uploads", up, "--port", str(a.port)])
if r.returncode: sys.exit("seed: код %d" % r.returncode)
B = f"http://127.0.0.1:{a.port}"
env = dict(os.environ, META_SEED_DEMO="1", DATABASE_URL="sqlite:///" + db.replace("\\", "/"), UPLOADS_DIR=up, BASE_URL=B, CORS_ALLOWED_ORIGINS=B)
srv = subprocess.Popen([a.python, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(a.port)], cwd=app, env=env,
                       stdout=open(os.path.join(w, "server.log"), "w"), stderr=subprocess.STDOUT)
bad = []
def call(m, p, tok=None, body=None):
    q = urllib.request.Request(B + p, method=m, data=None if body is None else json.dumps(body).encode(),
                               headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + tok} if tok else {})})
    try: x = urllib.request.urlopen(q); c, raw = x.status, x.read()
    except urllib.error.HTTPError as e: c, raw = e.code, e.read()
    try: return c, json.loads(raw)
    except Exception: return c, raw
def step(name, got, want=200, extra=""):
    ok = got == want; print(("OK  " if ok else "FAIL"), name, got, extra)
    if not ok: bad.append(name)
try:
    for _ in range(60):
        try: urllib.request.urlopen(B + "/api/topics"); break
        except urllib.error.HTTPError: break
        except Exception:
            if srv.poll() is not None: sys.exit("сервер не запустился (нужен --python из .venv):\n" + open(os.path.join(w, "server.log")).read()[-800:])
            time.sleep(0.5)
    tok = {}
    for k, e in (("st", "student25@test.com"), ("te", "teacher_demo@test.com"), ("he", "head@test.com")):
        c, j = call("POST", "/api/login", body={"email": e, "password": "123456"}); step("login " + e, c); tok[k] = j.get("token")
    t7 = next(t for t in call("GET", "/api/topics", tok["st"])[1] if t["title_ru"].startswith("Система подбора научных"))
    c, j = call("POST", "/api/applications/apply", tok["st"], {"topic_id": t7["id"], "priority": 1, "motivation": "демо"}); step("1 студент подаёт заявку", c); aid = j.get("application_id") or j.get("id")
    c, j = call("GET", "/api/applications", tok["te"]); q = [x["student_name"] for x in j if x["status"] == "SUBMITTED"]; step("  в очереди преподавателя 1 заявка", len(q), 1, q)
    c, j = call("POST", "/api/applications/decide", tok["te"], {"application_id": aid, "decision": "APPROVE"}); step("2 преподаватель одобряет", c, 200, j.get("status", ""))
    c, j = call("POST", "/api/applications/decide", tok["he"], {"application_id": aid, "decision": "APPROVE"}); step("3 завкафедрой утверждает", c, 200, j.get("status", ""))
    c, j = call("POST", "/api/allocation/gale-shapley?dry_run=true", tok["he"]); step("4a Гейл-Шепли предпросмотр", c, 200, j.get("stats"))
    c, j = call("POST", "/api/allocation/gale-shapley?apply=true", tok["he"]); step("4b Гейл-Шепли применить", c, 200, j.get("stats"))
    c, j = call("POST", "/api/head/confirm-all", tok["he"]); step("5 утвердить весь поток", c, 200, j.get("message"))
    c, raw = call("GET", "/api/export/pdf-order", tok["he"]); h = sqlite3.connect(db).execute("select document_hash from official_orders where status in ('SIGNED','ACTIVE')").fetchone()[0]
    step("6 приказ PDF отдан", c); step("  SHA-256 PDF = document_hash", hashlib.sha256(raw).hexdigest(), h.lower())
    c, j = call("GET", "/api/applications", tok["he"]); print("  заявки 13 и новые:", [(x["id"], x["student_name"], x["status"]) for x in j if x["id"] == 13 or x["id"] > 25])
    c, j = call("POST", "/api/login", body={"email": "teacher@test.com", "password": "123456"}); t5 = j.get("token")
    c, j = call("POST", "/api/applications/decide", t5, {"application_id": 13, "decision": "APPROVE"})
    print("  контроль (не делать на демо): teacher@test.com одобряет №13 ->", c, j.get("detail"))
    c, j = call("POST", "/api/applications/decide", tok["he"], {"application_id": 13, "decision": "APPROVE"})
    print("  контроль: head одобряет №13 ->", c, j.get("detail"), "(другая ошибка, TOPIC_FULL только у преподавателя)")
finally:
    srv.terminate(); srv.wait()
print("\nИТОГ:", "ВСЕ ШАГИ OK, TOPIC_FULL нет" if not bad else "ОШИБКИ: " + ", ".join(bad), "| копия:", w)
sys.exit(1 if bad else 0)
