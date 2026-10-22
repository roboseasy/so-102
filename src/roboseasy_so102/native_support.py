"""LeRobot 기본 보정 저장소와 CLI 생명주기를 연결한다."""

import logging
import os
from pathlib import Path
import tempfile
from typing import Any

from lerobot.motors import MotorCalibration
from lerobot.utils.errors import DeviceAlreadyConnectedError

from .config import JOINT_NAMES

LOGGER = logging.getLogger(__name__)


class NativeLifecycle:
	"""공식 SO 기반 클래스에 SO-102 보정·정리 동작을 제공한다."""

	def __init__(self, config: Any) -> None:
		self._has_torque_changes = False
		super().__init__(config)

	@property
	def is_connected(self) -> bool:
		"""버스와 구성된 모든 카메라의 연결 상태."""
		return self.bus.is_connected and all(camera.is_connected
			for camera in getattr(self, 'cameras', {}).values())

	def connect(self, calibrate: bool = True) -> None:
		"""보정 후 구동한다. calibrate=False는 보정 전 읽기 연결만 연다."""
		if self.bus.is_connected:
			raise DeviceAlreadyConnectedError('SO-102 버스가 이미 연결되었습니다.')
		try:
			self.bus.connect()
			if not calibrate:
				return
			if self.calibration:
				self._validate_calibration()
			if not self.is_calibrated:
				self.calibrate()
			self._validate_calibration()
			for camera in getattr(self, 'cameras', {}).values():
				camera.connect()
			self.configure()
		except BaseException:
			try:
				self.disconnect()
			except Exception:
				LOGGER.exception('연결 실패 후 SO-102 정리 실패')
			raise

	def _validate_calibration(self) -> None:
		if set(self.calibration) != set(JOINT_NAMES):
			raise ValueError('SO-102 보정값에 7개 관절이 필요합니다.')
		for name, item in self.calibration.items():
			if (item.id != self.bus.motors[name].id
				or not 0 <= item.range_min < item.range_max <= 4095
				or item.drive_mode not in (0, 1)):
				raise ValueError(f'{name}: 모터 구성과 보정값이 일치하지 않습니다.')

	def calibrate(self) -> None:
		"""lerobot-calibrate에서 호출되며 기본 LeRobot JSON으로 저장한다."""
		if not self.bus.is_connected:
			raise RuntimeError('보정 전에 connect(calibrate=False)가 필요합니다.')
		if self.calibration:
			answer = input('기존 보정값을 적용하려면 Enter, '
				'새로 측정하려면 c를 입력하세요: ')
			if answer.strip().lower() != 'c':
				self._validate_calibration()
				self._has_torque_changes = True
				self.bus.disable_torque()
				self.bus.write_calibration(self.calibration)
				return
		self._has_torque_changes = True
		self.bus.disable_torque()
		for name in JOINT_NAMES:
			self.bus.write('Operating_Mode', name, 0)
		input('로봇을 지지하고 관절을 가동 범위 중앙에 놓은 뒤 Enter: ')
		offsets = self.bus.set_half_turn_homings()
		print('7개 관절 모두를 충돌 없는 운용 범위로 천천히 움직이세요.')
		print('손목 회전도 직접 측정합니다. 완료하면 Enter를 누르세요.')
		mins, maxes = self.bus.record_ranges_of_motion(list(JOINT_NAMES))
		calibration = {name: MotorCalibration(
			id=self.bus.motors[name].id, drive_mode=0,
			homing_offset=offsets[name], range_min=mins[name],
			range_max=maxes[name]) for name in JOINT_NAMES}
		previous = self.calibration
		self.calibration = calibration
		try:
			self._validate_calibration()
		except ValueError:
			self.calibration = previous
			raise
		self.bus.write_calibration(calibration)
		self._save_calibration()
		print(f'LeRobot 보정 저장: {self.calibration_fpath}')

	def _save_calibration(self, fpath: Path | None = None) -> None:
		target = fpath or self.calibration_fpath
		target.parent.mkdir(parents=True, exist_ok=True)
		with tempfile.NamedTemporaryFile(
			dir=target.parent, delete=False) as stream:
			temporary = Path(stream.name)
		try:
			super()._save_calibration(temporary)
			os.replace(temporary, target)
		finally:
			if temporary.exists():
				temporary.unlink()

	def disconnect(self) -> None:
		"""카메라와 버스를 정리하고 오류가 나도 포트 닫기를 시도한다."""
		failures = []
		for camera in getattr(self, 'cameras', {}).values():
			try:
				if camera.is_connected:
					camera.disconnect()
			except Exception as error:
				failures.append(error)
		try:
			if self.bus.is_connected:
				is_disable_torque = self._has_torque_changes and getattr(
					self.config, 'disable_torque_on_disconnect', True)
				self.bus.disconnect(disable_torque=is_disable_torque)
		except Exception as error:
			failures.append(error)
		finally:
			if self.bus.is_connected:
				try:
					self.bus.port_handler.closePort()
				except Exception as error:
					failures.append(error)
			self._has_torque_changes = False
		if failures:
			raise RuntimeError('SO-102 장치 정리를 완료하지 못했습니다.') from failures[0]
