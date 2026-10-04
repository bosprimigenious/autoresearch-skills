#!/usr/bin/env python3
"""Fail-closed public-release privacy scanner with redacted diagnostics."""

from __future__ import annotations

import argparse
import hashlib
import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qsl, urlsplit


ARCHIVE_SUFFIXES = {".zip", ".docx", ".pptx", ".xlsx", ".odt", ".ods", ".odp", ".jar", ".whl"}
TEXT_SUFFIXES = {
    ".cfg", ".conf", ".css", ".csv", ".env", ".html", ".ini", ".json", ".jsonl",
    ".md", ".py", ".rst", ".sh", ".toml", ".tsv", ".txt", ".xml", ".yaml", ".yml",
}
MAX_MEMBER_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 10_000
MAX_ARCHIVE_DEPTH = 2
MAX_FILE_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True)
class Finding:
    code: str
    path: Path
    line: int
    member: str | None = None


PATTERNS = {
    "MAC_HOME": re.compile(r"/" + r"Users/[A-Za-z0-9._-]+/"),
    "LINUX_HOME": re.compile(r"/" + r"home/[A-Za-z0-9._-]+/"),
    "WINDOWS_HOME": re.compile(r"[A-Za-z]:\\" + r"Users\\[^\\\s]+\\", re.IGNORECASE),
    "PRIVATE_KEY": re.compile("BEGIN " + r"(?:RSA |EC |OPENSSH )?PRIVATE KEY"),
    "API_TOKEN": re.compile(
        r"(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
        r"xox[baprs]-[A-Za-z0-9-]{10,}|AKIA[A-Z0-9]{16})"
    ),
    "JWT": re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    "BEARER_TOKEN": re.compile(
        r"(?:Authorization\s*:\s*Bearer|Bearer)\s+(?![<$\[{])[A-Za-z0-9._~+/-]{12,}", re.IGNORECASE
    ),
    "PRIVATE_COLLAB_URL": re.compile(
        r"https?://[^\s)\]>'\"]*(?:" + "feishu" + r"\.cn|" + "larkoffice" + r"\.com)[^\s)\]>'\"]*",
        re.IGNORECASE,
    ),
    "PRIVATE_IPV4": re.compile(
        r"(?<!\d)(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|"
        r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})(?!\d)"
    ),
    "EMAIL": re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w.-])"),
}

URL_PATTERN = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
CAPABILITY_KEYS = {
    "access_token", "auth", "authorization", "code", "credential", "key", "password",
    "secret", "signature", "sig", "token", "x-amz-credential", "x-amz-signature",
    "x-goog-credential", "x-goog-signature",
}
CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)\b(?:api[_-]?key|access[_-]?key|client[_-]?secret|secret[_-]?key|password|passwd|"
    r"auth[_-]?token|access[_-]?token|refresh[_-]?token|api[_-]?token|token|secret|credential)"
    r"\b\s*[=:]\s*[\"']?([^\s\"',;}]{4,})"
)
METADATA_PATTERN = re.compile(
    r"(?is)<(?:(?:dc:)?creator|cp:lastModifiedBy|Company|Manager|author)(?:\s[^>]*)?>([^<]+)</"
)
METADATA_ATTRIBUTE_PATTERN = re.compile(
    r"(?is)(?:\b(?:w:)?author\s*=|<p:cmAuthor\b[^>]*\bname\s*=)\s*[\"']([^\"']+)[\"']"
)
ASCII_STRING = re.compile(rb"[\x20-\x7e]{8,}")
UTF16_LE_STRING = re.compile(rb"(?:[\x20-\x7e]\x00){8,}")
UTF16_BE_STRING = re.compile(rb"(?:\x00[\x20-\x7e]){8,}")

SENSITIVE_EXACT_NAMES = {
    ".env", ".git-credentials", ".netrc", ".npmrc", ".pypirc", "authorized_keys", "credentials",
    "credentials.json", "id_dsa", "id_ecdsa", "id_ed25519", "id_rsa", "service-account.json",
    "service_account.json",
}
SENSITIVE_SUFFIXES = {".key", ".p12", ".pfx", ".pem"}
SAFE_FILENAME_SUFFIXES = {".example", ".sample", ".template"}
TRANSIENT_ARCHIVE_PARTS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
TRANSIENT_ARCHIVE_SUFFIXES = {".pyc", ".pyo"}
PLACEHOLDER_WORDS = {
    "changeme", "dummy", "example", "fake", "placeholder", "redacted", "replace-me",
    "false", "none", "null", "replace_me", "sample", "test", "todo", "true", "your-api-key",
    "your_api_key", "xxx",
}
RESERVED_EMAIL_DOMAINS = {"example.com", "example.net", "example.org"}


def _is_placeholder(value: str) -> bool:
    normalized = value.strip().strip("'\"").lower()
    if not normalized:
        return True
    if normalized.startswith(("${", "{{", "{", "<", "$", "%")):
        return True
    if normalized in PLACEHOLDER_WORDS or normalized.replace("*", "") == "":
        return True
    return any(word in normalized for word in ("placeholder", "redacted", "your_", "your-"))


def _is_sensitive_name(name: str) -> bool:
    normalized = name.replace("\\", "/").lower()
    basename = PurePosixPath(normalized).name
    if any(basename.endswith(suffix) for suffix in SAFE_FILENAME_SUFFIXES):
        return False
    private_config_paths = (".aws/credentials", ".kube/config", ".docker/config.json", "gcloud/application_default_credentials.json")
    return (
        basename in SENSITIVE_EXACT_NAMES
        or basename.startswith(".env.")
        or any(basename.endswith(suffix) for suffix in SENSITIVE_SUFFIXES)
        or any(normalized.endswith(pattern) for pattern in private_config_paths)
        or "/.ssh/" in f"/{normalized}"
    )


def _url_has_capability(value: str) -> bool:
    try:
        parsed = urlsplit(value.rstrip(".,;:)"))
        query = parse_qsl(parsed.query, keep_blank_values=True)
    except ValueError:
        return True
    query_has_secret = any(
        key.lower() in CAPABILITY_KEYS and not _is_placeholder(item) for key, item in query
    )
    authority_has_secret = parsed.password is not None and not _is_placeholder(parsed.password)
    return query_has_secret or authority_has_secret


def _location_is_sensitive(location: str) -> bool:
    if _is_sensitive_name(location) or any(pattern.search(location) for pattern in PATTERNS.values()):
        return True
    if any(not _is_placeholder(match.group(1)) for match in CREDENTIAL_ASSIGNMENT.finditer(location)):
        return True
    return any(_url_has_capability(match.group(0)) for match in URL_PATTERN.finditer(location))


def _scan_text(text: str, path: Path, member: str | None = None) -> list[Finding]:
    findings: list[Finding] = []
    for pattern in (METADATA_PATTERN, METADATA_ATTRIBUTE_PATTERN):
        for match in pattern.finditer(text):
            if not _is_placeholder(match.group(1)):
                line_number = text.count("\n", 0, match.start()) + 1
                findings.append(Finding("DOCUMENT_AUTHOR_METADATA", path, line_number, member))
    for line_number, line in enumerate(text.splitlines(), 1):
        for code, pattern in PATTERNS.items():
            matches = list(pattern.finditer(line))
            if code == "EMAIL":
                matches = [m for m in matches if m.group(0).rsplit("@", 1)[-1].lower() not in RESERVED_EMAIL_DOMAINS]
            if matches:
                findings.append(Finding(code, path, line_number, member))
        for match in CREDENTIAL_ASSIGNMENT.finditer(line):
            if not _is_placeholder(match.group(1)):
                findings.append(Finding("CREDENTIAL_ASSIGNMENT", path, line_number, member))
        for match in URL_PATTERN.finditer(line):
            try:
                parsed = urlsplit(match.group(0).rstrip(".,;:)"))
            except ValueError:
                findings.append(Finding("MALFORMED_URL", path, line_number, member))
                continue
            if _url_has_capability(match.group(0)):
                findings.append(Finding("CAPABILITY_URL", path, line_number, member))
    return findings


def _decode_text(data: bytes, suffix: str) -> str | None:
    encodings = ["utf-8-sig"]
    if data.startswith((b"\xff\xfe", b"\xfe\xff")) or suffix in TEXT_SUFFIXES:
        encodings.append("utf-16")
    for encoding in encodings:
        try:
            decoded = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if suffix in TEXT_SUFFIXES or "\x00" not in decoded:
            return decoded
    return None


def _scan_binary_strings(data: bytes, path: Path, member: str | None) -> list[Finding]:
    fragments = [match.group(0).decode("ascii") for match in ASCII_STRING.finditer(data)]
    fragments.extend(match.group(0).decode("utf-16-le") for match in UTF16_LE_STRING.finditer(data))
    fragments.extend(match.group(0).decode("utf-16-be") for match in UTF16_BE_STRING.finditer(data))
    return _scan_text("\n".join(fragments), path, member)


def _scan_name(name: str, path: Path, member: str | None = None) -> list[Finding]:
    findings: list[Finding] = []
    normalized = name.replace("\\", "/")
    parts = PurePosixPath(normalized).parts
    if normalized.startswith("/") or ".." in parts:
        findings.append(Finding("UNSAFE_ARCHIVE_PATH" if member else "UNSAFE_PATH", path, 0, member))
    if _is_sensitive_name(normalized):
        findings.append(Finding("SENSITIVE_FILENAME", path, 0, member))
    basename = PurePosixPath(normalized).name
    if member is not None and (
        any(part in TRANSIENT_ARCHIVE_PARTS for part in parts)
        or basename.endswith(tuple(TRANSIENT_ARCHIVE_SUFFIXES))
        or basename == ".DS_Store"
        or basename.startswith("._")
    ):
        findings.append(Finding("TRANSIENT_ARCHIVE_ARTIFACT", path, 0, member))
    findings.extend(_scan_text(normalized, path, member))
    return findings


def _scan_archive(data: bytes, path: Path, member: str | None, depth: int) -> list[Finding]:
    findings: list[Finding] = []
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (OSError, zipfile.BadZipFile):
        return [Finding("ARCHIVE_UNREADABLE", path, 0, member)]
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_MEMBERS:
            return [Finding("ARCHIVE_MEMBER_LIMIT", path, 0, member)]
        if sum(info.file_size for info in infos) > MAX_ARCHIVE_BYTES:
            return [Finding("ARCHIVE_SIZE_LIMIT", path, 0, member)]
        if archive.comment:
            comment_member = "@archive-comment" if member is None else f"{member}!@archive-comment"
            findings.extend(_scan_binary_strings(archive.comment, path, comment_member))
        for info in infos:
            child = info.filename if member is None else f"{member}!{info.filename}"
            findings.extend(_scan_name(info.filename, path, child))
            if info.comment or info.extra:
                findings.extend(_scan_binary_strings(info.comment + info.extra, path, f"{child}!@metadata"))
            if info.is_dir():
                continue
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                findings.append(Finding("ARCHIVE_SYMLINK", path, 0, child))
                continue
            if info.flag_bits & 0x1:
                findings.append(Finding("ARCHIVE_ENCRYPTED", path, 0, child))
                continue
            if info.file_size > MAX_MEMBER_BYTES:
                findings.append(Finding("ARCHIVE_MEMBER_SIZE_LIMIT", path, 0, child))
                continue
            try:
                payload = archive.read(info)
            except (OSError, RuntimeError, NotImplementedError, zipfile.BadZipFile):
                findings.append(Finding("ARCHIVE_MEMBER_UNREADABLE", path, 0, child))
                continue
            suffix = Path(info.filename).suffix.lower()
            if suffix in ARCHIVE_SUFFIXES:
                if depth >= MAX_ARCHIVE_DEPTH:
                    findings.append(Finding("ARCHIVE_DEPTH_LIMIT", path, 0, child))
                else:
                    findings.extend(_scan_archive(payload, path, child, depth + 1))
                continue
            text = _decode_text(payload, suffix)
            findings.extend(_scan_text(text, path, child) if text is not None else _scan_binary_strings(payload, path, child))
    return findings


def scan(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in sorted(root.rglob("*")):
        if ".git" in path.parts or "__pycache__" in path.parts:
            continue
        if path.is_symlink():
            findings.append(Finding("SYMLINK", path, 0))
            continue
        if not path.is_file():
            continue
        findings.extend(_scan_name(path.relative_to(root).as_posix(), path))
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                findings.append(Finding("FILE_SIZE_LIMIT", path, 0))
                continue
            data = path.read_bytes()
        except OSError:
            findings.append(Finding("FILE_UNREADABLE", path, 0))
            continue
        suffix = path.suffix.lower()
        if suffix in ARCHIVE_SUFFIXES:
            findings.extend(_scan_archive(data, path, None, 0))
            continue
        text = _decode_text(data, suffix)
        findings.extend(_scan_text(text, path) if text is not None else _scan_binary_strings(data, path, None))
    return sorted(set(findings), key=lambda item: (str(item.path), item.member or "", item.line, item.code))


def _safe_locator(finding: Finding, root: Path) -> str:
    relative = finding.path.relative_to(root).as_posix()
    pieces = [relative]
    if finding.member:
        pieces.extend(finding.member.split("!"))
    safe = []
    for piece in pieces:
        if _location_is_sensitive(piece):
            digest = hashlib.sha256(piece.encode("utf-8", errors="replace")).hexdigest()[:12]
            safe.append(f"<redacted-{digest}>")
        else:
            safe.append(piece)
    return "!".join(safe)


def format_finding(finding: Finding, root: Path) -> str:
    return f"{finding.code}:{_safe_locator(finding, root)}:{finding.line}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    findings = scan(root)
    if findings:
        for finding in findings:
            print(format_finding(finding, root))
        print(f"FAIL: {len(findings)} privacy finding(s); matched values and sensitive locations were suppressed")
        return 1
    print("PASS: no blocked private identifiers, secret patterns, or unreadable release containers found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
