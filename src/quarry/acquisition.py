"""The request the kernel's acquisition takes (spec §9), built from a record and its files.
quarry emits it; the kernel retrieves the bytes and mints the dataset. The URL and digest checks
mirror the kernel's validators (beliefs `holdings/records.py`: `_canonical_url`,
`require_canonical_digest`) so a request quarry prints is one the kernel takes, and a refusal
names the file here instead of failing there."""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

from quarry.errors import QuarryError
from quarry.files import File, http_url
from quarry.http import UpstreamError
from quarry.results import Result

BAD_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _fault(url: str) -> str | None:
    """Why the kernel would refuse this http(s) URL, or None when it would take it."""
    if any(not (0x21 <= ord(c) <= 0x7E) for c in url):
        return "carries whitespace, a control byte or a non-ASCII character (the adapter must percent-encode it)"
    if "#" in url:
        return "carries a fragment"
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:          # an unbalanced or non-IPv6 bracket, a port that is not a number in 0-65535
        return f"does not parse ({exc})"
    if "@" in parts.netloc:
        return "carries userinfo"
    if not parts.hostname:
        return "names no host"
    if parts.netloc.startswith("["):
        try:
            ipaddress.IPv6Address(parts.hostname)      # urlsplit also lets an IPvFuture literal through; the kernel does not
        except ValueError:
            return "brackets a host that is not an IPv6 literal"
    if port == 0:
        return "names port 0"
    if BAD_ESCAPE.search(parts.path):
        return "carries a malformed percent escape in its path"
    return None


def _retrievable(file: File) -> str:
    url = http_url(file)
    if url is None:
        where = ", ".join(file.urls) or "no location"
        raise QuarryError("unsupported_transport",
                          f"{file.name} has no http(s) location ({where}); the kernel retrieves over http and https only")
    fault = _fault(url)
    if fault:
        raise QuarryError("unsupported_transport", f"{file.name}: its URL {fault}, which the kernel refuses")
    return url


def _expected(file: File, record: Result) -> str | None:
    """`sha256:<64 lowercase hex>` when the resource published a sha256; a malformed one is the resource's fault."""
    digest = file.checksums.get("sha256")
    if digest is None:
        return None
    if not SHA256.match(digest.lower()):
        raise UpstreamError(f"{record.id}: {file.name} carries a malformed sha256 digest {digest!r}")
    return f"sha256:{digest.lower()}"


def build(record: Result, files: list[File]) -> dict:
    """The locator names the record's canonical CURIE, so every accession of one record maps to one dataset."""
    if not files:
        raise QuarryError("no_files", f"{record.id} has no files to acquire")
    seen: set[str] = set()
    resources = []
    for file in files:
        if file.name in seen:
            raise QuarryError("duplicate_file_name", f"{record.id} lists {file.name!r} twice; narrow the list with --file")
        seen.add(file.name)
        resource = {"name": file.name, "url": _retrievable(file)}
        expected = _expected(file, record)
        if expected:
            resource["expected"] = expected
        resources.append(resource)
    return {"title": record.title or record.id, "locator": f"accession:{record.id}", "resources": resources}
