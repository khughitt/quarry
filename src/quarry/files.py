"""A record's files (spec §8, §9): what an adapter lists, and how --file narrows the list."""
from __future__ import annotations

import fnmatch
from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class File:
    name: str
    urls: tuple[str, ...] = ()                     # every location the resource gives, https first when it has one
    size: int | None = None
    checksums: Mapping[str, str] = field(default_factory=dict)   # algorithm -> hex digest
    media_type: str | None = None
    license: str | None = None

    def as_dict(self) -> dict:
        out: dict = {"name": self.name, "urls": list(self.urls)}
        if self.size is not None:
            out["size"] = self.size
        if self.checksums:
            out["checksums"] = dict(self.checksums)
        if self.media_type is not None:
            out["media_type"] = self.media_type
        if self.license is not None:
            out["license"] = self.license
        return out


def select(files: list[File], globs: tuple[str, ...]) -> list[File]:
    """The files whose name matches any glob; every file when there is none. Case matters."""
    if not globs:
        return list(files)
    return [f for f in files if any(fnmatch.fnmatchcase(f.name, g) for g in globs)]


def http_url(file: File) -> str | None:
    """The location the kernel can retrieve: https before http; None when the file has neither."""
    for scheme in ("https://", "http://"):
        for url in file.urls:
            if url.startswith(scheme):
                return url
    return None
