"""장비 없이 고객 명령행의 정상·오류 경로를 검증한다."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class CliTest(unittest.TestCase):
	"""깨끗한 작업 디렉터리에서 설정 생성과 실행을 검사한다."""

	def invoke(self, directory: str, *args: str) -> subprocess.CompletedProcess:
		"""현재 소스의 CLI를 별도 프로세스에서 실행한다."""
		environment = os.environ.copy()
		source = Path(__file__).resolve().parents[1] / 'src'
		environment['PYTHONPATH'] = str(source)
		return subprocess.run(
			[sys.executable, '-m', 'roboseasy_so102.cli', *args],
			cwd=directory, env=environment, capture_output=True, text=True,
			timeout=15)

	def test_init_check_simulate(self) -> None:
		with tempfile.TemporaryDirectory() as directory:
			for args in (('init',), ('check',),
				('teleoperate', '--simulate', '--duration', '.05')):
				result = self.invoke(directory, *args)
				self.assertEqual(result.returncode, 0, result.stderr)

	def test_existing_config_preserved(self) -> None:
		with tempfile.TemporaryDirectory() as directory:
			self.invoke(directory, 'init')
			path = Path(directory) / 'config/so102.json'
			original = path.read_bytes()
			result = self.invoke(directory, 'init')
			self.assertNotEqual(result.returncode, 0)
			self.assertEqual(path.read_bytes(), original)

	def test_execute_flag_required(self) -> None:
		with tempfile.TemporaryDirectory() as directory:
			self.invoke(directory, 'init')
			for args in (('teleoperate',), ('calibrate', '--role', 'leader'),
				('setup-motor', '--role', 'follower', '--joint', 'gripper')):
				result = self.invoke(directory, *args)
				self.assertNotEqual(result.returncode, 0)
				self.assertIn('--execute', result.stderr)
				self.assertNotIn('Traceback', result.stderr)
