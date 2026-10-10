"""Durable Web index/event journal. Codex owns actual conversation context."""
import json
import sqlite3
import time

class SessionStore:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, cwd TEXT NOT NULL, model TEXT NOT NULL,
                effort TEXT NOT NULL, title TEXT NOT NULL, updated REAL NOT NULL,
                created REAL NOT NULL, archived INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, session TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS events_session ON events(session, seq);
        ''')
        columns = {row['name'] for row in self.db.execute('PRAGMA table_info(sessions)')}
        if 'archived' not in columns:
            self.db.execute('ALTER TABLE sessions ADD COLUMN archived INTEGER NOT NULL DEFAULT 0')
        if 'created' not in columns:
            self.db.execute('ALTER TABLE sessions ADD COLUMN created REAL')
            self.db.execute('UPDATE sessions SET created=updated WHERE created IS NULL')
        if 'title_set' not in columns:
            self.db.execute('ALTER TABLE sessions ADD COLUMN title_set INTEGER NOT NULL DEFAULT 0')
            self.db.execute("UPDATE sessions SET title_set=1 WHERE title NOT IN ('New Session','Imported Session','')")
        if not {'archived', 'created', 'title_set'} <= columns:
            self.db.commit()

    def save(self, sid, cwd, model, effort, title='New Session'):
        now = time.time()
        existing = self.db.execute('SELECT created,title,title_set FROM sessions WHERE id=?', (sid,)).fetchone()
        title_set = int(bool(title.strip()) and title not in ('New Session', 'Imported Session'))
        if existing is None:
            self.db.execute('INSERT INTO sessions (id,cwd,model,effort,title,updated,created,archived,title_set) VALUES (?,?,?,?,?,?,?,0,?)', (sid,cwd,model,effort,title,now,now,title_set))
        else:
            created = existing['created'] or now
            if existing['title_set']:
                title, title_set = existing['title'], 1
            self.db.execute('UPDATE sessions SET cwd=?,model=?,effort=?,title=?,updated=?,created=?,archived=0,title_set=? WHERE id=?', (cwd,model,effort,title,now,created,title_set,sid))
        self.db.commit()

    def rename(self, sid, title):
        self.get(sid)
        title = title.strip()
        if not title or len(title) > 120:
            raise ValueError('会话标题需要 1 到 120 个字符')
        with self.db:
            self.db.execute('UPDATE sessions SET title=?,title_set=1 WHERE id=?', (title, sid))

    def get(self, sid):
        row = self.db.execute('SELECT * FROM sessions WHERE id=?', (sid,)).fetchone()
        if row is None:
            raise KeyError('会话不存在')
        return dict(row)

    def list(self, archived=False):
        return [dict(r) for r in self.db.execute('SELECT * FROM sessions WHERE archived=? ORDER BY updated DESC', (int(archived),))]

    def archive(self, sid):
        self.db.execute('UPDATE sessions SET archived=1 WHERE id=?', (sid,))
        self.db.commit()

    def delete(self, sid):
        with self.db:
            self.db.execute('DELETE FROM events WHERE session=?', (sid,))
            self.db.execute('DELETE FROM sessions WHERE id=?', (sid,))

    def append(self, sid, event):
        self.db.execute('INSERT INTO events(session,payload) VALUES (?,?)', (sid,json.dumps(event,ensure_ascii=False)))
        self.db.commit()

    def events(self, sid, after=0):
        return [{'seq':r['seq'], **json.loads(r['payload'])} for r in self.db.execute('SELECT seq,payload FROM events WHERE session=? AND seq>? ORDER BY seq LIMIT 500', (sid,after))]

    def latest_seq(self, sid):
        row = self.db.execute('SELECT COALESCE(MAX(seq),0) AS seq FROM events WHERE session=?', (sid,)).fetchone()
        return row['seq']

    def resolved_requests(self, sid):
        return {row[0] for row in self.db.execute(
            "SELECT json_extract(payload,'$.params.id') FROM events WHERE session=? "
            "AND json_extract(payload,'$.method')='web/requestResolved'", (sid,))}

    def latest_event(self, sid, method):
        row = self.db.execute(
            "SELECT seq,payload FROM events WHERE session=? AND json_extract(payload,'$.method')=? ORDER BY seq DESC LIMIT 1",
            (sid, method),
        ).fetchone()
        if row is None:
            return None
        return {'seq':row['seq'], **json.loads(row['payload'])}

    def snapshot_events(self, sid):
        snapshot = []
        plans = {}
        for row in self.db.execute('SELECT seq,payload FROM events WHERE session=? ORDER BY seq', (sid,)):
            event = {'seq':row['seq'], **json.loads(row['payload'])}
            if event.get('method') == 'turn/plan/updated':
                params = event.get('params', {})
                plans[params.get('turnId')] = params
            if event.get('method') == 'web/history':
                for turn in event.get('params', {}).get('turns', []):
                    if turn['id'] in plans:
                        turn['webPlan'] = plans[turn['id']]
                snapshot = [event]
            else:
                snapshot.append(event)
        return snapshot

    def turn_plans(self, sid, cursor):
        """Plans are notifications, so thread/read need not include them."""
        plans = {}
        for row in self.db.execute(
            "SELECT payload FROM events WHERE session=? AND seq<=? "
            "AND json_extract(payload,'$.method')='turn/plan/updated' ORDER BY seq",
            (sid, cursor),
        ):
            params = json.loads(row['payload']).get('params', {})
            if params.get('turnId'):
                plans[params['turnId']] = params
        return plans
