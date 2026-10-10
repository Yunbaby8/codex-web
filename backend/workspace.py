from pathlib import Path

class Workspaces:
    def __init__(self, roots):
        if not roots:
            raise ValueError('至少配置一个 allowed_roots')
        self.roots = [Path(r).expanduser().resolve(strict=True) for r in roots]

    def resolve(self, value):
        path = Path(value).expanduser().resolve(strict=True)
        if not path.is_dir() or not any(path == r or r in path.parents for r in self.roots):
            raise ValueError('目录不在允许范围内')
        return path

    def resolve_file(self, value):
        path = Path(value).expanduser().resolve(strict=True)
        if not path.is_file() or not any(path == r or r in path.parents for r in self.roots):
            raise ValueError('文件不在允许范围内')
        return path

    def browse(self, value):
        path = self.resolve(value)
        children = []
        for child in path.iterdir():
            try:
                target = self.resolve(str(child))
                children.append({'name': child.name, 'path': str(target)})
            except (ValueError, OSError, RuntimeError):
                continue
        try:
            parent = str(self.resolve(str(path.parent)))
        except (ValueError, OSError):
            parent = None
        return {'path': str(path), 'parent': parent, 'directories': sorted(children, key=lambda c: c['name'].lower())}
