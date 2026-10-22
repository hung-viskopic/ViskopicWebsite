"""Upload validation: decide the real file type from its content, not its name."""
import io
import zipfile

from ..config import get_settings


class InvalidUpload(ValueError):
    pass


EXTENSIONS = {"pdf": ".pdf", "docx": ".docx", "txt": ".txt"}


def detect_type(filename: str, data: bytes) -> str:
    s = get_settings()
    if not data:
        raise InvalidUpload("the file is empty")
    if len(data) > s.max_upload_bytes:
        raise InvalidUpload(f"the file is larger than {s.max_upload_bytes // (1024 * 1024)} MiB")

    name = filename.lower()
    if data.startswith(b"%PDF-"):
        kind = "pdf"
    elif data.startswith(b"PK\x03\x04"):
        kind = _check_docx(data)
    elif data.startswith(b"\xd0\xcf\x11\xe0"):
        raise InvalidUpload("legacy .doc files are not supported; save as .docx or PDF")
    else:
        kind = _check_txt(data)

    if not name.endswith(EXTENSIONS[kind]):
        raise InvalidUpload(f"the file content is {kind.upper()} but the name does not end in {EXTENSIONS[kind]}")
    return kind


def _check_docx(data: bytes) -> str:
    s = get_settings()
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            infos = z.infolist()
            if len(infos) > 2000:
                raise InvalidUpload("the document archive contains too many parts")
            total = sum(i.file_size for i in infos)
            if total > s.max_docx_uncompressed_bytes:
                raise InvalidUpload("the document expands to an unreasonable size")
            names = {i.filename for i in infos}
    except zipfile.BadZipFile as e:
        raise InvalidUpload("the file looks like a damaged .docx") from e
    if "word/document.xml" not in names or "[Content_Types].xml" not in names:
        raise InvalidUpload("the archive is not a Word .docx document")
    if any(n.lower().endswith("vbadata.xml") or n.lower().endswith("vbaproject.bin") for n in names):
        raise InvalidUpload("documents containing macros are not accepted")
    return "docx"


def _check_txt(data: bytes) -> str:
    if b"\x00" in data:
        raise InvalidUpload("unsupported file type: accepted formats are PDF, DOCX and plain-text UTF-8")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise InvalidUpload("text files must be UTF-8 encoded") from e
    return "txt"
