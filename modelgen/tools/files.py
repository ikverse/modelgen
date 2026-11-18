"""Reading and writing inside one project folder only. Any path that would leave it is refused."""

from pathlib import Path


class OutsideProject(ValueError):
    pass


class ProjectFiles:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def path(self, rel):
        rel = str(rel).replace("\\", "/")
        if not rel or rel.startswith("/") or ":" in rel:
            raise OutsideProject(f"'{rel}' is not a path inside the project")
        full = (self.root / rel).resolve()
        if full != self.root and not full.is_relative_to(self.root):
            raise OutsideProject(f"'{rel}' is outside the project folder")
        return full

    def exists(self, rel):
        return self.path(rel).exists()

    def read(self, rel):
        return self.path(rel).read_text(encoding="utf-8")

    def write(self, rel, text):
        full = self.path(rel)
        full.parent.mkdir(parents=True, exist_ok=True)
        tmp = full.with_name(full.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(full)
        return full

    def list(self, rel="."):
        folder = self.path(rel) if rel != "." else self.root
        if not folder.is_dir():
            return []
        return sorted(str(p.relative_to(self.root)).replace("\\", "/") for p in folder.rglob("*") if p.is_file())
