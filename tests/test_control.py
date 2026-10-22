"""보정·명령 제한·오류 정리 및 LeRobot 실제 API 호환성 검증."""

from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from roboseasy_so102.config import ArmConfig, JOINT_NAMES, Settings
from roboseasy_so102.hardware import Arm
from roboseasy_so102.runtime import SimulatedArm, teleoperate
from roboseasy_so102.safety import limit_action, map_action, positions


def settings() -> Settings:
	"""테스트에 사용할 유효한 두 장치 설정."""
	return Settings(ArmConfig('/dev/leader', 'leader'),
		ArmConfig('/dev/follower', 'follower'), Path('/tmp/so102-tests'),
		fps=100, max_cycle_seconds=0.1)


def pose(value: float = 0.0) -> dict[str, float]:
	"""7개 관절 위치를 생성한다."""
	return {name: value for name in JOINT_NAMES}


class SafetyTest(unittest.TestCase):
	"""유효하지 않은 제어 입력을 거부하는지 검사한다."""

	def test_missing_joint(self) -> None:
		with self.assertRaises(ValueError):
			positions({'shoulder_pan': 0})

	def test_nonfinite(self) -> None:
		for value in (float('nan'), float('inf'), True, '1'):
			with self.subTest(value=value), self.assertRaises(ValueError):
				positions(pose(value))

	def test_relative_limit(self) -> None:
		bounds = {name: (-10, 10) for name in JOINT_NAMES}
		self.assertEqual(limit_action(pose(8), pose(), bounds, 2), pose(2))
		self.assertEqual(limit_action(pose(-8), pose(), bounds, 2), pose(-2))

	def test_out_of_range(self) -> None:
		bounds = {name: (-10, 10) for name in JOINT_NAMES}
		for target, present in ((pose(11), pose()), (pose(), pose(-11))):
			with self.assertRaises(ValueError):
				limit_action(target, present, bounds, 2)

	def test_mapping(self) -> None:
		config = settings()
		config.scale['wrist_yaw'] = -1
		config.offset['wrist_yaw'] = 3
		self.assertEqual(map_action(pose(1), config)['wrist_yaw'], 2)

	def test_port_alias(self) -> None:
		config = settings()
		config.follower.port = config.leader.port
		with self.assertRaises(ValueError):
			config.validate()

	def test_motor_id_collision(self) -> None:
		config = settings()
		config.leader.motor_ids['gripper'] = 1
		with self.assertRaises(ValueError):
			config.validate()

	def test_path_traversal_id(self) -> None:
		with self.assertRaises(ValueError):
			ArmConfig('/dev/tty', '../other').validate()

	def test_invalid_control_numbers(self) -> None:
		for value in (0, -1, float('nan'), True):
			with self.subTest(value=value), self.assertRaises(ValueError):
				replace(settings(), max_step=value).validate()


class RuntimeTest(unittest.TestCase):
	"""가상 장치로 제어와 실패 경로의 자원 정리를 검사한다."""

	def test_simulation(self) -> None:
		config = settings()
		leader = SimulatedArm(config.leader, 'leader')
		follower = SimulatedArm(config.follower, 'follower')
		self.assertGreater(teleoperate(config, leader, follower, .025), 0)
		self.assertFalse(leader.is_connected)
		self.assertFalse(follower.is_connected)

	def test_initial_difference(self) -> None:
		"""보조 루프도 초기 차이로 중단하지 않고 목표 변화량을 제한한다."""
		config = settings()
		leader = SimulatedArm(config.leader, 'leader')
		follower = SimulatedArm(config.follower, 'follower')
		follower._position['wrist_yaw'] = 40
		with patch.object(follower, 'write', wraps=follower.write) as write:
			self.assertGreater(teleoperate(config, leader, follower, .01), 0)
			goal = write.call_args_list[0].args[0]
			self.assertEqual(goal['wrist_yaw'], 38.0)
		self.assertFalse(follower.is_active)
		self.assertFalse(leader.is_connected)

	def test_partial_connection_failure(self) -> None:
		config = settings()
		leader = SimulatedArm(config.leader, 'leader')
		follower = SimulatedArm(config.follower, 'follower')
		with patch.object(follower, 'connect', side_effect=ConnectionError):
			with self.assertRaises(ConnectionError):
				teleoperate(config, leader, follower, .01)
		self.assertFalse(leader.is_connected)
		self.assertFalse(follower.is_connected)

	def test_invalid_live_input_no_write(self) -> None:
		config = settings()
		leader = SimulatedArm(config.leader, 'leader')
		follower = SimulatedArm(config.follower, 'follower')
		valid = {name: 50 if name == 'gripper' else 0 for name in JOINT_NAMES}
		with patch.object(leader, 'read',
			side_effect=[valid, valid, pose(float('nan'))]):
			with patch.object(follower, 'write') as write:
				with self.assertRaises(ValueError):
					teleoperate(config, leader, follower, .01)
				write.assert_not_called()
		self.assertFalse(follower.is_active)

	def test_stale_input_no_write(self) -> None:
		config = replace(settings(), max_cycle_seconds=.02)
		leader = SimulatedArm(config.leader, 'leader')
		follower = SimulatedArm(config.follower, 'follower')
		original = leader.read
		calls = 0

		def slow_read() -> dict[str, float]:
			nonlocal calls
			calls += 1
			if calls >= 3:
				time.sleep(.03)
			return original()

		with patch.object(leader, 'read', side_effect=slow_read):
			with patch.object(follower, 'write') as write:
				with self.assertRaises(TimeoutError):
					teleoperate(config, leader, follower, .1)
				write.assert_not_called()
		self.assertFalse(follower.is_connected)

	def test_write_failure_cleanup(self) -> None:
		config = settings()
		leader = SimulatedArm(config.leader, 'leader')
		follower = SimulatedArm(config.follower, 'follower')
		with patch.object(follower, 'write', side_effect=OSError):
			with self.assertRaises(OSError):
				teleoperate(config, leader, follower, .01)
		self.assertFalse(leader.is_active)
		self.assertFalse(follower.is_active)

	def test_cleanup_error_is_reported(self) -> None:
		config = settings()
		leader = SimulatedArm(config.leader, 'leader')
		follower = SimulatedArm(config.follower, 'follower')
		with patch.object(follower, 'close', side_effect=OSError):
			with self.assertLogs('roboseasy_so102.runtime', level='ERROR'):
				with self.assertRaises(RuntimeError):
					teleoperate(config, leader, follower, .01)
		self.assertFalse(leader.is_connected)

	def test_interrupt_cleanup(self) -> None:
		config = settings()
		leader = SimulatedArm(config.leader, 'leader')
		follower = SimulatedArm(config.follower, 'follower')
		with patch.object(follower, 'write', side_effect=KeyboardInterrupt):
			with self.assertRaises(KeyboardInterrupt):
				teleoperate(config, leader, follower, .01)
		self.assertFalse(leader.is_connected)
		self.assertFalse(follower.is_connected)


class FakeBus:
	"""LeRobot API를 따르며 쓰기 순서를 기록하는 모터 버스."""

	def __init__(self) -> None:
		self.is_connected = False
		self.calls = []
		self.calibration = {}
		self.raw = pose(2000)

	def connect(self) -> None:
		self.is_connected = True

	def disconnect(self, disable_torque: bool = True) -> None:
		self.calls.append(('disconnect', disable_torque))
		self.is_connected = False

	def sync_read(self, name: str, **kwargs: object) -> dict[str, float]:
		return self.raw.copy()

	def read_calibration(self) -> dict:
		return self.calibration.copy()

	def disable_torque(self) -> None:
		self.calls.append(('disable',))

	def configure_motors(self) -> None:
		self.calls.append(('configure',))

	def write(self, *args: object) -> None:
		self.calls.append(('write', *args))

	def sync_write(self, *args: object, **kwargs: object) -> None:
		self.calls.append(('goal', args, kwargs))

	def enable_torque(self) -> None:
		self.calls.append(('enable',))


@unittest.skipUnless(importlib.util.find_spec('lerobot'), 'robot extra 필요')
class HardwareTest(unittest.TestCase):
	"""실제 LeRobot 보정 타입과 대체 버스로 하드웨어 경계를 검사한다."""

	def setUp(self) -> None:
		from lerobot.motors import MotorCalibration

		self.directory = tempfile.TemporaryDirectory()
		self.addCleanup(self.directory.cleanup)
		self.bus = FakeBus()
		self.arm = Arm(ArmConfig('/dev/test', 'test'), 'follower',
			Path(self.directory.name), bus=self.bus)
		self.bus.calibration = {name: MotorCalibration(index, 0, 0, 1000, 3000)
			for index, name in enumerate(JOINT_NAMES, 1)}
		data = {'schema': 1, 'id': 'test', 'role': 'follower',
			'motor_ids': self.arm.config.motor_ids,
			'motors': {name: vars(item)
				for name, item in self.bus.calibration.items()}}
		self.arm.calibration_path.parent.mkdir(parents=True)
		self.arm.calibration_path.write_text(json.dumps(data))

	def test_readonly_diagnosis(self) -> None:
		self.arm.connect()
		self.arm.raw_positions()
		self.arm.close(disable_torque=False)
		self.assertEqual(self.bus.calls, [('disconnect', False)])

	def test_current_goal_before_enable(self) -> None:
		self.arm.connect()
		self.arm.load_calibration()
		self.arm.activate()
		names = [call[0] for call in self.bus.calls]
		self.assertLess(names.index('goal'), names.index('enable'))

	def test_calibration_mismatch(self) -> None:
		self.bus.calibration['wrist_yaw'].homing_offset = 42
		with self.assertRaises(ValueError):
			self.arm.load_calibration()
		self.assertFalse(self.arm.bounds)

	def test_raw_range_violation(self) -> None:
		self.arm.load_calibration()
		self.bus.raw['gripper'] = 3001
		with self.assertRaises(ValueError):
			self.arm.read()

	def test_units(self) -> None:
		self.arm.load_calibration()
		result = self.arm.read()
		self.assertEqual(result['wrist_yaw'], 0)
		self.assertEqual(result['gripper'], 50)

	def test_write_before_activation(self) -> None:
		with self.assertRaises(RuntimeError):
			self.arm.write(pose())

	def test_existing_calibration_not_overwritten(self) -> None:
		with self.assertRaises(FileExistsError):
			self.arm.calibrate()
		self.assertEqual(self.bus.calls, [])

	def test_factory(self) -> None:
		from lerobot.robots import make_robot_from_config
		from lerobot.teleoperators import make_teleoperator_from_config
		from roboseasy_so102 import SO102FollowerConfig, SO102LeaderConfig

		follower = make_robot_from_config(SO102FollowerConfig(port='/dev/test'))
		leader = make_teleoperator_from_config(
			SO102LeaderConfig(port='/dev/test'))
		self.assertEqual(len(follower.action_features), 7)
		self.assertIn('wrist_yaw.pos', leader.action_features)
		self.assertFalse(follower.is_connected)
		self.assertFalse(leader.is_connected)

	def test_calibration_save_and_restore(self) -> None:
		self.arm.calibration_path.unlink()
		self.bus.set_half_turn_homings = lambda: dict.fromkeys(JOINT_NAMES, 0)
		self.bus.record_ranges_of_motion = lambda names: (
			{name: 1000 for name in names}, {name: 3000 for name in names})
		self.bus.write_calibration = lambda calibration: setattr(
			self.bus, 'calibration', calibration)
		with patch('builtins.input', return_value=''):
			self.arm.calibrate()
		self.arm.load_calibration()
		self.assertEqual(len(self.arm.bounds), 7)
		self.assertFalse(list(self.arm.calibration_path.parent.glob('*.tmp')))

	def test_no_automatic_full_turn(self) -> None:
		self.arm.calibration_path.unlink()
		self.bus.set_half_turn_homings = lambda: dict.fromkeys(JOINT_NAMES, 0)
		def ranges(names: list[str]) -> tuple[dict, dict]:
			self.assertIn('wrist_roll', names)
			return ({name: 1000 for name in names},
				{name: 1000 for name in names})
		self.bus.record_ranges_of_motion = ranges
		with patch('builtins.input', return_value=''):
			with self.assertRaises(ValueError):
				self.arm.calibrate()
		self.assertFalse(self.arm.calibration_path.exists())

	def test_close_falls_back_after_torque_error(self) -> None:
		from unittest.mock import Mock

		self.bus.is_connected = True
		self.bus.disconnect = Mock(side_effect=OSError('lost'))
		self.bus.port_handler = Mock()
		with self.assertLogs('roboseasy_so102.hardware', level='ERROR'):
			with self.assertRaises(RuntimeError):
				self.arm.close()
		self.bus.port_handler.closePort.assert_called_once()
