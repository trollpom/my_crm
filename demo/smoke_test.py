"""Launch the existing isolated demo and check synthetic tasks and disabled integrations."""
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


def main():
    root = Path(__file__).resolve().parents[1]
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    with tempfile.TemporaryFile(mode='w+') as log:
        process = subprocess.Popen([sys.executable, str(root/'demo/run_demo.py'), '--port', str(port)], cwd=root, stdout=log, stderr=log)
        try:
            origin = f'http://127.0.0.1:{port}'
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError('Demo exited before startup')
                try:
                    with urllib.request.urlopen(origin+'/api/tasks', timeout=1) as response:
                        tasks = json.load(response)
                    break
                except (OSError, urllib.error.URLError):
                    time.sleep(.1)
            else:
                raise RuntimeError('Demo startup timeout')
            with urllib.request.urlopen(origin+'/api/ai/status', timeout=3) as response:
                config = json.load(response)
            with urllib.request.urlopen(origin+'/', timeout=3) as response:
                assert response.status == 200
            assert len(tasks) == 8, 'Expected eight synthetic tasks'
            with urllib.request.urlopen(origin+'/api/mail/counts', timeout=3) as response:
                mailboxes = json.load(response)
            assert all(mailboxes.get(name) == 0 for name in ['total','unread','starred','sent']), 'Demo must have zero mail records'
            ai = config
            assert not ai.get('enabled') and not ai.get('key_set') and not ai.get('api_key'), 'AI must be disabled'
            assert all('Синтетическая задача' in task.get('description', '') for task in tasks)
            print('PASS: demo starts; UI 200; 8 synthetic tasks; no mailboxes or AI key')
        except Exception:
            log.seek(0)
            print(log.read(), file=sys.stderr)
            raise
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == '__main__':
    main()
