"""Read-only Git status and bounded text snapshots; never runs a shell; comparisons use raw Git blobs."""
import os
import re
import subprocess
import tempfile
from pathlib import Path, PureWindowsPath

from fastapi import HTTPException
from .editor_files import MAX_EDITOR_BYTES


class GitView:
    def __init__(self, editor_files):
        self.files = editor_files

    def run(self, cwd, *args, check=True):
        unc = PureWindowsPath(str(cwd)).as_posix().split('/')
        wsl = os.name == 'nt' and len(unc) >= 4 and unc[:2] == ['', ''] and unc[2].lower() in ('wsl.localhost', 'wsl$')
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        env.update(GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0', GIT_NO_LAZY_FETCH='1', LC_ALL='C')
        command = ['git', '--no-optional-locks', '--literal-pathspecs', '-c', 'core.fsmonitor=false',
                   '-c', 'core.untrackedCache=false', '-c', 'core.hooksPath=' + ('/dev/null' if wsl else os.devnull), *args]
        if args and args[0] == 'status':
            # Status may hash dirty worktree files. Disable configured clean/process
            # filters so a read-only UI does not launch repository filter commands.
            _, filters = self.run(cwd, 'config', '--null', '--name-only', '--get-regexp',
                                  r'^filter\..*\.(clean|process|required)$', check=False)
            names = {os.fsdecode(key).rsplit('.', 1)[0] for key in filters.split(b'\0') if key}
            overrides = []
            for name in names:
                overrides += ['-c', name + '.clean=', '-c', name + '.process=', '-c', name + '.required=false']
            command[1:1] = overrides
        if wsl:
            env['WSL_UTF8'] = '1'
            command = ['wsl.exe', '-d', unc[3], '--cd', '/' + '/'.join(unc[4:]), '--exec',
                       'env', 'GIT_OPTIONAL_LOCKS=0', 'GIT_TERMINAL_PROMPT=0',
                       'GIT_NO_LAZY_FETCH=1', 'LC_ALL=C', *command]
        try:
            with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
                result = subprocess.run(command, cwd=None if wsl else cwd, env=env, stdout=out, stderr=err,
                                        timeout=15, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                if out.tell() > 4 * 1024 * 1024:
                    raise HTTPException(413, 'Git 输出过大，请选择更小的工作区')
                out.seek(0); data = out.read()
                if result.returncode and check:
                    raise HTTPException(409, 'Git 读取失败，仓库可能正在变化，请刷新后重试')
                return result.returncode, data
        except FileNotFoundError as exc:
            raise HTTPException(503, '服务器未安装 Git') from exc
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(504, 'Git 读取超过 15 秒，请稍后刷新或选择更小的工作区') from exc

    def repository(self, workspace):
        root = self.files.workspaces.resolve(workspace)
        code, data = self.run(root, 'rev-parse', '--show-toplevel', check=False)
        if code:
            # Distinguish a non-repository from permissions/ownership/config errors.
            if any((p / '.git').exists() for p in [root, *root.parents]):
                raise HTTPException(409, '无法读取 Git 仓库，请检查仓库权限及所有权配置')
            return root, None
        repo_path = os.fsdecode(data.rstrip(b'\n'))
        unc = PureWindowsPath(str(root)).as_posix().split('/')
        if os.name == 'nt' and repo_path.startswith('/') and len(unc) >= 4 and unc[:2] == ['', ''] and unc[2].lower() in ('wsl.localhost', 'wsl$'):
            repo_path = '//' + unc[2] + '/' + unc[3] + repo_path
        repo = self.files.workspaces.resolve(repo_path)
        if root != repo and repo not in root.parents:
            raise HTTPException(403, 'Git 仓库与工作区不匹配')
        return root, repo

    @staticmethod
    def relative(path, root, repo):
        parts = path.split('/')
        if path.startswith('/') or any(p in ('', '.', '..', '.git') for p in parts):
            raise HTTPException(403, '文件不在当前工作区内')
        try:
            return (repo / path).relative_to(root).as_posix()
        except ValueError as exc:
            raise HTTPException(403, '文件不在当前工作区内') from exc

    def changes(self, root, repo):
        _, raw = self.run(root, 'status', '--porcelain=v1', '-z', '--untracked-files=all',
                          '--ignore-submodules=none', '--renames', '--', '.')
        records = iter(raw.split(b'\0'))
        entries = []
        for record in records:
            if not record:
                continue
            xy, path = record[:2].decode('ascii'), os.fsdecode(record[3:])
            old = os.fsdecode(next(records)) if 'R' in xy or 'C' in xy else None
            path = self.relative(path, root, repo)
            try:
                original = self.relative(old, root, repo) if old else path
            except HTTPException:
                original = None
            if len(entries) >= 10000:
                raise HTTPException(413, '改动超过 10000 项，请选择更小的工作区')
            if xy in ('DD', 'AU', 'UD', 'UA', 'DU', 'AA', 'UU'):
                entries.append(dict(path=path, originalPath=original, group='conflicts', status='U'))
            elif xy == '??':
                entries.append(dict(path=path, originalPath=path, group='untracked', status='?'))
            else:
                for index, group in [(0, 'staged'), (1, 'unstaged')]:
                    if xy[index] != ' ':
                        entries.append(dict(path=path, originalPath=original if index == 0 else path,
                                            group=group, status=xy[index]))
        return entries

    def status(self, workspace):
        root, repo = self.repository(workspace)
        if repo is None:
            return dict(repository=False, branch=None, entries=[])
        code, branch = self.run(root, 'symbolic-ref', '--quiet', '--short', 'HEAD', check=False)
        detached = bool(code)
        if detached:
            _, branch = self.run(root, 'rev-parse', '--short', 'HEAD')
        return dict(repository=True, branch=os.fsdecode(branch.rstrip(b'\n')), detached=detached,
                    entries=self.changes(root, repo))

    def blob(self, root, repo, path, revision):
        repo_path = (root / path).relative_to(repo).as_posix()
        if revision != 'INDEX':
            if revision == 'HEAD':
                code, _ = self.run(repo, 'rev-parse', '--verify', 'HEAD', check=False)
                if code:
                    return ''  # Unborn branch.
            _, raw = self.run(repo, 'ls-tree', '-z', revision, '--', repo_path)
        else:
            _, raw = self.run(repo, 'ls-files', '--stage', '-z', '--', repo_path)
        if not raw:
            return ''
        records = [r for r in raw.split(b'\0') if r]
        if len(records) != 1:
            raise HTTPException(409, '文件存在合并冲突，第一版暂不提供冲突对比')
        metadata, found = records[0].split(b'\t', 1)
        if os.fsdecode(found) != repo_path:
            raise HTTPException(409, '文件状态已变化，请刷新')
        fields = metadata.split()
        if fields[0] not in (b'100644', b'100755'):
            raise HTTPException(415, '符号链接、子模块和特殊文件暂不支持差异对比')
        oid = fields[1] if revision == 'INDEX' else fields[2]
        _, size = self.run(repo, 'cat-file', '-s', oid.decode('ascii'))
        if int(size) > MAX_EDITOR_BYTES:
            raise HTTPException(413, '文件超过 2 MiB，暂不支持差异对比')
        _, raw = self.run(repo, 'cat-file', 'blob', oid.decode('ascii'))
        if len(raw) > MAX_EDITOR_BYTES:
            raise HTTPException(413, '文件超过 2 MiB，暂不支持差异对比')
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise HTTPException(415, '二进制或非 UTF-8 文件，暂不支持差异对比') from exc
        if any(ord(c) < 32 and c not in '\t\r\n\f' for c in text):
            raise HTTPException(415, '二进制文件，暂不支持差异对比')
        return text

    def commit_id(self, repo, value):
        if not re.fullmatch(r'(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})', value):
            raise HTTPException(400, '提交 ID 格式不正确，请从提交记录中选择')
        code, kind = self.run(repo, 'cat-file', '-t', value, check=False)
        if code or kind.strip() != b'commit':
            raise HTTPException(404, '提交已不存在或尚未下载到本地，请刷新记录')
        return value.lower()

    def history(self, workspace, head=None, offset=0, limit=50):
        if not 0 <= offset <= 1000000 or not 1 <= limit <= 100:
            raise HTTPException(400, '提交记录分页参数不正确')
        root, repo = self.repository(workspace)
        if repo is None:
            return dict(repository=False, commits=[], head=None, nextOffset=None)
        _, branch = self.run(root, 'symbolic-ref', '--quiet', '--short', 'HEAD', check=False)
        if head:
            head = self.commit_id(repo, head)
        else:
            code, raw = self.run(repo, 'rev-parse', '--verify', 'HEAD', check=False)
            if code:
                return dict(repository=True, branch=os.fsdecode(branch).strip(), commits=[], head=None, nextOffset=None)
            head = self.commit_id(repo, raw.decode('ascii').strip())
        # Anchor subsequent pages to this HEAD even if a new commit arrives meanwhile.
        scope = ['--', '.'] if root != repo else ['--']
        _, raw = self.run(root, 'log', '--full-history', '--date-order', '--no-decorate',
                          '--encoding=UTF-8', '-z', '--format=%H%x00%an%x00%aI%x00%s',
                          '--skip=' + str(offset), '-n', str(limit + 1), head, *scope)
        fields = raw.decode('utf-8', errors='replace').split('\0')[:-1]
        commits = [dict(id=fields[i], author=fields[i+1], date=fields[i+2], subject=fields[i+3])
                   for i in range(0, len(fields), 4)]
        return dict(repository=True, branch=os.fsdecode(branch).strip() or head[:8], head=head,
                    commits=commits[:limit], nextOffset=offset+limit if len(commits)>limit else None)

    def commit(self, workspace, commit):
        root, repo = self.repository(workspace)
        if repo is None:
            raise HTTPException(404, '当前工作区不是 Git 仓库')
        commit = self.commit_id(repo, commit)
        _, raw = self.run(repo, 'show', '-s', '--no-show-signature', '--encoding=UTF-8',
                          '--format=%an%x00%aI%x00%B', commit, '--')
        author, date, message = raw.decode('utf-8', errors='replace').split('\0', 2)
        # Read real parents: pretty-format %P hides missing parents in shallow clones.
        _, raw_commit = self.run(repo, 'cat-file', 'commit', commit)
        parents = [line[7:].decode('ascii') for line in raw_commit.split(b'\n\n', 1)[0].splitlines()
                   if line.startswith(b'parent ')]
        base = parents[0] if parents else None
        if base:
            self.commit_id(repo, base)
        revisions = [base, commit] if base else ['--root', commit]
        _, raw = self.run(root, 'diff-tree', '-r', '--no-commit-id', '--name-status', '-z',
                          '--no-ext-diff', '--no-textconv', '--find-renames', *revisions, '--', '.')
        records = iter(raw.split(b'\0')[:-1])
        entries = []
        for status in records:
            original = os.fsdecode(next(records))
            path = os.fsdecode(next(records)) if status[:1] in (b'R', b'C') else original
            path = self.relative(path, root, repo)
            try:
                original = self.relative(original, root, repo)
            except HTTPException:
                original = None
            if len(entries) >= 10000:
                raise HTTPException(413, '提交改动超过 10000 项，请选择更小的工作区')
            entries.append(dict(path=path, originalPath=original, status=status[:1].decode('ascii')))
        return dict(id=commit, author=author, date=date, message=message.rstrip('\n'),
                    parents=parents, base=base, entries=entries)

    def commit_diff(self, workspace, commit, path):
        detail = self.commit(workspace, commit)
        entry = next((e for e in detail['entries'] if e['path'] == path), None)
        if entry is None:
            raise HTTPException(404, '该文件不在本次提交的工作区改动中')
        if entry['originalPath'] is None:
            raise HTTPException(403, '重命名前的文件位于当前工作区之外，无法读取对比')
        root, repo = self.repository(workspace)
        original = '' if not detail['base'] or entry['status'] == 'A' else self.blob(root, repo, entry['originalPath'], detail['base'])
        modified = '' if entry['status'] == 'D' else self.blob(root, repo, path, detail['id'])
        return dict(**entry, original=original, modified=modified,
                    labels=['提交前 ' + detail['base'][:8] if detail['base'] else '首次提交前（空）',
                            '提交后 ' + detail['id'][:8]])

    def diff(self, workspace, path, group):
        root, repo = self.repository(workspace)
        if repo is None:
            raise HTTPException(404, '当前工作区不是 Git 仓库')
        if group not in ('staged', 'unstaged', 'untracked', 'conflicts'):
            raise HTTPException(400, '未知的改动分组')
        entry = next((e for e in self.changes(root, repo) if e['path'] == path and e['group'] == group), None)
        if entry is None:
            raise HTTPException(409, '该改动已不存在，请刷新列表')
        if group == 'conflicts':
            raise HTTPException(409, '文件存在合并冲突，第一版仅列出冲突文件，不提供冲突处理')
        if entry['originalPath'] is None:
            raise HTTPException(403, '重命名前的文件位于当前工作区之外，无法读取对比')
        if group == 'staged':
            original = self.blob(root, repo, entry['originalPath'], 'HEAD')
            modified = self.blob(root, repo, path, 'INDEX')
            labels = ['最近提交 HEAD', '暂存区']
        else:
            original = '' if group == 'untracked' else self.blob(root, repo, path, 'INDEX')
            modified = '' if entry['status'] == 'D' else self.files.read(root, path)['content']
            labels = ['空文件' if group == 'untracked' else '暂存区', '磁盘文件']
        return dict(**entry, original=original, modified=modified, labels=labels)
