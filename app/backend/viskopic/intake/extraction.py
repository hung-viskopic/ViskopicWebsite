"""Text extraction plus document signals.

The visible text is what a human reader sees and is what later analysis uses.
Text hidden from the reader (white, tiny or 'hidden' formatting) is reported as a
document signal instead, because it is often an attempt to influence automated tools.

`extract_sandboxed` runs the parsers in a separate process with CPU, memory and time
limits, since document parsers are a common attack surface.
"""
import io
import json
import re
import resource
import subprocess
import sys
import zipfile
from dataclasses import asdict, dataclass, field

from defusedxml import ElementTree as ET

EXTRACTOR_VERSION = "extract-1.0"

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
CP = "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}"
DC = "{http://purl.org/dc/elements/1.1/}"
DCT = "{http://purl.org/dc/terms/}"
EP = "{http://schemas.openxmlformats.org/officeDocument/2006/extended-properties}"

WHITE = {"FFFFFF", "FFFFFE", "FEFEFE"}
TINY_HALF_POINTS = 4  # 2pt or smaller


class ExtractionError(Exception):
    def __init__(self, message: str, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent


@dataclass
class Extracted:
    text: str
    word_count: int
    char_count: int
    hidden_text_chars: int = 0
    hidden_text_spans: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    extractor_version: str = EXTRACTOR_VERSION


def count_words(text: str) -> int:
    return len(text.split())


def _finish(text: str, hidden: list[dict], metadata: dict) -> Extracted:
    text = re.sub(r"[ \t]+\n", "\n", text.replace("\r\n", "\n")).strip()
    if count_words(text) == 0:
        raise ExtractionError(
            "no readable text found (an image-only PDF needs OCR, which is not supported yet)", permanent=True
        )
    return Extracted(
        text=text,
        word_count=count_words(text),
        char_count=len(text),
        hidden_text_chars=sum(h["chars"] for h in hidden),
        hidden_text_spans=hidden,
        metadata=metadata,
    )


# ------------------------------------------------------------------ docx

def _run_hidden_reason(rpr) -> str | None:
    if rpr is None:
        return None
    vanish = rpr.find(f"{W}vanish")
    if vanish is not None and vanish.get(f"{W}val", "true") not in ("false", "0"):
        return "hidden_formatting"
    color = rpr.find(f"{W}color")
    if color is not None and (color.get(f"{W}val") or "").upper() in WHITE:
        return "white_text"
    size = rpr.find(f"{W}sz")
    if size is not None:
        try:
            if int(size.get(f"{W}val", "24")) <= TINY_HALF_POINTS:
                return "tiny_text"
        except ValueError:
            pass
    return None


def extract_docx(data: bytes) -> Extracted:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        root = ET.fromstring(z.read("word/document.xml"))
    except Exception as e:
        raise ExtractionError(f"could not read the Word document: {type(e).__name__}", permanent=True) from e

    paragraphs: list[str] = []
    hidden: dict[str, int] = {}
    for p in root.iter(f"{W}p"):
        parts = []
        for r in p.iter(f"{W}r"):
            run_text = "".join(
                (t.text or "") if t.tag == f"{W}t" else ("\t" if t.tag == f"{W}tab" else "\n" if t.tag == f"{W}br" else "")
                for t in r
            )
            if not run_text:
                continue
            reason = _run_hidden_reason(r.find(f"{W}rPr"))
            if reason:
                hidden[reason] = hidden.get(reason, 0) + len(run_text.strip())
            else:
                parts.append(run_text)
        paragraphs.append("".join(parts))

    metadata = _docx_metadata(z)
    spans = [{"reason": k, "chars": v} for k, v in sorted(hidden.items()) if v > 0]
    return _finish("\n".join(paragraphs), spans, metadata)


def _docx_metadata(z: zipfile.ZipFile) -> dict:
    meta: dict = {}
    names = set(z.namelist())
    if "docProps/core.xml" in names:
        core = ET.fromstring(z.read("docProps/core.xml"))
        for key, tag in [("author", f"{DC}creator"), ("last_modified_by", f"{CP}lastModifiedBy"),
                         ("created", f"{DCT}created"), ("modified", f"{DCT}modified"), ("revision", f"{CP}revision")]:
            el = core.find(tag)
            if el is not None and el.text:
                meta[key] = el.text.strip()
    if "docProps/app.xml" in names:
        app = ET.fromstring(z.read("docProps/app.xml"))
        for key, tag in [("application", f"{EP}Application"), ("total_edit_minutes", f"{EP}TotalTime"),
                         ("declared_words", f"{EP}Words")]:
            el = app.find(tag)
            if el is not None and el.text:
                meta[key] = el.text.strip()
    for k in ("revision", "total_edit_minutes", "declared_words"):
        if k in meta:
            try:
                meta[k] = int(meta[k])
            except ValueError:
                pass
    return meta


# ------------------------------------------------------------------ pdf

def extract_pdf(data: bytes) -> Extracted:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ExtractionError("the PDF is password-protected", permanent=True)
        pages, invisible_pages = [], 0
        for page in reader.pages:
            pages.append(page.extract_text() or "")
            contents = page.get_contents()
            if contents is not None and re.search(rb"(^|\s)3\s+Tr\b", contents.get_data()):
                invisible_pages += 1
        info = reader.metadata or {}
    except ExtractionError:
        raise
    except Exception as e:
        raise ExtractionError(f"could not read the PDF: {type(e).__name__}", permanent=True) from e

    meta = {}
    for key, attr in [("author", "/Author"), ("creator_tool", "/Creator"), ("producer", "/Producer"),
                      ("created", "/CreationDate"), ("modified", "/ModDate")]:
        v = info.get(attr)
        if v:
            meta[key] = str(v)
    meta["pages"] = len(pages)
    spans = [{"reason": "invisible_render_mode", "chars": 0, "pages": invisible_pages}] if invisible_pages else []
    return _finish("\n\n".join(pages), spans, meta)


# ------------------------------------------------------------------ txt

def extract_txt(data: bytes) -> Extracted:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise ExtractionError("text files must be UTF-8 encoded", permanent=True) from e
    return _finish(text, [], {})


EXTRACTORS = {"pdf": extract_pdf, "docx": extract_docx, "txt": extract_txt}


def extract(kind: str, data: bytes) -> Extracted:
    return EXTRACTORS[kind](data)


# ------------------------------------------------------------------ sandbox

def _limits(memory_mb: int, cpu_seconds: int):
    def apply():
        resource.setrlimit(resource.RLIMIT_AS, (memory_mb * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    return apply


def extract_sandboxed(kind: str, data: bytes, timeout: int = 60, memory_mb: int = 512) -> Extracted:
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "viskopic.intake.extraction", kind],
            input=data,
            capture_output=True,
            timeout=timeout,
            preexec_fn=_limits(memory_mb, timeout),
            cwd=_package_root(),
        )
    except subprocess.TimeoutExpired as e:
        raise ExtractionError("extraction took too long and was stopped", permanent=True) from e
    if proc.returncode != 0:
        try:
            err = json.loads(proc.stdout)
            raise ExtractionError(err["error"], permanent=err.get("permanent", False))
        except (json.JSONDecodeError, KeyError):
            raise ExtractionError("the extractor crashed or ran out of memory") from None
    return Extracted(**json.loads(proc.stdout))


def _package_root() -> str:
    import pathlib
    return str(pathlib.Path(__file__).resolve().parents[2])


if __name__ == "__main__":
    kind_arg = sys.argv[1]
    payload = sys.stdin.buffer.read()
    try:
        result = extract(kind_arg, payload)
    except ExtractionError as exc:
        print(json.dumps({"error": str(exc), "permanent": exc.permanent}))
        sys.exit(2)
    except Exception as exc:  # unexpected parser failure: retryable
        print(json.dumps({"error": f"unexpected extraction failure: {type(exc).__name__}", "permanent": False}))
        sys.exit(3)
    print(json.dumps(asdict(result)))
