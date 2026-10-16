"""Both local services must stop when either process exits."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ServiceLauncherTests(unittest.TestCase):
    def test_failed_cad_process_does_not_leave_web_process_running(self):
        with tempfile.TemporaryDirectory(prefix='l-launcher-') as directory:
            folder = Path(directory)
            shim = f'''#!{sys.executable}
import os, pathlib, sys, time
name = pathlib.Path(sys.argv[0]).name
pathlib.Path(os.environ['L_TEST_PIDS'], name + '.pid').write_text(str(os.getpid()))
if name == 'python3':
    time.sleep(0.3)
    sys.exit(7)
time.sleep(30)
'''
            for name in ('python3', 'node_modules/.bin/vinext'):
                path = folder / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(shim)
                path.chmod(0o700)
            environment = dict(os.environ, PATH=str(folder) + os.pathsep + os.environ['PATH'],
                               L_TEST_PIDS=str(folder))
            process = subprocess.Popen(['/bin/bash', str(ROOT / 'scripts/start-render.sh')],
                                       cwd=folder, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                process.communicate(timeout=5)
                self.assertEqual(process.returncode, 7)
            finally:
                # Also clean up if the regression leaves a child holding the output pipe.
                for path in folder.glob('*.pid'):
                    try:
                        os.kill(int(path.read_text()), signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                if process.poll() is None:
                    process.kill()
                process.communicate()


if __name__ == '__main__':
    unittest.main()
