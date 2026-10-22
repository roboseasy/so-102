"""배포 리소스 및 잘못된 모델 검증."""

from pathlib import Path
import tempfile
import unittest

from roboseasy_so102.cli import inspect_model, model_path


class ModelTest(unittest.TestCase):
	"""실제 URDF와 손상된 입력을 검사한다."""

	def test_release_model(self) -> None:
		"""배포 모델의 모든 메시와 추가 관절을 확인한다."""
		joints = inspect_model(model_path())
		self.assertEqual(len(joints), 7)
		self.assertEqual(joints[4][0], 'wrist_yaw')

	def test_dual_model_rejected(self) -> None:
		"""잘못 지정한 양팔 모델을 거부한다."""
		with tempfile.TemporaryDirectory() as directory:
			path = Path(directory) / 'wrong.urdf'
			path.write_text('<robot name="wrong"/>')
			with self.assertRaises(ValueError):
				inspect_model(path)
