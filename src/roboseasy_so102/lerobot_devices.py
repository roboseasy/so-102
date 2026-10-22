"""공식 LeRobot CLI와 보정 형식을 사용하는 SO-102 확장 장치."""

from dataclasses import dataclass, field
import logging

from lerobot.motors import Motor, MotorCalibration, MotorNormMode
from lerobot.motors.feetech import FeetechMotorsBus
from lerobot.robots import RobotConfig
from lerobot.robots.so_follower import SOFollower
from lerobot.robots.so_follower.config_so_follower import SOFollowerRobotConfig
from lerobot.robots.utils import ensure_safe_goal_position
from lerobot.teleoperators import TeleoperatorConfig
from lerobot.teleoperators.so_leader import SOLeader
from lerobot.teleoperators.so_leader.config_so_leader import (
	SOLeaderTeleopConfig,
)
from lerobot.utils.decorators import check_if_not_connected

from .config import ArmConfig, JOINT_NAMES, positive
from .hardware import require_lerobot
from .native_support import NativeLifecycle
from .safety import positions

LOGGER = logging.getLogger(__name__)


@RobotConfig.register_subclass('so102_follower')
@dataclass(kw_only=True)
class SO102FollowerConfig(SOFollowerRobotConfig):
	"""LeRobot 표준 옵션에 7축 모터 구성과 초기 자세 제한을 추가한다."""

	id: str | None = 'so102_follower'
	max_relative_target: float | dict[str, float] | None = 2.0
	startup_tolerance: float = 10.0
	motor_ids: dict[str, int] = field(default_factory=lambda: {
		name: index for index, name in enumerate(JOINT_NAMES, 1)
	})


@TeleoperatorConfig.register_subclass('so102_leader')
@dataclass(kw_only=True)
class SO102LeaderConfig(SOLeaderTeleopConfig):
	"""LeRobot 표준 리더 옵션과 SO-102 모터 구성."""

	id: str | None = 'so102_leader'
	motor_ids: dict[str, int] = field(default_factory=lambda: {
		name: index for index, name in enumerate(JOINT_NAMES, 1)
	})


def make_native_bus(config: SO102FollowerConfig | SO102LeaderConfig,
	calibration: dict[str, MotorCalibration]) -> FeetechMotorsBus:
	"""순서와 이름을 보존하여 기본 1~7번 STS3215 버스를 생성한다."""
	require_lerobot()
	ArmConfig(config.port, config.id or 'so102', config.motor_ids).validate()
	body_mode = (MotorNormMode.DEGREES if config.use_degrees
		else MotorNormMode.RANGE_M100_100)
	motors = {name: Motor(config.motor_ids[name], 'sts3215',
		MotorNormMode.RANGE_0_100 if name == 'gripper' else body_mode)
		for name in JOINT_NAMES}
	return FeetechMotorsBus(config.port, motors, calibration=calibration)


class SO102Follower(NativeLifecycle, SOFollower):
	"""SOFollower를 확장한 7축 SO-102. 카메라는 기본 비활성이다."""

	name = 'so102_follower'
	config_class = SO102FollowerConfig

	def __init__(self, config: SO102FollowerConfig) -> None:
		positive(config.startup_tolerance, 'startup_tolerance')
		limits = config.max_relative_target
		if isinstance(limits, dict):
			if set(limits) != set(JOINT_NAMES):
				raise ValueError('max_relative_target에 7개 관절이 필요합니다.')
			for name, value in limits.items():
				positive(value, name)
		elif limits is not None:
			positive(limits, 'max_relative_target')
		super().__init__(config)
		self.bus = make_native_bus(config, self.calibration)
		self._is_first_action = True

	def configure(self) -> None:
		"""현재 위치를 목표로 기록한 다음 팔로워 토크를 활성화한다."""
		self._is_first_action = True
		self._has_torque_changes = True
		self.bus.disable_torque()
		self.bus.configure_motors()
		for name in JOINT_NAMES:
			self.bus.write('Operating_Mode', name, 0)
			self.bus.write('P_Coefficient', name, 16)
			self.bus.write('I_Coefficient', name, 0)
			self.bus.write('D_Coefficient', name, 32)
		self.bus.write('Max_Torque_Limit', 'gripper', 500)
		self.bus.write('Protection_Current', 'gripper', 250)
		self.bus.write('Overload_Torque', 'gripper', 25)
		raw = self.bus.sync_read('Present_Position', normalize=False)
		positions(raw)
		self.bus.sync_write('Goal_Position', raw, normalize=False)
		self.bus.enable_torque()

	@check_if_not_connected
	def send_action(self, action: dict[str, float]) -> dict[str, float]:
		"""공식 명령 형식의 위치를 검증하고 실제 전송한 값을 반환한다."""
		if set(action) != set(self.action_features):
			raise ValueError('7개 .pos 관절 명령이 필요합니다.')
		target = positions({name: action[f'{name}.pos']
			for name in JOINT_NAMES})
		present = positions(self.bus.sync_read('Present_Position'))
		for name in JOINT_NAMES:
			calibration = self.calibration[name]
			if name == 'gripper':
				lower, upper = 0.0, 100.0
			elif self.config.use_degrees:
				span = calibration.range_max - calibration.range_min
				half = span * 180 / 4095
				lower, upper = -half, half
			else:
				lower, upper = -100.0, 100.0
			if not lower <= present[name] <= upper:
				raise ValueError(f'{name}: 팔로워 현재 위치가 보정 범위를 벗어남')
			if not lower <= target[name] <= upper:
				raise ValueError(f'{name}: 팔로워 보정 범위를 벗어난 목표')
			if self._is_first_action and abs(target[name] - present[name]
				) > self.config.startup_tolerance:
				raise ValueError(f'{name}: 두 암의 초기 자세를 맞추세요.')
		goal = target
		if self.config.max_relative_target is not None:
			pairs = {name: (target[name], present[name])
				for name in JOINT_NAMES}
			goal = ensure_safe_goal_position(
				pairs, self.config.max_relative_target)
		self.bus.sync_write('Goal_Position', goal)
		self._is_first_action = False
		return {f'{name}.pos': value for name, value in goal.items()}


class SO102Leader(NativeLifecycle, SOLeader):
	"""기존 SOLeader의 입력·토크 인터페이스를 확장한 SO-102 리더."""

	name = 'so102_leader'
	config_class = SO102LeaderConfig

	def __init__(self, config: SO102LeaderConfig) -> None:
		super().__init__(config)
		self.bus = make_native_bus(config, self.calibration)

	def configure(self) -> None:
		"""리더는 토크를 해제하고 위치 입력에 사용할 모터를 설정한다."""
		self._has_torque_changes = True
		super().configure()

	@check_if_not_connected
	def get_action(self) -> dict[str, float]:
		"""완전하고 유한한 7개 관절 입력을 공식 형식으로 반환한다."""
		values = positions(self.bus.sync_read('Present_Position'))
		return {f'{name}.pos': value for name, value in values.items()}
