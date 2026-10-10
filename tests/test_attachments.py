import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from backend.attachments import Attachments, MAX_FILE_BYTES
from backend.codex_manager import CodexManager
from backend.session_manager import SessionStore

async def chunks(data):
    yield data

class AttachmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_session_containment_and_native_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            a=Attachments(Path(temp))
            image=await a.upload('a','../../image.png',chunks(b'\x89PNG\r\n\x1a\nfixture'))
            document=await a.upload('a','report.txt',chunks(b'FILE_TOKEN'))
            self.assertEqual(image['name'],'image.png')
            listed=a.for_session('a')
            self.assertEqual([item['name'] for item in listed],['image.png','report.txt'])
            self.assertTrue(Path(listed[0]['path']).is_file())
            result=a.inputs('a',[image['id'],document['id']])
            self.assertEqual([item['type'] for item in result],['text','localImage','text'])
            self.assertTrue(Path(result[1]['path']).is_relative_to(Path(temp)))
            with self.assertRaises(ValueError):a.inputs('b',[image['id']])
            with self.assertRaises(ValueError):a.get('a','../../etc/passwd')
            with self.assertRaises(ValueError):a.inputs('a',[image['id']]*9)
            self.assertEqual(Attachments(Path(temp)).get('a',document['id'])[1].read_bytes(),b'FILE_TOKEN')
            other=await a.upload('b','other.txt',chunks(b'OTHER'))
            a.delete_session('a')
            self.assertEqual([folder.name for folder in a.root.iterdir()],[other['id']])

    async def test_oversize_cleanup_and_empty_file(self):
        with tempfile.TemporaryDirectory() as temp:
            a=Attachments(Path(temp))
            async def oversized():
                for _ in range(21):yield b'x'*(1024*1024)
            with self.assertRaises(ValueError):await a.upload('a','big.txt',oversized())
            self.assertEqual(list(a.root.iterdir()),[])
            empty=await a.upload('a','empty.txt',chunks(b''))
            self.assertEqual(empty['size'],0)

    async def test_turn_cleanup_preserves_documents_drafts_and_other_sessions(self):
        for status in ('completed', 'failed', 'interrupted'):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temp:
                a = Attachments(Path(temp))
                store = SessionStore(Path(temp)/'db')
                store.save('a', temp, 'm', 'medium')
                manager = CodexManager({}, store, a)
                manager.models = [{'id':'m', 'efforts':['medium']}]
                manager.loaded.add('a')
                async def rpc(method, params):
                    return {'turn':{'id':'turn1'}}
                manager.client.rpc = rpc
                first = await a.upload('a', 'first.png', chunks(b'\x89PNG\r\n\x1a\nfixture'))
                steer = await a.upload('a', 'steer.png', chunks(b'\x89PNG\r\n\x1a\nfixture'))
                draft = await a.upload('a', 'draft.png', chunks(b'\x89PNG\r\n\x1a\nfixture'))
                other = await a.upload('b', 'other.png', chunks(b'\x89PNG\r\n\x1a\nfixture'))
                document = await a.upload('a', 'report.txt', chunks(b'report'))
                await manager.send('a', 'look', 'm', 'medium', attachments=a.inputs('a', [first['id'], document['id']]))
                await manager.steer('a', 'also look', a.inputs('a', [steer['id']]))
                self.assertTrue(a.get('a', first['id'])[1].is_file())
                self.assertTrue(a.get('a', steer['id'])[1].is_file())
                reader = asyncio.StreamReader()
                manager.client.proc = SimpleNamespace(stdout=reader)
                task = asyncio.create_task(manager.client.read())
                reader.feed_data((json.dumps({'method':'turn/completed', 'params':{
                    'threadId':'a', 'turn':{'id':'turn1', 'status':status}}})+'\n').encode())
                # Keep the connection open: cleanup must happen on completion, not EOF.
                await asyncio.sleep(0)
                self.assertNotIn('a', manager.active)
                self.assertEqual({p.name for p in a.root.iterdir()}, {draft['id'], other['id'], document['id']})
                reader.feed_eof()
                await task
                store.db.close()

    async def test_connection_exit_cleans_used_images(self):
        with tempfile.TemporaryDirectory() as temp:
            a = Attachments(Path(temp))
            uploaded = await a.upload('a', 'image.png', chunks(b'\x89PNG\r\n\x1a\nfixture'))
            store = SessionStore(Path(temp)/'db')
            manager = CodexManager({}, store, a)
            manager.active['a'] = 'turn1'
            manager.turn_images['a'] = {str(a.get('a', uploaded['id'])[1])}
            reader = asyncio.StreamReader()
            reader.feed_eof()
            manager.client.proc = SimpleNamespace(stdout=reader)
            await manager.client.read()
            self.assertEqual(list(a.root.iterdir()), [])
            store.db.close()

if __name__=='__main__':unittest.main()
