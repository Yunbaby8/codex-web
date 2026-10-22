"""Session-bound uploads stored separately from source workspaces."""
import json
import re
import shutil
import time
import uuid
from pathlib import Path

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_ATTACHMENTS = 8

class Attachments:
    def __init__(self, data_dir):
        self.root = data_dir / 'uploads'
        self.root.mkdir(mode=0o700, exist_ok=True)

    async def upload(self, sid, name, chunks):
        name = name.replace('\\', '/').split('/')[-1]
        name = re.sub(r'[\x00-\x1f\x7f]', '', name).strip()[:160]
        if not name or name in ('.', '..'):
            raise ValueError('文件名无效')
        aid = uuid.uuid4().hex
        folder = self.root / aid
        folder.mkdir(mode=0o700)
        suffix = re.sub(r'[^\w.-]', '_', Path(name).suffix[:16]).rstrip('.')
        path = folder / ('file' + suffix)
        size = 0
        try:
            with path.open('xb') as target:
                async for chunk in chunks:
                    size += len(chunk)
                    if size > MAX_FILE_BYTES:
                        raise ValueError('单个附件不能超过 20 MB')
                    target.write(chunk)
            with path.open('rb') as source:
                head = source.read(16)
            image = (head.startswith(b'\x89PNG\r\n\x1a\n') or head.startswith(b'\xff\xd8\xff') or
                     (head.startswith(b'RIFF') and head[8:12] == b'WEBP') or head.startswith((b'GIF87a', b'GIF89a')))
            meta = {'id':aid, 'session':sid, 'name':name, 'size':size, 'image':image, 'file':path.name,
                    'uploaded_at':time.time()}
            (folder/'metadata.json').write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
            return {k:meta[k] for k in ('id','name','size','image')}
        except BaseException:
            shutil.rmtree(folder)
            raise

    def get(self, sid, aid):
        if not re.fullmatch(r'[0-9a-f]{32}', aid):
            raise ValueError('附件 ID 无效')
        folder = self.root / aid
        meta = json.loads((folder/'metadata.json').read_text(encoding='utf-8'))
        if meta['session'] != sid:
            raise ValueError('附件不属于当前会话')
        return meta, folder / meta['file']

    def inputs(self, sid, ids):
        if len(ids) > MAX_ATTACHMENTS or len(set(ids)) != len(ids):
            raise ValueError('每条消息最多 8 个附件，不能重复')
        inputs = []
        for aid in ids:
            meta, path = self.get(sid, aid)
            inputs.append({'type':'text','text':f"用户上传的附件：{json.dumps(meta['name'],ensure_ascii=False)}\n服务器文件路径：{json.dumps(str(path),ensure_ascii=False)}"})
            if meta['image']:
                inputs.append({'type':'localImage','path':str(path)})
        return inputs

    def for_session(self, sid):
        result = []
        for folder in self.root.iterdir():
            metadata = folder / 'metadata.json'
            if not metadata.is_file():
                continue
            meta = json.loads(metadata.read_text(encoding='utf-8'))
            if meta.get('session') != sid:
                continue
            path = folder / meta['file']
            if not path.is_file():
                continue
            result.append({
                'id': meta['id'], 'name': meta['name'], 'size': meta['size'],
                'image': meta['image'], 'path': str(path),
                'uploadedAt': meta.get('uploaded_at', path.stat().st_mtime),
            })
        return sorted(result, key=lambda item: item['name'])

    def delete_session(self, sid):
        for folder in self.root.iterdir():
            metadata = folder / 'metadata.json'
            if metadata.is_file() and json.loads(metadata.read_text(encoding='utf-8')).get('session') == sid:
                shutil.rmtree(folder)

    def delete_images(self, sid, paths):
        for value in paths:
            path = Path(value)
            if path.parent.parent != self.root:
                continue
            try:
                meta, actual = self.get(sid, path.parent.name)
            except FileNotFoundError:
                continue
            if meta['image'] and actual == path:
                shutil.rmtree(path.parent)
