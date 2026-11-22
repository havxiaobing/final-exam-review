#!/usr/bin/env python3
"""Convert explicitly supplied course files into locally stored Markdown."""

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

from course_workspace import require_confirmed


def source_id(path):
    canonical = os.path.normcase(str(path.resolve()))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


def markdown_text(text):
    # Source headings are demoted so they cannot impersonate page boundaries.
    lines = []
    fence = None
    for line in text.strip().splitlines():
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
        elif fence is None:
            line = re.sub(r"^#{1,6}([ \t]+)", r"###\1", line)
        lines.append(line)
    return "\n".join(lines)


def extract_pdf(path):
    try:
        import pymupdf
    except ImportError as exc:
        raise RuntimeError("PDF conversion requires PyMuPDF: python -m pip install pymupdf") from exc
    sections = []
    warnings = ["PDF layout, tables, equations, images and reading order require checking against the source."]
    with pymupdf.open(path) as doc:
        if doc.needs_pass:
            raise RuntimeError("PDF is password-protected; supply an unlocked copy.")
        for number, page in enumerate(doc, 1):
            text = page.get_text("text", sort=True).strip()
            sections.append(("Page", number, markdown_text(text)))
            if not text:
                warnings.append(f"Page {number} has no extracted text; it may require OCR or manual transcription.")
            if page.get_images(full=True):
                warnings.append(f"Page {number} contains images that are not transcribed.")
    return sections, warnings


def slide_shapes(shapes):
    for shape in shapes:
        if shape.shape_type == 6:
            yield from slide_shapes(shape.shapes)
        else:
            yield shape


def extract_pptx(path):
    try:
        from pptx import Presentation
    except ImportError as exc:
        raise RuntimeError("PPTX conversion requires python-pptx: python -m pip install python-pptx") from exc
    presentation = Presentation(str(path))
    sections = []
    warnings = ["Slide layout, images, charts, embedded objects and equations require checking against the source; speaker notes are excluded."]
    for number, slide in enumerate(presentation.slides, 1):
        fragments = []
        shapes = sorted(slide_shapes(slide.shapes), key=lambda s: (s.top, s.left))
        for shape in shapes:
            if shape.has_table:
                for row in shape.table.rows:
                    fragments.append(" | ".join(cell.text.replace("\n", " / ") for cell in row.cells))
            elif shape.has_text_frame:
                text = shape.text_frame.text.strip()
                if text:
                    fragments.append(text)
            if shape.shape_type == 13:
                warnings.append(f"Slide {number} contains an image that is not transcribed.")
        content = markdown_text("\n\n".join(fragments))
        sections.append(("Slide", number, content))
        if not content:
            warnings.append(f"Slide {number} has no extracted text; it may require manual transcription.")
    return sections, warnings


def extract_text(path):
    text = path.read_text(encoding="utf-8-sig")
    return [("Section", 1, markdown_text(text))], ([] if text.strip() else ["The file contains no text."])


def convert(path, course_dir, overwrite):
    if not path.is_file():
        raise ValueError("Source is not an existing file.")
    output = course_dir / "materials" / (source_id(path) + ".md")
    if output.exists() and not overwrite:
        return {"source": str(path), "output": str(output), "status": "skipped_existing", "warnings": ["Existing Markdown preserved, including any manual corrections. Use --overwrite only after reviewing it."]}
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        sections, warnings = extract_pdf(path)
    elif suffix == ".pptx":
        sections, warnings = extract_pptx(path)
    elif suffix in (".md", ".txt"):
        sections, warnings = extract_text(path)
    elif suffix == ".ppt":
        raise ValueError("Legacy .ppt is unsupported; save a copy as .pptx in PowerPoint or LibreOffice first.")
    else:
        raise ValueError("Supported formats are .pdf, .pptx, .md and .txt.")
    if not sections:
        raise ValueError("No pages or slides found; existing Markdown was preserved.")
    if not any(content for _, _, content in sections):
        if output.exists():
            raise ValueError("No text was extracted; existing Markdown was preserved.")
        warnings.append("No text was extracted from this source; do not treat it as a complete study source.")
    title = re.sub(r"[\r\n]+", " ", path.name)
    output.parent.mkdir(parents=True, exist_ok=True)
    content = f"# {title}\n\n" + "\n\n".join(f"## {kind} {number}\n\n{body}" for kind, number, body in sections) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=output.parent, delete=False, suffix=".tmp") as stream:
            temporary = Path(stream.name)
            stream.write(content)
        temporary.replace(output)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    return {"source": str(path), "output": str(output), "status": "converted", "sections": len(sections), "empty_sections": [number for _, number, body in sections if not body], "warnings": warnings}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--course-dir", required=True, type=Path, help="Absolute path to the user-selected course directory")
    parser.add_argument("--overwrite", action="store_true", help="Replace existing converted Markdown, including corrections")
    parser.add_argument("files", nargs="+", type=Path, help="Explicit local files to convert")
    args = parser.parse_args()
    if not args.course_dir.is_absolute():
        parser.error("--course-dir must be an absolute path")
    try:
        require_confirmed(args.course_dir)
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    results = []
    for path in args.files:
        try:
            results.append(convert(path.resolve(), args.course_dir.resolve(), args.overwrite))
        except Exception as exc:
            results.append({"source": str(path), "status": "error", "error": str(exc)})
    print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
    return 1 if any(result["status"] == "error" for result in results) else 0


if __name__ == "__main__":
    sys.exit(main())
