"""Server-only configuration; credentials never enter the public config response."""
import os
from pathlib import Path
import yaml

BASE = Path(__file__).resolve().parent.parent

def load_config():
    path = Path(os.environ.get('CODEX_WEB_CONFIG', BASE / 'config.yaml'))
    data = yaml.safe_load(path.read_text())
    if not data.get('username') or not data.get('password'):
        raise RuntimeError('config.yaml 必须设置用户名和非空密码')
    data.setdefault('host', '0.0.0.0')
    data.setdefault('port', 5200)
    data.setdefault('codex_binary', 'codex')
    data.setdefault('auth_file', str(Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')) / 'auth.json'))
    data.setdefault('auth_poll_seconds', 1)
    data.setdefault('sandbox', 'workspace-write')
    data.setdefault('models', [])
    data.setdefault('allowed_origins', ['http://127.0.0.1:5200', 'http://localhost:5200'])
    data.setdefault('cookie_secure', False)
    data['data_dir'] = Path(data.get('data_dir', BASE / 'data')).expanduser().resolve()
    data['auth_file'] = Path(data['auth_file']).expanduser().resolve()
    return data
