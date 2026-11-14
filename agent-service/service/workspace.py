"""File store and per-run working directories.

Layout under the data dir:
    files/<file_id>/meta.json (+ blob.<ext> for uploads; registered files are referenced in place)
    runs/<run_id>/run.json, runs/<run_id>/ws/   <- the agent's working directory

The agent never sees original file names (they can leak the answer): inside a workspace files are
called drawing.<ext>, drawing_2.<ext>, ...
"""
from __future__ import annotations

import json
import re
import secrets
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import ALLOWED_EXT

ID_RE = re.compile(r"^[0-9a-f]{16}$")


class NotFound(Exception):
    """Unknown id or a path that does not exist inside its container."""


class Forbidden(Exception):
    """A path that points outside the directory it must stay in."""


def new_id() -> str:
    return secrets.token_hex(8)


def check_id(value: str) -> str:
    """Ids are used as directory names, so anything that is not one of ours is simply unknown."""
    if not ID_RE.match(value):
        raise NotFound(value)
    return value


def contained(root: Path, candidate: str | Path) -> Path:
    """Resolve `candidate` (relative to `root` unless absolute) and insist the result is inside `root`.

    Symlinks are resolved first, so a link inside the workspace that points elsewhere is refused too.
    """
    root = root.resolve()
    try:
        p = (root / candidate).resolve()
    except (OSError, ValueError, RuntimeError) as e:  # NUL bytes, symlink loops
        raise Forbidden(str(candidate)) from e
    if p != root and root not in p.parents:
        raise Forbidden(str(candidate))
    return p


def extension(name: str) -> str | None:
    ext = Path(name).suffix.lower().lstrip(".")
    return ext if ext in ALLOWED_EXT else None


def _copy_dir_cow(src: Path, dst: Path) -> None:
    """Copy `src` directory to `dst` using Btrfs/XFS CoW (`cp --reflink=auto -r`) with `shutil.copytree` fallback.
    Never creates symlinks so `contained()` inside `ws/` stays happy."""
    if dst.exists():
        shutil.rmtree(dst)
    try:
        r = subprocess.run(["cp", "--reflink=auto", "-r", str(src), str(dst)], capture_output=True, timeout=30)
        if r.returncode == 0 and dst.is_dir():
            return
    except (OSError, subprocess.SubprocessError):
        pass
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst)


@dataclass
class FileMeta:
    file_id: str
    ext: str
    original_name: str
    size: int
    path: str          # where the bytes live (our blob, or the registered file)
    registered: bool
    precomputed_dir: str | None = None


class FileStore:
    def __init__(self, data_dir: Path):
        self.dir = data_dir / "files"
        self.dir.mkdir(parents=True, exist_ok=True)

    def new_blob(self, ext: str) -> tuple[str, Path]:
        """Reserve an id and return where the upload should be written."""
        fid = new_id()
        (self.dir / fid).mkdir()
        return fid, self.dir / fid / f"blob.{ext}"

    def discard(self, file_id: str) -> None:
        shutil.rmtree(self.dir / file_id, ignore_errors=True)

    def delete(self, file_id: str) -> str:
        """Delete a stored/registered file record by id, or raise NotFound."""
        fid = check_id(file_id)
        if not (self.dir / fid / "meta.json").is_file():
            raise NotFound(file_id)
        self.discard(fid)
        return fid

    def commit(self, file_id: str, original_name: str, path: Path, registered: bool,
               precomputed_dir: str | None = None) -> FileMeta:
        if registered:
            (self.dir / file_id).mkdir()
        meta = FileMeta(file_id, path.suffix.lower().lstrip("."), Path(original_name).name, path.stat().st_size,
                        str(path), registered, precomputed_dir=precomputed_dir)
        (self.dir / file_id / "meta.json").write_text(json.dumps(asdict(meta), ensure_ascii=False))
        return meta

    def register(self, path: str, root: Path, precomputed_dir: str | None = None) -> FileMeta:
        """Reference a file already on the server; only inside `root`."""
        p = contained(root, path)
        if not p.is_file():
            raise NotFound(path)
        if extension(p.name) is None:
            raise ValueError(f"extension not allowed: {p.suffix or '(none)'}")
        pre_str = None
        if precomputed_dir is not None:
            pre_p = contained(root, precomputed_dir)
            if not pre_p.is_dir():
                raise NotFound(precomputed_dir)
            pre_str = str(pre_p)
        return self.commit(new_id(), p.name, p, registered=True, precomputed_dir=pre_str)

    def get(self, file_id: str) -> FileMeta:
        try:
            meta = FileMeta(**json.loads((self.dir / check_id(file_id) / "meta.json").read_text()))
        except (OSError, ValueError, TypeError) as e:
            raise NotFound(file_id) from e
        if not Path(meta.path).is_file():
            raise NotFound(file_id)
        return meta


class Workspace:
    """The agent's working directory for one run."""

    def __init__(self, root: Path):
        self.root = root
        self.inputs: list[str] = []  # neutral names of the files placed here

    def place(self, files: list[FileMeta]) -> list[dict]:
        """Copy the files in under neutral names; -> [{file_id, name, original_name}] for the run metadata.

        A copy, not a hard link: the agent has a shell and must not be able to modify the stored original.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        out = []
        for i, m in enumerate(files, 1):
            name = f"drawing.{m.ext}" if i == 1 else f"drawing_{i}.{m.ext}"
            shutil.copyfile(m.path, self.root / name)
            self.inputs.append(name)
            if m.precomputed_dir:
                pre_src = Path(m.precomputed_dir)
                if pre_src.is_dir():
                    pre_name = "_precomputed" if i == 1 else f"_precomputed_{i}"
                    _copy_dir_cow(pre_src, self.root / pre_name)
            out.append(dict(file_id=m.file_id, name=name, original_name=m.original_name))
        return out

    def resolve(self, rel: str) -> Path:
        """An existing regular file inside the workspace, or Forbidden / NotFound."""
        p = contained(self.root, rel)
        if not p.is_file():
            raise NotFound(rel)
        return p

    def artifacts(self) -> list[dict]:
        """Everything the agent left behind (inputs, _precomputed*, hidden files and escaping symlinks excluded)."""
        if not self.root.is_dir():
            return []
        out = []
        for p in sorted(self.root.rglob("*")):
            rel = p.relative_to(self.root)
            if (any(part.startswith(".") for part in rel.parts)
                    or str(rel) in self.inputs
                    or (rel.parts and rel.parts[0].startswith("_precomputed"))):
                continue
            try:
                if p.is_file() and contained(self.root, rel):
                    out.append(dict(name=str(rel), size=p.stat().st_size))
            except (Forbidden, OSError):
                continue
        return out
