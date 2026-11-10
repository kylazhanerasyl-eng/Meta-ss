#!/usr/bin/env python3
"""META: добавляет демо-данные со свободными местами, чтобы цепочка
«заявка -> одобрение преподавателя -> утверждение завкафедрой -> Гейл-Шепли -> приказ»
шла без TOPIC_FULL.

Только ДОБАВЛЯЕТ строки: 3 темы, 5 пользователей (демо-преподаватель и 4 студента), 9 предпочтений.
Существующие строки, квоты, пароли, подписанный приказ и его PDF не изменяются. GPA/рейтинг у новых
студентов 3.5 - то же значение, которое приложение само ставит при регистрации студента.
Только стандартная библиотека. Запускать при ОСТАНОВЛЕННОМ сервере (скрипт это проверяет).

  python seed_demo_slots.py --db diploma.db --uploads uploads             # применить
  python seed_demo_slots.py --db diploma.db --uploads uploads --check     # только проверить
  python seed_demo_slots.py --db diploma.db --uploads uploads --restore backups/<файл>.db   # откат

Коды выхода: 0 - готово к демо; 1 - ошибка (БД не изменена); 3 - демо на этой базе уже пройдено.
"""
import argparse, hashlib, os, secrets, socket, sqlite3, sys
from datetime import datetime, timezone

YEAR = "2026-2027"
# Новый преподаватель: в его очереди будет ТОЛЬКО демо-заявка, старая заявка №13 (тема 3, student3) ему не видна.
DEMO_TEACHER = ("teacher_demo@test.com", "Демо-преподаватель")
TOPICS = [  # (ключ, русское название, kz, en, категория, теги, навыки, email руководителя, квота)
    ("T7", "Система подбора научных руководителей по навыкам студентов",
     "Студенттердің дағдылары бойынша ғылыми жетекшілерді таңдау жүйесі",
     "Supervisor matching system based on student skills",
     "WEB", "Web, FastAPI, Рекомендации", "Python, SQL, FastAPI", DEMO_TEACHER[0], 3),
    ("T8", "Автоматизированная проверка оригинальности дипломных работ",
     "Дипломдық жұмыстардың түпнұсқалылығын автоматты тексеру",
     "Automated originality checking of diploma theses",
     "AI_ML", "NLP, Python", "Python, NLP, ML", "head@test.com", 2),
    ("T9", "Мобильное приложение записи на консультации к научному руководителю",
     "Ғылыми жетекшіге кеңеске жазылуға арналған мобильді қосымша",
     "Mobile app for booking supervisor consultations",
     "WEB", "Mobile, API", "Flutter, REST, SQL", DEMO_TEACHER[0], 2),
]
GPA = 3.5  # значение по умолчанию приложения (регистрация студента); при равных GPA порядок решает id
ACTOR = ("student25@test.com", "Демо-студент 4 (заявка)")        # ведёт цепочку; без заявок и предпочтений
ACTOR_TOPIC = "T7"
PARTICIPANTS = [("student22@test.com", "Демо-студент 1"),         # участники Гейла-Шепли
                ("student23@test.com", "Демо-студент 2"),
                ("student24@test.com", "Демо-студент 3")]
PREF_ORDER = ["T8", "T9", "T7"]  # приоритеты 1,2,3 у участников
ALL_NEW_USERS = [ACTOR] + PARTICIPANTS
ALL_NEW_EMAILS = [DEMO_TEACHER[0]] + [e for e, _ in ALL_NEW_USERS]
ASSIGNED = ("APPROVED", "SUPERVISOR_APPROVED", "CONFIRMED_BY_HEAD", "ALLOCATED")
EXPECT_ADDED = {"users": len(ALL_NEW_EMAILS), "topics": len(TOPICS), "preferences": len(PARTICIPANTS) * len(PREF_ORDER)}


def server_running(port):
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
        return True
    except OSError:
        return False


def fail(msg):
    raise SystemExit("ОШИБКА: " + msg)


def one(con, sql, *a):
    r = con.execute(sql, a).fetchone()
    return r[0] if r else None


def tables(con):
    return [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def rows_hash(con, t, upto):
    h = hashlib.sha256()
    for row in con.execute(f'SELECT rowid,* FROM "{t}" WHERE rowid<=? ORDER BY rowid', (upto,)):
        h.update(repr(row).encode())
    return h.hexdigest()


def snapshot(con):
    """По каждой таблице: (число строк, max rowid, sha256 строк до max rowid)."""
    out = {}
    for t in tables(con):
        mx = one(con, f'SELECT COALESCE(MAX(rowid),0) FROM "{t}"')
        out[t] = (one(con, f'SELECT COUNT(*) FROM "{t}"'), mx, rows_hash(con, t, mx))
    return out


def snapshot_old(con, before):
    return {t: rows_hash(con, t, mx) for t, (_, mx, _) in before.items()}


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_password(pw):  # тот же формат, что в main.py
    salt = secrets.token_hex(16)
    d = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 100_000)
    return f"pbkdf2:sha256:100000${salt}${d.hex()}"


def check_signed_orders(con, uploads):
    """Подписанный приказ: PDF обязан быть найден, и его SHA-256 обязан совпасть с document_hash."""
    n = 0
    for oid, code, status, doc_hash in con.execute(
            "SELECT id, verification_code, status, document_hash FROM official_orders"):
        if status not in ("SIGNED", "ACTIVE"):
            continue
        pdf = os.path.join(uploads, "orders", f"order_{code}.pdf")
        if not os.path.isfile(pdf):
            fail(f"PDF подписанного приказа {code} не найден: {pdf}. Укажите --uploads <папка uploads>; без этой проверки скрипт не работает")
        if sha256_file(pdf) != (doc_hash or "").lower():
            fail(f"SHA-256 PDF приказа {code} не совпадает с document_hash - остановитесь")
        print(f"  ok приказ {code}: SHA-256 PDF = document_hash ({doc_hash[:12]}...)")
        n += 1
    if not n:
        fail("в базе нет подписанного приказа - это не та база")


def topic_by_title(con, title):
    return con.execute("SELECT id, quota_total, quota_used, status, supervisor_id FROM topics WHERE title_ru=?",
                       (title,)).fetchone()


def verify(con, uploads):
    """Только чтение. Возвращает True, если демо ещё не проходили (свежее состояние)."""
    ids, used = {}, False
    for key, ru, *_rest, sup_email, quota in TOPICS:
        r = topic_by_title(con, ru)
        if not r:
            fail(f"нет темы {key}")
        tid, qt, qu, st, sup = r
        if qt != quota or not 0 <= qu <= qt:
            fail(f"тема {key}: неверная квота {qu}/{qt}")
        if sup != one(con, "SELECT id FROM users WHERE email=?", sup_email):
            fail(f"тема {key}: неверный руководитель")
        used |= qu > 0
        ids[key] = (tid, qt - qu)
    q = ",".join("?" * len(ASSIGNED))
    if not one(con, "SELECT id FROM users WHERE email=? AND role='teacher' AND is_active=1", DEMO_TEACHER[0]):
        fail(f"{DEMO_TEACHER[0]} не найден или не активен")
    aid = one(con, "SELECT id FROM users WHERE email=? AND role='student' AND is_active=1", ACTOR[0])
    if not aid:
        fail(f"{ACTOR[0]} не найден или не активен")
    used |= bool(one(con, "SELECT COUNT(*) FROM applications WHERE student_id=?", aid))
    for email, _ in PARTICIPANTS:
        uid = one(con, "SELECT id FROM users WHERE email=? AND role='student' AND is_active=1", email)
        if not uid:
            fail(f"нет активного студента {email}")
        used |= bool(one(con, "SELECT COUNT(*) FROM applications WHERE student_id=?", uid))
        pr = [p[0] for p in con.execute("SELECT topic_id FROM preferences WHERE student_id=? ORDER BY rank", (uid,))]
        if pr != [ids[k][0] for k in PREF_ORDER]:
            fail(f"у {email} неверные предпочтения")
    if con.execute("SELECT id FROM topics WHERE quota_total<=0 OR quota_used<0 OR quota_used>quota_total").fetchall():
        fail("некорректные квоты в таблице topics")
    check_signed_orders(con, uploads)
    if used:
        return False
    # свежее состояние: свободных мест хватает всем (1 у актёра на T7 + по месту участникам)
    free = sum(v[1] for v in ids.values())
    if ids[ACTOR_TOPIC][1] < 1 or free - 1 < len(PARTICIPANTS):
        fail(f"свободных мест мало: {free} (нужно {1 + len(PARTICIPANTS)})")
    print("  ok свободные места: " + ", ".join(f"{k}={v[1]}" for k, v in ids.items()))
    return True


def restore(db, backup, uploads):
    """Откат: проверяет копию, сохраняет текущую базу рядом, затем переносит копию в базу через Backup API."""
    backup = os.path.abspath(backup)
    if not os.path.isfile(backup):
        fail(f"нет файла {backup}")
    b = sqlite3.connect(backup)
    if one(b, "PRAGMA integrity_check") != "ok":
        fail("integrity_check копии не ok - откат не выполнен")
    cur = sqlite3.connect(db)
    safe = os.path.join(os.path.dirname(backup), "diploma_before_restore_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".db")
    d = sqlite3.connect(safe)
    cur.backup(d)
    d.close()
    cur.close()
    print("Текущая база сохранена:", safe)
    tgt = sqlite3.connect(db, timeout=5)
    b.backup(tgt)  # Backup API - файл базы не подменяется копированием
    if one(tgt, "PRAGMA integrity_check") != "ok":
        fail("integrity_check после отката не ok; текущая база до отката: " + safe)
    tgt.close()
    b.close()
    chk = sqlite3.connect(db)
    check_signed_orders(chk, uploads)
    print("ГОТОВО: база возвращена из", backup)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(here, "diploma.db"))
    ap.add_argument("--uploads", default=os.environ.get("UPLOADS_DIR"),
                    help="папка uploads (по умолчанию $UPLOADS_DIR, иначе рядом с БД)")
    ap.add_argument("--backup-dir", default=None, help="по умолчанию <папка БД>/backups")
    ap.add_argument("--check", action="store_true", help="только проверить, ничего не менять")
    ap.add_argument("--restore", metavar="BACKUP.db", help="откат: вернуть базу из резервной копии")
    ap.add_argument("--port", type=int, default=8002, help="порт сервера, который должен быть остановлен")
    a = ap.parse_args()
    db = os.path.abspath(a.db)
    if not os.path.isfile(db):
        fail(f"нет файла {db}")
    uploads = a.uploads or os.path.join(os.path.dirname(db), "uploads")
    if not a.check and server_running(a.port):
        fail(f"на порту {a.port} работает сервер - остановите его (закройте окно start.bat) и запустите снова")
    if a.restore:
        return restore(db, a.restore, uploads)

    con = sqlite3.connect(db, timeout=5, isolation_level=None)
    con.execute("PRAGMA foreign_keys=ON")
    if one(con, "PRAGMA integrity_check") != "ok":
        fail("integrity_check исходной БД не ok")

    n_t = sum(1 for k, ru, *_ in TOPICS if topic_by_title(con, ru))
    n_u = sum(1 for e in ALL_NEW_EMAILS if one(con, "SELECT 1 FROM users WHERE email=?", e))
    if (n_t, n_u) == (len(TOPICS), len(ALL_NEW_EMAILS)):
        print("Демо-данные уже добавлены. Проверяю:")
        if verify(con, uploads):
            print("ГОТОВО: данные на месте, демо ещё не проходили, изменений не вносилось.")
            return
        print("ДЕМО НА ЭТОЙ БАЗЕ УЖЕ ПРОЙДЕНО (места заняты / у демо-студентов есть заявки).\n"
              "Структура цела, но повторить цепочку нельзя. Верните базу из резервной копии (см. откат) и запустите скрипт снова.")
        sys.exit(3)
    if (n_t, n_u) != (0, 0):
        fail(f"частичное состояние (тем {n_t}, студентов {n_u}); ничего не менял, нужен ручной разбор")
    if a.check:
        fail("демо-данные ещё не добавлены (режим --check ничего не пишет)")

    check_signed_orders(con, uploads)  # до любой записи: нет PDF приказа - не начинаем
    # 1. Сначала блокировка записи (сервер не успеет записать между копией и изменением), потом копия
    con.execute("BEGIN IMMEDIATE")  # упадёт, если БД занята работающим сервером
    # 2. Резервная копия через SQLite Backup API + integrity_check копии
    bdir = a.backup_dir or os.path.join(os.path.dirname(db), "backups")
    os.makedirs(bdir, exist_ok=True)
    bpath = os.path.join(bdir, "diploma_before_demo_slots_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".db")
    try:
        src, dst = sqlite3.connect(db), sqlite3.connect(bpath)  # чтение допускается, пока мы держим блокировку записи
        src.backup(dst)
        src.close()
        if one(dst, "PRAGMA integrity_check") != "ok":
            fail("integrity_check резервной копии не ok")
        dst.close()
        print("1. Резервная копия:", bpath, "(integrity_check: ok)")

        # 3. Одна транзакция: добавление + проверка; при любой ошибке - откат
        before = snapshot(con)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
        ids = {}
        pw = hash_password("123456")  # пароль только у НОВЫХ демо-пользователей
        con.execute(
            "INSERT INTO users (full_name,email,password_hash,role,department,is_active,created_at)"
            " VALUES (?,?,?,'teacher','ВТиПО',1,?)", (DEMO_TEACHER[1], DEMO_TEACHER[0], pw, now))
        for key, ru, kz, en, cat, tags, skills, sup_email, quota in TOPICS:
            sup = con.execute("SELECT id, full_name FROM users WHERE email=?", (sup_email,)).fetchone()
            if not sup:
                fail(f"нет руководителя {sup_email}")
            cur = con.execute(
                "INSERT INTO topics (title,title_ru,title_kz,title_en,supervisor_id,supervisor_name,department,"
                "category,tags,required_skills,difficulty,has_data,quota_total,quota_used,status,source,"
                "academic_year,work_type,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,3,1,?,0,'available','teacher',?,"
                "'DIPLOMA',?)",
                (ru, ru, kz, en, sup[0], sup[1], "ВТиПО", cat, tags, skills, quota, YEAR, now))
            ids[key] = cur.lastrowid
        for email, name in ALL_NEW_USERS:
            cur = con.execute(
                "INSERT INTO users (full_name,email,password_hash,role,rating,gpa,department,is_active,created_at)"
                " VALUES (?,?,?,'student',?,?,'ВТиПО',1,?)", (name, email, pw, GPA, GPA, now))
            if (email, name) in PARTICIPANTS:
                for rank, key in enumerate(PREF_ORDER, 1):
                    con.execute("INSERT INTO preferences (student_id,topic_id,priority,rank) VALUES (?,?,?,?)",
                                (cur.lastrowid, ids[key], rank, rank))
        print("2. Добавлено: темы", ids, "| пользователей", len(ALL_NEW_EMAILS))

        # 4. Проверка внутри транзакции: старое не тронуто, добавлено ровно ожидаемое
        after = snapshot_old(con, before)
        for t, (cnt, mx, h) in before.items():
            c2 = one(con, f'SELECT COUNT(*) FROM "{t}"')
            if c2 - cnt != EXPECT_ADDED.get(t, 0):
                fail(f"таблица {t}: добавлено {c2 - cnt}, ожидалось {EXPECT_ADDED.get(t, 0)}")
            if after[t] != h:
                fail(f"таблица {t}: изменились существующие строки")
        if con.execute("PRAGMA foreign_key_check").fetchall():
            fail("foreign_key_check нашёл нарушения")
        if not verify(con, uploads):
            fail("после вставки демо не в свежем состоянии")
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        print("Транзакция ОТКАТАНА, БД не изменена. Резервная копия:", bpath)
        raise
    con.close()

    # 5. Повторная проверка свежим соединением
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")
    if one(con, "PRAGMA integrity_check") != "ok":
        fail("integrity_check после изменения не ok")
    after = snapshot_old(con, before)
    for t, (cnt, mx, h) in before.items():
        if after[t] != h:
            fail(f"после коммита изменилась таблица {t}")
    print("3. После коммита: integrity_check ok, существующие строки всех", len(before), "таблиц не изменены")
    print("ГОТОВО. Откат: остановить сервер и вернуть копию:", bpath)
    print("ВНИМАНИЕ: на демо входить как teacher_demo@test.com. У teacher@test.com в очереди заявка №13: её одобрение даст TOPIC_FULL.")


if __name__ == "__main__":
    main()
