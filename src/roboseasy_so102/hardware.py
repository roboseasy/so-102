"""LeRobot v0.6.0 Feetech 버스 기반 SO-102 연결·보정·제어."""

from dataclasses import asdict
from importlib.metadata import version
import json
import logging
import os
from pathlib import Path
import tempfile
from typing import Any

from .config import ArmConfig, JOINT_NAMES
from .safety import positions

LOGGER = logging.getLogger(__name__)


def require_lerobot() -> None:
	"""정확한 LeRobot 버전을 요구한다."""
	if version('lerobot') != '0.6.0':
		raise RuntimeError('LeRobot v0.6.0이 필요합니다: pip install ".[robot]"')


def make_bus(config: ArmConfig) -> Any:
	"""검증한 모터 구성으로 LeRobot Feetech 버스를 생성한다."""
	require_lerobot()
	from lerobot.motors import Motor, MotorNormMode
	from lerobot.motors.feetech import FeetechMotorsBus

	config.validate()
	motors = {name: Motor(config.motor_ids[name], 'sts3215',
		MotorNormMode.RANGE_0_100 if name == 'gripper'
		else MotorNormMode.DEGREES) for name in JOINT_NAMES}
	return FeetechMotorsBus(port=config.port, motors=motors)


class Arm:
	"""읽기 연결과 구동 활성화를 구분하는 단일 SO-102 장치.

	Attributes:
		bus: LeRobot Feetech 버스 또는 테스트용 대체 버스.
		bounds: 보정 좌표에서의 관절별 범위.
	"""

	def __init__(self, config: ArmConfig, role: str,
		calibration_dir: Path, bus: Any = None) -> None:
		if role not in ('leader', 'follower'):
			raise ValueError('role은 leader 또는 follower여야 합니다.')
		config.validate()
		self.config = config
		self.role = role
		self.bus = make_bus(config) if bus is None else bus
		self.calibration_path = calibration_dir / role / f'{config.id}.json'
		self.bounds: dict[str, tuple[float, float]] = {}
		self.is_active = False
		self._calibration: dict[str, Any] = {}

	def connect(self) -> None:
		"""포트를 열고 예상 모터의 응답을 검사한다. 쓰기를 수행하지 않는다."""
		try:
			self.bus.connect()
		except BaseException:
			self.close(disable_torque=False)
			raise

	def raw_positions(self) -> dict[str, float]:
		"""보정 없이 모터 원시 위치를 읽는다."""
		return positions(self.bus.sync_read(
			'Present_Position', normalize=False))

	def load_calibration(self) -> None:
		"""장치·모터 식별과 보정값을 확인한다. 모터에 쓰지 않는다."""
		from lerobot.motors import MotorCalibration

		self.bounds = {}
		self._calibration = {}
		data = json.loads(self.calibration_path.read_text())
		if not isinstance(data, dict):
			raise ValueError('보정 파일의 최상위 값은 JSON 객체여야 합니다.')
		if (data.get('schema') != 1 or data.get('id') != self.config.id
			or data.get('role') != self.role
			or data.get('motor_ids') != self.config.motor_ids):
			raise ValueError('보정 파일의 장치/모터 구성이 일치하지 않습니다.')
		entries = data.get('motors', {})
		if not isinstance(entries, dict) or set(entries) != set(JOINT_NAMES):
			raise ValueError('보정 파일에 7개 관절이 필요합니다.')
		calibration = {}
		bounds = {}
		for name in JOINT_NAMES:
			item = MotorCalibration(**entries[name])
			if (any(type(value) is not int for value in asdict(item).values())
				or item.id != self.config.motor_ids[name]
				or item.drive_mode != 0
				or not -2047 <= item.homing_offset <= 2047
				or not 0 <= item.range_min < item.range_max <= 4095):
				raise ValueError(f'{name}: 보정값이 유효하지 않습니다.')
			calibration[name] = item
			half = (item.range_max - item.range_min) * 180 / 4095
			bounds[name] = (0.0, 100.0) if name == 'gripper' else (-half, half)
		actual = self.bus.read_calibration()
		if actual != calibration:
			raise ValueError('모터와 파일의 보정값이 다릅니다. 재보정하세요.')
		self._calibration = calibration
		self.bus.calibration = calibration
		self.bounds = bounds

	def read(self) -> dict[str, float]:
		"""몸체 도 단위와 그리퍼 0~100 위치를 읽는다."""
		if not self._calibration:
			raise RuntimeError('먼저 보정값을 불러오세요.')
		raw = self.raw_positions()
		result = {}
		for name in JOINT_NAMES:
			item = self._calibration[name]
			value = raw[name]
			if not item.range_min <= value <= item.range_max:
				raise ValueError(f'{name}: 원시 위치가 보정 범위를 벗어났습니다.')
			if name == 'gripper':
				result[name] = (value - item.range_min) * 100 / (
					item.range_max - item.range_min)
			else:
				midpoint = (item.range_min + item.range_max) / 2
				result[name] = (value - midpoint) * 360 / 4095
		return positions(result)

	def activate(self) -> None:
		"""설정을 적용한다. 팔로워는 현재 위치를 목표로 쓴 후 토크를 켠다."""
		if not self._calibration:
			raise RuntimeError('보정값 없이 활성화할 수 없습니다.')
		self.read()
		self.is_active = True
		self.bus.disable_torque()
		self.bus.configure_motors()
		for name in JOINT_NAMES:
			self.bus.write('Operating_Mode', name, 0)
		if self.role == 'follower':
			for name in JOINT_NAMES:
				self.bus.write('P_Coefficient', name, 16)
				self.bus.write('I_Coefficient', name, 0)
				self.bus.write('D_Coefficient', name, 32)
			self.bus.write('Max_Torque_Limit', 'gripper', 500)
			self.bus.write('Protection_Current', 'gripper', 250)
			self.bus.write('Overload_Torque', 'gripper', 25)
			self.read()
			raw = self.raw_positions()
			self.bus.sync_write('Goal_Position', raw, normalize=False)
			self.bus.enable_torque()

	def write(self, target: dict[str, float]) -> None:
		"""활성화된 팔로워에 완전한 관절 명령을 보낸다."""
		if self.role != 'follower' or not self.is_active:
			raise RuntimeError('활성화된 팔로워만 명령을 받을 수 있습니다.')
		target = positions(target)
		for name, value in target.items():
			lower, upper = self.bounds[name]
			if not lower <= value <= upper:
				raise ValueError(f'{name}: 보정 범위 초과')
		self.bus.sync_write('Goal_Position', target)

	def calibrate(self, is_overwrite: bool = False) -> None:
		"""모든 관절의 실제 가동 범위를 측정하고 원자적으로 저장한다.

		Args:
			is_overwrite: 기존 보정 파일을 교체할지 여부.
		"""
		from lerobot.motors import MotorCalibration

		if self.calibration_path.exists() and not is_overwrite:
			raise FileExistsError('기존 보정값이 있습니다. --overwrite로 재보정하세요.')
		self.is_active = True
		self.bus.disable_torque()
		for name in JOINT_NAMES:
			self.bus.write('Operating_Mode', name, 0)
		input('로봇을 지지하고 모든 관절을 가동 범위 중앙에 놓은 뒤 Enter: ')
		offsets = self.bus.set_half_turn_homings()
		print('각 관절을 충돌 없이 사용할 범위로 천천히 움직이세요.')
		print('손목 회전도 실제 범위만 측정합니다. 완료하면 Enter.')
		mins, maxes = self.bus.record_ranges_of_motion(list(JOINT_NAMES))
		calibration = {}
		for name in JOINT_NAMES:
			if not 0 <= mins[name] < maxes[name] <= 4095:
				raise ValueError(f'{name}: 범위가 유효하지 않아 저장하지 않습니다.')
			calibration[name] = MotorCalibration(
				id=self.config.motor_ids[name], drive_mode=0,
				homing_offset=offsets[name], range_min=mins[name],
				range_max=maxes[name])
		self.bus.write_calibration(calibration)
		data = {
			'schema': 1, 'id': self.config.id, 'role': self.role,
			'motor_ids': self.config.motor_ids,
			'motors': {name: asdict(item)
				for name, item in calibration.items()},
		}
		self.calibration_path.parent.mkdir(parents=True, exist_ok=True)
		temporary = None
		try:
			with tempfile.NamedTemporaryFile(mode='w', suffix='.tmp',
				dir=self.calibration_path.parent, delete=False) as stream:
				temporary = Path(stream.name)
				json.dump(data, stream, indent=2)
				stream.flush()
				os.fsync(stream.fileno())
			os.replace(temporary, self.calibration_path)
		finally:
			if temporary is not None and temporary.exists():
				temporary.unlink()
		print(f'보정 저장: {self.calibration_path}')

	def close(self, disable_torque: bool = True) -> None:
		"""가능한 장치를 정리하고 실패 시에도 포트를 닫는다."""
		failure = None
		try:
			if self.bus.is_connected:
				self.bus.disconnect(disable_torque=disable_torque)
		except Exception as error:
			failure = error
			LOGGER.exception('장치 종료 실패: %s', self.config.port)
		finally:
			try:
				if self.bus.is_connected:
					self.bus.port_handler.closePort()
			except Exception as error:
				failure = error
				LOGGER.exception('포트 강제 종료 실패')
			self.is_active = False
		if failure is not None:
			raise RuntimeError('장치 종료를 확인하지 못했습니다. '
				'전원과 자세를 확인하세요.') from failure
