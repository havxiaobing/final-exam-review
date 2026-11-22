import argparse
from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import struct
import sys
import tempfile

from course_workspace import require_confirmed

DB_NAME = 'course-index.sqlite3'
HEADING = re.compile(r'^## (Page|Slide|Section) (\d+)\s*$', re.MULTILINE)
CHUNK_SIZE = 350
OVERLAP = 50


def digest(data):
    return hashlib.sha256(data).hexdigest()


def material_files(course):
    folder = course / 'materials'
    return sorted(folder.glob('*.md'), key=lambda p: p.name.casefold()) if folder.is_dir() else []


def snapshot(course):
    return {p.name: digest(p.read_bytes()) for p in material_files(course)}


def provenance_headings(raw):
    # Mask fenced blocks while preserving offsets into the original text.
    masked = []
    fence_char = None
    fence_length = 0
    for line in raw.splitlines(keepends=True):
        in_fence = fence_char is not None
        if in_fence:
            closing_pattern = r'^ {0,3}' + re.escape(fence_char) + '{' + str(fence_length) + r',}[ \t]*(?:\r?\n)?$'
            if re.match(closing_pattern, line):
                fence_char = None
        else:
            opening = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
            if opening:
                fence_char = opening.group(1)[0]
                fence_length = len(opening.group(1))
                in_fence = True
        if in_fence:
            masked.append(''.join(c if c in '\r\n' else ' ' for c in line))
        else:
            masked.append(line)
    return list(HEADING.finditer(''.join(masked)))


def chunks(course):
    result = []
    warnings = []
    for path in material_files(course):
        raw = path.read_text(encoding='utf-8-sig')
        title_match = re.search(r'^# (.+)$', raw, re.MULTILINE)
        title = title_match.group(1).strip() if title_match else path.stem
        matches = provenance_headings(raw)
        if not matches:
            warnings.append(f'{path.name}: no recognized page/slide/section headings')
        for i, match in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
            body = raw[match.end():end].strip()
            locator = f'{match.group(1)} {match.group(2)}'
            if not body:
                warnings.append(f'{path.name}: {locator} is empty')
                continue
            start = 0
            part = 0
            while start < len(body):
                stop = min(start + CHUNK_SIZE, len(body))
                if stop < len(body):
                    boundary = max(body.rfind('\n', start + CHUNK_SIZE // 2, stop), body.rfind('。', start + CHUNK_SIZE // 2, stop))
                    if boundary > start:
                        stop = boundary + 1
                text = body[start:stop].strip()
                if text:
                    part += 1
                    identity = f'{path.name}\0{locator}\0{i}\0{part}\0{text}'
                    result.append((digest(identity.encode('utf-8'))[:20], path.name, title, locator, part, text))
                if stop >= len(body):
                    break
                start = max(start + 1, stop - OVERLAP)
    return result, warnings


def model_signature(path):
    files = []
    for p in sorted(path.rglob('*')):
        if p.is_file():
            stat = p.stat()
            files.append((str(p.relative_to(path)), stat.st_size, stat.st_mtime_ns))
    return digest(json.dumps(files, ensure_ascii=False).encode('utf-8'))


def load_model(path, download=False):
    if not path.is_absolute():
        raise ValueError('The model cache must be an absolute local directory.')
    if not download and not path.is_dir():
        raise ValueError('Model cache is missing. Run prepare-model first.')
    try:
        from fastembed import TextEmbedding
    except ImportError as exc:
        raise RuntimeError('Missing fastembed. See references/vector-library.md.') from exc
    return TextEmbedding(model_name='BAAI/bge-small-zh-v1.5', cache_dir=str(path), local_files_only=not download, threads=2)


def embed(model, texts):
    vectors = list(model.embed(texts, batch_size=32))
    output = []
    for row in vectors:
        values = [float(x) for x in row]
        norm = math.sqrt(sum(x * x for x in values))
        if not norm or not math.isfinite(norm) or not all(math.isfinite(x) for x in values):
            raise ValueError('Model produced invalid embeddings')
        output.append([x / norm for x in values])
    return output


def connect_readonly(path):
    if not path.is_file():
        raise ValueError('Index is missing. Run index with --model first.')
    return sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)


def get_meta(db):
    return {k: json.loads(v) for k, v in db.execute('SELECT key, value FROM metadata')}


def build(course, model_path):
    current = snapshot(course)
    if not current:
        raise ValueError('No Markdown files found in course-dir/materials.')
    items, warnings = chunks(course)
    if not items:
        raise ValueError('No non-empty indexed sections found. Check converted Markdown headings and content.')
    model_path = Path(model_path).expanduser()
    model = load_model(model_path)
    signature_before = model_signature(model_path)
    vectors = embed(model, [item[-1] for item in items])
    if current != snapshot(course):
        raise RuntimeError('Materials changed during indexing. Retry.')
    if signature_before != model_signature(model_path):
        raise RuntimeError('Model files changed during indexing. Retry.')
    course.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(prefix='.course-index-', suffix='.sqlite3', dir=course)
    os.close(handle)
    tmp = Path(tmp_name)
    try:
        with closing(sqlite3.connect(tmp)) as db, db:
            db.execute('CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('CREATE TABLE chunks (id TEXT PRIMARY KEY, source TEXT, title TEXT, locator TEXT, part INTEGER, text TEXT, vector BLOB)')
            meta = {'format': 1, 'model': str(model_path.resolve()), 'model_signature': signature_before, 'materials': current, 'dimension': len(vectors[0])}
            db.executemany('INSERT INTO metadata VALUES (?, ?)', [(k, json.dumps(v, ensure_ascii=False)) for k, v in meta.items()])
            for item, vector in zip(items, vectors):
                if len(vector) != meta['dimension']:
                    raise ValueError('Model produced inconsistent vector dimensions')
                db.execute('INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?)', (*item, struct.pack(f'<{len(vector)}f', *vector)))
        os.replace(tmp, course / DB_NAME)
    finally:
        tmp.unlink(missing_ok=True)
    return {'status': 'indexed', 'materials': len(current), 'chunks': len(items), 'model': str(model_path.resolve()), 'warnings': warnings}


def freshness(course, meta):
    before = meta['materials']
    after = snapshot(course)
    return {'added': sorted(set(after) - set(before)), 'removed': sorted(set(before) - set(after)), 'changed': sorted(k for k in set(after) & set(before) if after[k] != before[k])}


def validate_exam_sources(course, questions):
    """Check citation identities against a fresh index without loading the model."""
    course = Path(course)
    with closing(connect_readonly(course / DB_NAME)) as db:
        meta = get_meta(db)
        changes = freshness(course, meta)
        if any(changes.values()):
            raise ValueError('Materials changed since indexing. Run index again before saving a new exam. Changes: ' + json.dumps(changes, ensure_ascii=False))
        for question in questions:
            for source in question['sources']:
                row = db.execute(
                    'SELECT 1 FROM chunks WHERE source=? AND locator=? AND id=?',
                    (source['source_id'], source['locator'], source['chunk_id']),
                ).fetchone()
                if row is None:
                    raise ValueError(f"Citation does not exist in the current index for question {question['id']}: "
                                     f"{source['source_id']} / {source['locator']} / {source['chunk_id']}")


def status(course):
    with closing(connect_readonly(course / DB_NAME)) as db:
        meta = get_meta(db)
        changes = freshness(course, meta)
        count = db.execute('SELECT count(*) FROM chunks').fetchone()[0]
    model_path = Path(meta['model'])
    model_ok = model_path.is_dir() and model_signature(model_path) == meta['model_signature']
    return {'status': 'stale' if any(changes.values()) or not model_ok else 'ready', 'chunks': count, 'model': meta['model'], 'model_matches': model_ok, 'materials': changes}


def search(course, query, limit, sources):
    if not query.strip():
        raise ValueError('Query must not be empty')
    if not 1 <= limit <= 50:
        raise ValueError('Limit must be between 1 and 50')
    with closing(connect_readonly(course / DB_NAME)) as db:
        meta = get_meta(db)
        changes = freshness(course, meta)
        if any(changes.values()):
            raise ValueError('Materials changed since indexing. Run index again before search. Changes: ' + json.dumps(changes, ensure_ascii=False))
        model_path = Path(meta['model'])
        if not model_path.is_dir() or model_signature(model_path) != meta['model_signature']:
            raise ValueError('Index model is missing or changed. Restore it or rebuild index with --model.')
        model = load_model(model_path)
        vector = embed(model, [query])[0]
        if len(vector) != meta['dimension']:
            raise ValueError('Query embedding dimension differs from index')
        matches = []
        for row in db.execute('SELECT id, source, title, locator, part, text, vector FROM chunks'):
            chunk_id, source, title, locator, part, body, packed = row
            if sources and source not in sources:
                continue
            v = struct.unpack(f'<{meta["dimension"]}f', packed)
            score = sum(a * b for a, b in zip(vector, v))
            matches.append({'chunk_id': chunk_id, 'source_id': source, 'source_title': title, 'locator': locator, 'part': part, 'score': round(score, 6), 'excerpt': body})
    matches.sort(key=lambda x: x['score'], reverse=True)
    return {'status': 'ok', 'query': query, 'results': matches[:limit], 'total_candidates': len(matches), 'notice': 'Similarity ranks passages and does not establish factual correctness or exam relevance.'}


def main():
    parser = argparse.ArgumentParser(description='Local semantic index for converted course Markdown')
    parser.add_argument('--course-dir', required=True, help='Absolute local course workspace')
    sub = parser.add_subparsers(dest='command', required=True)
    idx = sub.add_parser('index')
    idx.add_argument('--model', required=True, help='Absolute local FastEmbed model cache directory')
    prep = sub.add_parser('prepare-model', help='Download the model only; no course material is sent')
    prep.add_argument('--model', required=True, help='Absolute local model cache directory')
    sub.add_parser('status')
    find = sub.add_parser('search')
    find.add_argument('--query', required=True)
    find.add_argument('--limit', type=int, default=6)
    find.add_argument('--source', action='append', default=[], help='Exact materials filename; repeat to restrict')
    args = parser.parse_args()
    course = Path(args.course_dir).expanduser()
    if not course.is_absolute():
        raise ValueError('--course-dir must be an absolute path')
    if args.command == 'prepare-model':
        model_path = Path(args.model).expanduser()
        load_model(model_path, download=True)
        result = {'status': 'model_ready', 'model': 'BAAI/bge-small-zh-v1.5', 'cache': str(model_path)}
    elif args.command == 'index':
        require_confirmed(course)
        result = build(course, args.model)
    elif args.command == 'status':
        result = status(course)
    else:
        result = search(course, args.query, args.limit, args.source)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'status': 'error', 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
