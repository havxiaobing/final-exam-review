#!/usr/bin/env python3
"""Local, append-only exam and learning records. JSON files are UTF-8."""
import argparse
import datetime as dt
import json
import math
import sqlite3
import sys
from pathlib import Path

from course_workspace import canonical_course, connect_existing, record_confirmation, require_confirmed
from library import validate_exam_sources


def fail(message):
    raise ValueError(message)


def required_string(obj, key):
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        fail(f"{key} must be a non-empty string")
    return value


def read_json(path):
    with open(path, encoding="utf-8-sig") as stream:
        obj = json.load(stream)
    if not isinstance(obj, dict):
        fail("JSON root must be an object")
    return obj


def dump(obj):
    return json.dumps(obj, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def timestamp():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def setup(course_dir, create=False):
    path = canonical_course(course_dir)
    if not create:
        return connect_existing(path, readonly=False)
    path.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path / "learning-records.sqlite3", timeout=30)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS profile (singleton INTEGER PRIMARY KEY CHECK(singleton=1), data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS exams (id TEXT PRIMARY KEY, data TEXT NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, exam_id TEXT NOT NULL REFERENCES exams(id), data TEXT NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS grades (id INTEGER PRIMARY KEY AUTOINCREMENT, attempt_id TEXT NOT NULL REFERENCES attempts(id), data TEXT NOT NULL, created TEXT NOT NULL);
    """)
    return conn


def one(conn, table, identity):
    row = conn.execute(f"SELECT data FROM {table} WHERE id = ?", (identity,)).fetchone()
    if row is None:
        fail(f"{table} record not found: {identity}")
    return json.loads(row[0])


def number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        fail(f"{label} must be a finite number")
    return value


def exam_validate(exam):
    required_string(exam, "id")
    required_string(exam, "title")
    required_string(exam, "mode")
    questions = exam.get("questions")
    if not isinstance(questions, list) or not questions:
        fail("questions must be a non-empty list")
    seen = set()
    for q in questions:
        if not isinstance(q, dict):
            fail("each question must be an object")
        qid = required_string(q, "id")
        if qid in seen:
            fail(f"duplicate question ID: {qid}")
        seen.add(qid)
        for key in ("prompt", "topic", "answer"):
            required_string(q, key)
        if number(q.get("points"), "points") <= 0:
            fail("points must be positive")
        if not q.get("rubric") or not isinstance(q["rubric"], (str, list, dict)):
            fail("rubric must be a non-empty string, list, or object")
        sources = q.get("sources")
        if not isinstance(sources, list) or not sources:
            fail("each question needs at least one source")
        for source in sources:
            if not isinstance(source, dict):
                fail("source must be an object")
            for key in ("source_id", "locator", "chunk_id"):
                required_string(source, key)


def save_unique(conn, table, identity, data, exam_id=None):
    encoded = dump(data)
    existing = conn.execute(f"SELECT data FROM {table} WHERE id=?", (identity,)).fetchone()
    if existing:
        if existing[0] == encoded:
            return "already_exists"
        fail(f"{table} ID already exists with different content: {identity}")
    if table == "attempts":
        conn.execute("INSERT INTO attempts VALUES (?, ?, ?, ?)", (identity, exam_id, encoded, timestamp()))
    else:
        conn.execute("INSERT INTO exams VALUES (?, ?, ?)", (identity, encoded, timestamp()))
    return "created"


def student_exam(exam):
    # Unknown top-level metadata can contain answer keys, so expose an allowlist.
    visible = {k: exam[k] for k in ("id", "title", "mode", "parent_exam_id", "instructions", "duration_minutes") if k in exam}
    visible["questions"] = [{k: q[k] for k in ("id", "prompt", "points", "options") if k in q} for q in exam["questions"]]
    return visible


def run(args, conn):
    if args.command == "confirm-storage":
        row = conn.execute("SELECT data FROM profile WHERE singleton=1").fetchone()
        if not row:
            fail("Existing course profile missing; initialize the course instead")
        profile = json.loads(row[0])
        if not isinstance(profile, dict):
            fail("Existing course profile is invalid")
        required_string(profile, "course")
        record_confirmation(conn, args.course_dir)
        return {"status": "confirmed", "course_dir": str(canonical_course(args.course_dir))}
    if args.command == "init":
        profile = read_json(args.file)
        required_string(profile, "course")
        old = conn.execute("SELECT data FROM profile WHERE singleton=1").fetchone()
        if old and old[0] != dump(profile):
            fail("profile already exists; use update-profile")
        if not old:
            conn.execute("INSERT INTO profile VALUES (1, ?)", (dump(profile),))
        record_confirmation(conn, args.course_dir)
        return {"status": "already_exists" if old else "created", "profile": profile, "course_dir": str(canonical_course(args.course_dir))}
    if args.command in ("profile", "update-profile"):
        row = conn.execute("SELECT data FROM profile WHERE singleton=1").fetchone()
        if not row:
            fail("profile missing; run init first")
        profile = json.loads(row[0])
        if args.command == "update-profile":
            profile.update(read_json(args.file))
            required_string(profile, "course")
            conn.execute("UPDATE profile SET data=? WHERE singleton=1", (dump(profile),))
        return profile
    if args.command == "save-exam":
        exam = read_json(args.file)
        exam_id = required_string(exam, "id")
        existing = conn.execute("SELECT data FROM exams WHERE id=?", (exam_id,)).fetchone()
        if existing:
            if existing[0] == dump(exam):
                return {"status": "already_exists", "exam_id": exam_id}
            fail(f"exams ID already exists with different content: {exam_id}")
        exam_validate(exam)
        for question in exam["questions"]:
            if question.get("basis") not in ("direct", "variant"):
                fail(f"question {question['id']} basis must be direct or variant")
            required_string(question, "basis_note")
        validate_exam_sources(canonical_course(args.course_dir), exam["questions"])
        if exam.get("parent_exam_id"):
            one(conn, "exams", exam["parent_exam_id"])
        return {"status": save_unique(conn, "exams", exam_id, exam), "exam_id": exam_id}
    if args.command == "exam":
        exam = one(conn, "exams", args.exam_id)
        return exam if args.key else student_exam(exam)
    if args.command == "exams":
        if not 1 <= args.limit <= 200:
            fail("--limit must be between 1 and 200")
        history = []
        for exam_id, created, encoded in conn.execute("SELECT id,created,data FROM exams ORDER BY rowid DESC"):
            exam = json.loads(encoded)
            questions = exam["questions"]
            if args.topic:
                questions = [q for q in questions if args.topic.casefold() in q["topic"].casefold()]
                if not questions:
                    continue
            history.append({"exam_id": exam_id, "title": exam["title"], "mode": exam["mode"],
                            "created": created, "questions": [{"id": q["id"], "topic": q["topic"],
                            "prompt": q["prompt"][:240]} for q in questions]})
            if len(history) == args.limit:
                break
        return {"exams": history}
    if args.command == "submit":
        attempt = read_json(args.file)
        identity = required_string(attempt, "id")
        exam_id = required_string(attempt, "exam_id")
        exam = one(conn, "exams", exam_id)
        answers = attempt.get("answers")
        if not isinstance(answers, dict) or any(not isinstance(v, str) for v in answers.values()):
            fail("answers must be an object mapping question IDs to strings")
        unknown = set(answers) - {q["id"] for q in exam["questions"]}
        if unknown:
            fail(f"unknown question IDs: {sorted(unknown)}")
        return {"status": save_unique(conn, "attempts", identity, attempt, exam_id), "attempt_id": identity}
    if args.command == "grade":
        grade = read_json(args.file)
        attempt_id = required_string(grade, "attempt_id")
        attempt = one(conn, "attempts", attempt_id)
        exam = one(conn, "exams", attempt["exam_id"])
        questions = {q["id"]: q for q in exam["questions"]}
        results = grade.get("results")
        if not isinstance(results, list) or len(results) != len(questions):
            fail("results must contain every exam question exactly once; use null score for pending")
        seen = set()
        for result in results:
            if not isinstance(result, dict):
                fail("each result must be an object")
            qid = required_string(result, "question_id")
            if qid not in questions or qid in seen:
                fail(f"unknown or duplicate question ID: {qid}")
            seen.add(qid)
            if "score" not in result:
                fail("score is required; use null for pending")
            if result["score"] is not None:
                score = number(result["score"], "score")
                if score < 0 or score > questions[qid]["points"]:
                    fail(f"score outside allowed range for {qid}")
            required_string(result, "feedback")
        encoded = dump(grade)
        last = conn.execute("SELECT id, data FROM grades WHERE attempt_id=? ORDER BY id DESC LIMIT 1", (attempt_id,)).fetchone()
        if last and last[1] == encoded:
            return {"status": "already_exists", "grade_id": last[0]}
        cursor = conn.execute("INSERT INTO grades (attempt_id,data,created) VALUES (?,?,?)", (attempt_id, encoded, timestamp()))
        return {"status": "created", "grade_id": cursor.lastrowid}
    if args.command == "attempt":
        attempt = one(conn, "attempts", args.attempt_id)
        grades = [{"grade_id": row[0], "created": row[1], "grade": json.loads(row[2])} for row in conn.execute("SELECT id,created,data FROM grades WHERE attempt_id=? ORDER BY id", (args.attempt_id,))]
        return {"attempt": attempt, "grades": grades}
    if args.command == "status":
        topics = {}
        history = []
        for attempt_id, exam_id, created, encoded in conn.execute("SELECT id,exam_id,created,data FROM attempts ORDER BY rowid"):
            exam = one(conn, "exams", exam_id)
            row = conn.execute("SELECT data FROM grades WHERE attempt_id=? ORDER BY id DESC LIMIT 1", (attempt_id,)).fetchone()
            results = {r["question_id"]: r for r in json.loads(row[0])["results"]} if row else {}
            graded = pending = 0
            for q in exam["questions"]:
                result = results.get(q["id"])
                if result is None or result["score"] is None:
                    pending += 1
                    continue
                graded += 1
                topic = topics.setdefault(q["topic"], {"observations": 0, "score": 0, "possible": 0, "recent": [], "incorrect": []})
                topic["observations"] += 1
                topic["score"] += result["score"]
                topic["possible"] += q["points"]
                topic["recent"].append({"attempt_id": attempt_id, "question_id": q["id"], "score": result["score"], "points": q["points"]})
                if result["score"] < q["points"]:
                    topic["incorrect"].append({"attempt_id": attempt_id, "exam_id": exam_id, "question_id": q["id"], "score": result["score"], "points": q["points"]})
            history.append({"attempt_id": attempt_id, "exam_id": exam_id, "created": created, "graded_questions": graded, "pending_questions": pending})
        for topic in topics.values():
            topic["recent"] = topic["recent"][-5:]
            topic["fraction"] = round(topic["score"] / topic["possible"], 4)
        return {"exam_count": conn.execute("SELECT COUNT(*) FROM exams").fetchone()[0], "attempts": history, "topics": topics}
    fail("unknown command")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--course-dir", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "update-profile", "save-exam", "submit", "grade"):
        command = sub.add_parser(name)
        command.add_argument("--file", required=True)
        if name == "init":
            command.add_argument("--confirm-storage", action="store_true", required=True, help="Confirm the user selected this exact storage directory")
    sub.add_parser("confirm-storage", help="Confirm an existing course directory after the user selects it")
    sub.add_parser("profile")
    exams = sub.add_parser("exams", help="Agent-only exam history including unattempted exams")
    exams.add_argument("--limit", type=int, default=20)
    exams.add_argument("--topic", help="Case-insensitive topic substring")
    exam = sub.add_parser("exam")
    exam.add_argument("--exam-id", required=True)
    exam.add_argument("--key", action="store_true")
    attempt = sub.add_parser("attempt")
    attempt.add_argument("--attempt-id", required=True)
    sub.add_parser("status")
    args = parser.parse_args()
    conn = None
    try:
        write_commands = {"init", "confirm-storage", "update-profile", "save-exam", "submit", "grade"}
        if args.command == "init":
            profile = read_json(args.file)
            required_string(profile, "course")
            conn = setup(args.course_dir, create=True)
        elif args.command == "confirm-storage":
            conn = connect_existing(args.course_dir)
        elif args.command in write_commands:
            require_confirmed(args.course_dir)
            conn = connect_existing(args.course_dir)
        else:
            conn = connect_existing(args.course_dir, readonly=True)
        with conn:
            conn.execute("BEGIN IMMEDIATE" if args.command in write_commands else "BEGIN")
            result = run(args, conn)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        if conn:
            conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
