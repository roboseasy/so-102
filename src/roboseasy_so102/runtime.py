"""장치 생명주기와 텔레오퍼레이션 제어 루프."""

import logging
import math
import sys
import time
from typing import Protocol

from .config import ArmConfig, JOINT_NAMES, Settings
from .safety import limit_action, map_action, positions

LOGGER = logging.getLogger(__name__)


class Device(Protocol):
	"""제어 루프가 요구하는 장치 인터페이스."""

	is_active: bool
	bounds: dict[str, tuple[float, float]]

	def connect(self) -> None: ...
	def load_calibration(self) -> None: ...
	def activate(self) -> None: ...
	def read(self) -> dict[str, float]: ...
	def write(self, target: dict[str, float]) -> None: ...
	def close(self, disable_torque: bool = True) -> None: ...


def check_start(target: dict[str, float], present: dict[str, float],
	settings: Settings) -> None:
	"""초기 자세 차이가 큰 경우 활성화 전에 거부한다."""
	for name in JOINT_NAMES:
		if abs(target[name] - present[name]) > settings.startup_tolerance:
			raise ValueError(f'{name}: 초기 자세 차이가 큽니다. '
				'두 암의 자세를 맞춘 후 다시 시작하세요.')


def teleoperate(settings: Settings, leader: Device, follower: Device,
	duration: float = 0.0) -> int:
	"""입력을 검증한 후 팔로워에 전달하며 모든 종료 경로를 정리한다.

	Args:
		settings: 검증할 장치와 제어 설정.
		leader: 리더 입력 장치.
		follower: 팔로워 출력 장치.
		duration: 초 단위 실행 시간. 0이면 중단할 때까지 실행.

	Returns:
		정상적으로 전송한 명령 수.
	"""
	settings.validate()
	if not math.isfinite(duration) or duration < 0:
		raise ValueError('duration은 유한한 0 이상의 값이어야 합니다.')
	count = 0
	try:
		leader.connect()
		follower.connect()
		leader.load_calibration()
		follower.load_calibration()
		target = map_action(leader.read(), settings)
		present = positions(follower.read())
		check_start(target, present, settings)
		limit_action(target, present, follower.bounds, settings.max_step)
		leader.activate()
		# 리더 토크 해제 이후 자세가 바뀔 수 있으므로 다시 검사한다.
		target = map_action(leader.read(), settings)
		present = positions(follower.read())
		check_start(target, present, settings)
		limit_action(target, present, follower.bounds, settings.max_step)
		follower.activate()
		started = previous = time.monotonic()
		while duration == 0 or time.monotonic() - started < duration:
			cycle = time.monotonic()
			if cycle - previous > settings.max_cycle_seconds:
				raise TimeoutError('제어 루프 갱신이 지연되어 중단합니다.')
			target = map_action(leader.read(), settings)
			present = positions(follower.read())
			goal = limit_action(target, present, follower.bounds,
				settings.max_step)
			if time.monotonic() - cycle > settings.max_cycle_seconds:
				raise TimeoutError('오래된 입력을 전송하지 않고 중단합니다.')
			follower.write(goal)
			if time.monotonic() - cycle > settings.max_cycle_seconds:
				raise TimeoutError('명령 전송이 지연되어 중단합니다.')
			previous = cycle
			count += 1
			time.sleep(max(0.0, 1 / settings.fps
				- (time.monotonic() - cycle)))
		return count
	finally:
		# 일부 연결·활성화 실패에서도 두 장치 모두 정리를 시도한다.
		has_original_error = sys.exc_info()[0] is not None
		has_cleanup_error = False
		for device in (follower, leader):
			try:
				device.close(disable_torque=(device.is_active
					and settings.disable_torque_on_exit))
			except Exception:
				has_cleanup_error = True
				LOGGER.exception('장치 정리 실패')
		if has_cleanup_error and not has_original_error:
			raise RuntimeError('장치 정리가 완료되지 않았습니다.')


class SimulatedArm:
	"""시리얼 장치 없이 실행 흐름을 확인하는 가상 장치."""

	def __init__(self, config: ArmConfig, role: str) -> None:
		self.config = config
		self.role = role
		self.is_active = False
		self.is_connected = False
		self.bounds = {name: (0.0, 100.0) if name == 'gripper'
			else (-90.0, 90.0) for name in JOINT_NAMES}
		self._position = {name: 50.0 if name == 'gripper'
			else 0.0 for name in JOINT_NAMES}
		self._started = time.monotonic()

	def connect(self) -> None:
		"""가상 연결 상태를 설정한다."""
		self.is_connected = True

	def load_calibration(self) -> None:
		"""가상 연결 여부를 검사한다."""
		if not self.is_connected:
			raise RuntimeError('가상 장치가 연결되지 않았습니다.')

	def activate(self) -> None:
		"""가상 구동 상태를 설정한다."""
		self.is_active = True

	def read(self) -> dict[str, float]:
		"""리더는 작은 파형, 팔로워는 마지막 명령값을 반환한다."""
		if self.role == 'leader':
			value = 3 * math.sin(time.monotonic() - self._started)
			return {name: 50.0 + value if name == 'gripper'
				else value for name in JOINT_NAMES}
		return self._position.copy()

	def write(self, target: dict[str, float]) -> None:
		"""명령값을 가상 위치로 저장한다."""
		if not self.is_active:
			raise RuntimeError('가상 장치가 비활성 상태입니다.')
		self._position = positions(target)

	def close(self, disable_torque: bool = True) -> None:
		"""가상 장치의 상태를 해제한다."""
		self.is_connected = self.is_active = False
