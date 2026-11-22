"""Offline regression tests for course isolation, storage consent and records."""

import json
import importlib.util
from contextlib import closing
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "scripts"


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sources = self.root / "source folder"
        self.sources.mkdir()
        self.source = self.sources / "notes.md"
        self.source.write_text("# Topic\n\nExample for review.\n", encoding="utf-8")
        self.profile = self.write_json("profile.json", {"course": "Synthetic mechanics"})

    def write_json(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def call(self, script, course, *args, ok=True):
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", str(SCRIPTS / script), "--course-dir", str(course), *map(str, args)],
            text=True, encoding="utf-8", capture_output=True, timeout=30,
        )
        if ok:
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        else:
            self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return proc

    def init(self, course):
        return self.call("records.py", course, "init", "--file", self.profile, "--confirm-storage")

    def index_fixture(self, course):
        self.call("convert.py", course, self.source)
        model = self.root / "fake-model"
        model.mkdir(exist_ok=True)
        (model / "model.bin").write_bytes(b"synthetic")
        spec = importlib.util.spec_from_file_location("test_library", SCRIPTS / "library.py")
        library = importlib.util.module_from_spec(spec)
        with patch.object(sys, "path", [str(SCRIPTS), *sys.path]):
            spec.loader.exec_module(library)
        with patch.object(library, "load_model", return_value=object()), patch.object(
            library, "embed", side_effect=lambda model, texts: [[1.0, 0.0] for _ in texts]
        ):
            library.build(course, model)
        with closing(sqlite3.connect(course / "course-index.sqlite3")) as db:
            chunk, source, locator = db.execute("SELECT id, source, locator FROM chunks LIMIT 1").fetchone()
        return library, {"source_id": source, "locator": locator, "chunk_id": chunk}

    def test_unconfirmed_writes_refuse_without_creating_target(self):
        for name, script, args in (
            ("convert", "convert.py", (self.source,)),
            ("index", "library.py", ("index", "--model", self.root / "model")),
            ("records", "records.py", ("init", "--file", self.profile)),
        ):
            with self.subTest(script=script):
                course = self.root / name
                self.call(script, course, *args, ok=False)
                self.assertFalse(course.exists())
        self.assertEqual([p.name for p in self.sources.iterdir()], ["notes.md"])

    def test_confirmed_courses_are_separate_from_source_and_each_other(self):
        courses = [self.root / "course one", self.root / "course two"]
        for course in courses:
            self.init(course)
            self.call("convert.py", course, self.source)
            materials = list((course / "materials").glob("*.md"))
            self.assertEqual(len(materials), 1)
            self.assertIn("Example for review", materials[0].read_text(encoding="utf-8"))
        self.assertEqual([p.name for p in self.sources.iterdir()], ["notes.md"])
        _, source = self.index_fixture(courses[0])
        exam = self.make_exam(source=source)
        self.call("records.py", courses[0], "save-exam", "--file", self.write_json("exam.json", exam))
        first = json.loads(self.call("records.py", courses[0], "status").stdout)
        second = json.loads(self.call("records.py", courses[1], "status").stdout)
        self.assertEqual(first["exam_count"], 1)
        self.assertEqual(second["exam_count"], 0)

    def test_legacy_database_requires_confirmation_and_keeps_existing_records(self):
        course = self.root / "legacy"
        course.mkdir()
        legacy_exam = self.make_exam("old")
        for question in legacy_exam["questions"]:
            question.pop("basis")
            question.pop("basis_note")
        with closing(sqlite3.connect(course / "learning-records.sqlite3")) as db, db:
            db.executescript("""
                CREATE TABLE profile (singleton INTEGER PRIMARY KEY CHECK(singleton=1), data TEXT NOT NULL);
                CREATE TABLE exams (id TEXT PRIMARY KEY, data TEXT NOT NULL, created TEXT NOT NULL);
                CREATE TABLE attempts (id TEXT PRIMARY KEY, exam_id TEXT NOT NULL REFERENCES exams(id), data TEXT NOT NULL, created TEXT NOT NULL);
                CREATE TABLE grades (id INTEGER PRIMARY KEY AUTOINCREMENT, attempt_id TEXT NOT NULL REFERENCES attempts(id), data TEXT NOT NULL, created TEXT NOT NULL);
            """)
            db.execute("INSERT INTO profile VALUES (1, ?)", (json.dumps({"course": "Legacy"}),))
            db.execute("INSERT INTO exams VALUES (?, ?, ?)", (
                "old", json.dumps(legacy_exam, ensure_ascii=False, sort_keys=True, separators=(",", ":")), "2026-01-01"
            ))
        self.call("convert.py", course, self.source, ok=False)
        self.call("records.py", course, "confirm-storage")
        self.call("convert.py", course, self.source)
        profile = json.loads(self.call("records.py", course, "profile").stdout)
        history = json.loads(self.call("records.py", course, "exams").stdout)
        self.assertEqual(profile["course"], "Legacy")
        self.assertEqual(history["exams"][0]["exam_id"], "old")
        self.call("records.py", course, "save-exam", "--file", self.write_json("old-exam.json", legacy_exam))
        old = json.loads(self.call("records.py", course, "exam", "--exam-id", "old", "--key").stdout)
        self.assertEqual(old["questions"][0]["answer"], "key 1")
        attempt = {"id": "legacy-attempt", "exam_id": "old", "answers": {"q1": "old answer"}}
        self.call("records.py", course, "submit", "--file", self.write_json("old-attempt.json", attempt))
        grade = {"attempt_id": "legacy-attempt", "results": [
            {"question_id": f"q{i}", "score": 5, "feedback": "Reviewed"} for i in range(1, 4)
        ]}
        self.call("records.py", course, "grade", "--file", self.write_json("old-grade.json", grade))

    @staticmethod
    def make_exam(exam_id="exam-1", source=None):
        source = source or {"source_id": "fixture.md", "locator": "Section 1", "chunk_id": "example"}
        return {
            "id": exam_id, "title": "Synthetic", "mode": "diagnostic", "private": "top secret",
            "questions": [
                {"id": f"q{i}", "prompt": f"Prompt {i}", "points": 10, "topic": f"topic {i}",
                 "answer": f"key {i}", "rubric": "ten points", "basis": "variant",
                 "basis_note": "Based on the provided worked example.", "sources": [source.copy()]}
                for i in range(1, 4)
            ],
        }

    def test_student_projection_and_partial_pending_grades(self):
        course = self.root / "course"
        self.init(course)
        _, source = self.index_fixture(course)
        self.call("records.py", course, "save-exam", "--file", self.write_json("exam.json", self.make_exam(source=source)))
        student = json.loads(self.call("records.py", course, "exam", "--exam-id", "exam-1").stdout)
        self.assertNotIn("private", student)
        for question in student["questions"]:
            for field in ("answer", "rubric", "sources", "topic"):
                self.assertNotIn(field, question)
        attempt = {"id": "attempt-1", "exam_id": "exam-1", "answers": {"q1": "equivalent expression", "q2": "partly correct"}}
        self.call("records.py", course, "submit", "--file", self.write_json("attempt.json", attempt))
        grade = {"attempt_id": "attempt-1", "results": [
            {"question_id": "q1", "score": 10, "feedback": "Equivalent solution accepted"},
            {"question_id": "q2", "score": 4, "feedback": "Partial credit"},
            {"question_id": "q3", "score": None, "feedback": "Pending"},
        ]}
        self.call("records.py", course, "grade", "--file", self.write_json("grade.json", grade))
        status = json.loads(self.call("records.py", course, "status").stdout)
        self.assertEqual(status["attempts"][0]["graded_questions"], 2)
        self.assertEqual(status["attempts"][0]["pending_questions"], 1)
        self.assertEqual(status["topics"]["topic 1"]["score"], 10)
        self.assertEqual(status["topics"]["topic 2"]["score"], 4)
        self.assertNotIn("topic 3", status["topics"])

    def test_exam_requires_current_index_and_verified_sources(self):
        course = self.root / "course"
        self.init(course)
        missing = self.make_exam()
        self.call("records.py", course, "save-exam", "--file", self.write_json("missing.json", missing), ok=False)
        _, source = self.index_fixture(course)
        for name, field in (("wrong-source", "source_id"), ("wrong-locator", "locator"), ("wrong-chunk", "chunk_id")):
            with self.subTest(name=name):
                exam = self.make_exam(name, source)
                exam["questions"][0]["sources"][0][field] = "fabricated"
                self.call("records.py", course, "save-exam", "--file", self.write_json(name + ".json", exam), ok=False)
        for name, field, value in (("scope-only", "basis", "scope-only"), ("empty-basis-note", "basis_note", ""), ("no-sources", "sources", [])):
            with self.subTest(name=name):
                exam = self.make_exam(name, source)
                exam["questions"][0][field] = value
                self.call("records.py", course, "save-exam", "--file", self.write_json(name + ".json", exam), ok=False)
        for field in ("basis", "basis_note", "sources"):
            with self.subTest(missing=field):
                exam = self.make_exam("missing-" + field, source)
                del exam["questions"][0][field]
                self.call("records.py", course, "save-exam", "--file", self.write_json("missing-" + field + ".json", exam), ok=False)
        direct = self.make_exam("direct", source)
        direct["questions"][0]["basis"] = "direct"
        self.call("records.py", course, "save-exam", "--file", self.write_json("direct.json", direct))
        material = next((course / "materials").glob("*.md"))
        material.write_text(material.read_text(encoding="utf-8") + "\nChanged.\n", encoding="utf-8")
        self.call("records.py", course, "save-exam", "--file", self.write_json("stale.json", self.make_exam("stale", source)), ok=False)
        self.call("records.py", course, "save-exam", "--file", self.root / "direct.json")

    def test_changed_material_is_rejected_before_loading_embedding_model(self):
        course = self.root / "course"
        self.init(course)
        library, _ = self.index_fixture(course)
        self.assertEqual(library.status(course)["status"], "ready")
        material = next((course / "materials").glob("*.md"))
        material.write_text(material.read_text(encoding="utf-8") + "\nChanged.\n", encoding="utf-8")
        self.assertEqual(library.status(course)["status"], "stale")
        with patch.object(library, "load_model") as load:
            with self.assertRaisesRegex(ValueError, "changed since indexing"):
                library.search(course, "topic", 3, [])
            load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
