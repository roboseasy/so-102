"""공식 LeRobot CLI 자동 발견·보정·실행 루프의 통합 검증."""

from collections.abc import Iterator
from contextlib import contextmanager, redirect_stdout
from dataclasses import asdict
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

NAMES = (
	'shoulder_pan', 'shoulder_lift', 'elbow_flex', 'wrist_flex',
	'wrist_yaw', 'wrist_roll', 'gripper',
)


class NativeBus:
	"""LeRobot 모터 타입을 사용하되 시리얼을 열지 않는 통합 시험 버스."""

	def __init__(self, config: object, calibration: dict) -> None:
		from lerobot.motors import Motor, MotorNormMode

		body_mode = (MotorNormMode.DEGREES if config.use_degrees
			else MotorNormMode.RANGE_M100_100)
		self.motors = {name: Motor(index, 'sts3215',
			MotorNormMode.RANGE_0_100 if name == 'gripper' else body_mode)
			for index, name in enumerate(NAMES, 1)}
		self.calibration = calibration
		self.is_connected = False
		self.calls = []
		self.port = config.port
		self._values = {name: 50.0 if name == 'gripper' else 0.0
			for name in NAMES}
		if 'leader' in self.port:
			self._values['wrist_yaw'] = 1.0

	@property
	def is_calibrated(self) -> bool:
		"""전체 관절 보정값의 유무."""
		return set(self.calibration) == set(NAMES)

	def connect(self) -> None:
		"""가상 연결."""
		self.is_connected = True

	def disconnect(self, disable_torque: bool = True) -> None:
		"""종료 정책 기록."""
		self.calls.append(('disconnect', disable_torque))
		self.is_connected = False

	def configure_motors(self) -> None:
		"""모터 설정 기록."""
		self.calls.append(('configure',))

	def disable_torque(self) -> None:
		"""토크 해제 기록."""
		self.calls.append(('disable',))

	def enable_torque(self) -> None:
		"""토크 활성화 기록."""
		self.calls.append(('enable',))

	@contextmanager
	def torque_disabled(self) -> Iterator[None]:
		"""공식 모터 설정의 토크 컨텍스트를 제공한다."""
		self.disable_torque()
		try:
			yield
		finally:
			self.enable_torque()

	def write(self, *args: object) -> None:
		"""레지스터 쓰기 기록."""
		self.calls.append(('write', args))

	def sync_read(self, name: str, normalize: bool = True) -> dict:
		"""정규화 또는 원시 위치 반환."""
		if normalize:
			return self._values.copy()
		return dict.fromkeys(NAMES, 2000)

	def sync_write(self, name: str, values: dict,
		normalize: bool = True) -> None:
		"""전체 관절 명령 기록."""
		self.calls.append(('goal', values.copy(), normalize))
		if normalize:
			self._values.update(values)

	def set_half_turn_homings(self) -> dict:
		"""모든 관절의 가상 보정 오프셋."""
		return dict.fromkeys(NAMES, 0)

	def record_ranges_of_motion(self, names: list[str]) -> tuple[dict, dict]:
		"""공식 보정처럼 손목 회전을 제외한 6축의 범위를 측정한다."""
		assert set(names) == set(NAMES) - {'wrist_roll'}
		return dict.fromkeys(names, 1000), dict.fromkeys(names, 3000)

	def write_calibration(self, calibration: dict) -> None:
		"""가상 모터 보정값 갱신."""
		self.calibration = calibration


@unittest.skipUnless(importlib.util.find_spec('lerobot'), 'LeRobot 설치 필요')
class NativePluginTest(unittest.TestCase):
	"""사용자가 제시한 명령을 공식 main과 파서로 검증한다."""

	def setUp(self) -> None:
		self.directory = tempfile.TemporaryDirectory()
		self.addCleanup(self.directory.cleanup)
		self.root = Path(self.directory.name)
		self.buses = []

	def bus(self, config: object, calibration: dict) -> NativeBus:
		"""장치별 대체 버스 생성."""
		bus = NativeBus(config, calibration)
		self.buses.append(bus)
		return bus

	def calibration(self, role: str) -> Path:
		"""LeRobot 표준 JSON 보정 파일을 생성한다."""
		path = self.root / role
		path.mkdir()
		data = {name: {'id': index, 'drive_mode': 0, 'homing_offset': 0,
			'range_min': 1000, 'range_max': 3000}
			for index, name in enumerate(NAMES, 1)}
		(path / f'so102_{role}.json').write_text(json.dumps(data))
		return path

	def test_fresh_process_discovery(self) -> None:
		"""사용자 코드의 수동 import 없이 배포 메타데이터로 발견한다."""
		script = '''
from lerobot.utils.import_utils import register_third_party_plugins
from lerobot.robots import RobotConfig, make_robot_from_config
from lerobot.teleoperators import TeleoperatorConfig
register_third_party_plugins()
config = RobotConfig.get_choice_class('so102_follower')(port='/dev/missing')
robot = make_robot_from_config(config)
assert robot.bus.motors['wrist_yaw'].id == 5
assert robot.bus.motors['gripper'].id == 7
assert TeleoperatorConfig.get_choice_class('so102_leader')
assert not robot.is_connected
print('AUTO_DISCOVERY_OK')
'''
		result = subprocess.run([sys.executable, '-c', script],
			cwd=self.root, capture_output=True, text=True, timeout=30)
		self.assertEqual(result.returncode, 0, result.stderr)
		self.assertIn('AUTO_DISCOVERY_OK', result.stdout)

	def test_official_help(self) -> None:
		"""공식 teleoperate·calibrate 도움말이 SO-102 타입을 인식한다."""
		for module, role in (('lerobot_teleoperate', 'robot'),
			('lerobot_calibrate', 'robot')):
			result = subprocess.run([sys.executable, '-m',
				f'lerobot.scripts.{module}',
				f'--{role}.type=so102_follower', '--help'],
				cwd=self.root, capture_output=True, text=True, timeout=30)
			self.assertEqual(result.returncode, 0, result.stderr)
			self.assertIn('so102_follower', result.stdout)

	def test_user_command_native_loop(self) -> None:
		"""공식 CLI의 기본 추종과 명시한 상대 목표 제한을 검증한다."""
		from lerobot.scripts import lerobot_teleoperate

		follower = self.calibration('follower')
		leader = self.calibration('leader')
		args = ['lerobot-teleoperate',
			'--robot.type=so102_follower',
			'--robot.port=/dev/so102_follower',
			'--robot.id=so102_follower',
			'--teleop.type=so102_leader',
			'--teleop.port=/dev/so102_leader',
			'--teleop.id=so102_leader', '--display_data=false',
			f'--robot.calibration_dir={follower}',
			f'--teleop.calibration_dir={leader}', '--teleop_time_s=0.02']
		for leader_pan, follower_pan, expected_pan, options in (
			(0.0, 0.0, 0.0, []),
			(-7.69, 7.82, -7.69, []),
			(7.82, -7.69, 7.82, []),
			(-7.69, 7.82, 5.82, ['--robot.max_relative_target=2.0']),
		):
			with self.subTest(leader_pan=leader_pan,
				follower_pan=follower_pan, options=options):
				self.buses.clear()

				def make_startup_bus(config: object,
					calibration: dict) -> NativeBus:
					"""사용자 로그의 초기 관절 위치를 재현한다."""
					bus = self.bus(config, calibration)
					bus._values['shoulder_pan'] = (leader_pan
						if 'leader' in config.port else follower_pan)
					return bus

				with patch('sys.argv', args + options), redirect_stdout(
					io.StringIO()):
					with patch('roboseasy_so102.lerobot_devices.make_native_bus',
						make_startup_bus):
						lerobot_teleoperate.main()
				self.assertEqual(len(self.buses), 2)
				follower_bus = next(b for b in self.buses
					if 'follower' in b.port)
				goals = [c[1] for c in follower_bus.calls
					if c[0] == 'goal' and c[2]]
				self.assertTrue(goals)
				self.assertEqual(set(goals[0]), set(NAMES))
				self.assertAlmostEqual(goals[0]['shoulder_pan'], expected_pan)
				self.assertEqual(goals[0]['wrist_yaw'], 1.0)
				previous_pan = follower_pan
				for goal in goals:
					if options:
						self.assertLessEqual(
							abs(goal['shoulder_pan'] - previous_pan), 2.0 + 1e-9)
					else:
						self.assertEqual(goal['shoulder_pan'], leader_pan)
					previous_pan = goal['shoulder_pan']
				self.assertTrue(all(not bus.is_connected for bus in self.buses))

	def test_native_calibrate_both_roles(self) -> None:
		"""공식 보정 명령으로 로봇·리더 모두 표준 JSON을 저장한다."""
		from lerobot.scripts import lerobot_calibrate

		for role, option in (('follower', 'robot'), ('leader', 'teleop')):
			with self.subTest(role=role):
				directory = self.root / role
				args = ['lerobot-calibrate',
					f'--{option}.type=so102_{role}',
					f'--{option}.port=/dev/so102_{role}',
					f'--{option}.id=so102_{role}',
					f'--{option}.calibration_dir={directory}']
				with patch('sys.argv', args), patch('builtins.input',
					return_value=''):
					with patch(
						'roboseasy_so102.lerobot_devices.make_native_bus',
						self.bus), redirect_stdout(io.StringIO()):
						lerobot_calibrate.main()
				saved = directory / f'so102_{role}.json'
				data = json.loads(saved.read_text())
				self.assertEqual(set(data), set(NAMES))
				self.assertEqual(data['wrist_yaw']['id'], 5)
				self.assertEqual(data['wrist_yaw']['range_min'], 1000)
				self.assertEqual(data['wrist_yaw']['range_max'], 3000)
				self.assertEqual(data['wrist_roll']['range_min'], 0)
				self.assertEqual(data['wrist_roll']['range_max'], 4095)
				self.assertFalse(self.buses[-1].is_connected)

	def test_official_config_defaults(self) -> None:
		"""공식 설정의 필드와 기본값을 추가 옵션 없이 그대로 사용한다."""
		from lerobot.robots.so_follower.config_so_follower import (
			SOFollowerRobotConfig,
		)
		from lerobot.teleoperators.so_leader.config_so_leader import (
			SOLeaderTeleopConfig,
		)
		from roboseasy_so102 import SO102FollowerConfig, SO102LeaderConfig

		for config_class, official_class in (
			(SO102FollowerConfig, SOFollowerRobotConfig),
			(SO102LeaderConfig, SOLeaderTeleopConfig),
		):
			with self.subTest(config_class=config_class):
				self.assertEqual(asdict(config_class(port='/dev/missing')),
					asdict(official_class(port='/dev/missing')))

	def test_relative_limits_and_units(self) -> None:
		"""공식 구현으로 7축 모두에 지정한 상대 제한과 정규화를 적용한다."""
		from roboseasy_so102 import SO102Follower, SO102FollowerConfig

		limits = {name: index / 10 for index, name in enumerate(NAMES, 1)}
		config = SO102FollowerConfig(port='/dev/follower', id='so102_follower',
			use_degrees=False, calibration_dir=self.calibration('follower'),
			max_relative_target=limits)
		with patch('roboseasy_so102.lerobot_devices.make_native_bus', self.bus):
			robot = SO102Follower(config)
			try:
				robot.connect()
				bus = self.buses[-1]
				self.assertEqual(bus.motors['wrist_yaw'].norm_mode.value,
					'range_m100_100')
				self.assertEqual(bus.motors['gripper'].norm_mode.value,
					'range_0_100')
				present = bus._values.copy()
				action = {f'{name}.pos': value + 5.0
					for name, value in present.items()}
				result = robot.send_action(action)
				self.assertEqual(set(result), {f'{name}.pos' for name in NAMES})
				for name in NAMES:
					self.assertAlmostEqual(result[f'{name}.pos'],
						present[name] + limits[name])
					self.assertEqual(bus.calls[-1][1][name], result[f'{name}.pos'])
			finally:
				robot.disconnect()
