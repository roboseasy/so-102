"""공식 SO-100/101 구현에 SO-102의 7축 모터 구성을 적용한다."""

from dataclasses import dataclass

from lerobot.motors import Motor, MotorCalibration, MotorNormMode
from lerobot.motors.feetech import FeetechMotorsBus
from lerobot.robots import RobotConfig
from lerobot.robots.so_follower import SOFollower
from lerobot.robots.so_follower.config_so_follower import SOFollowerRobotConfig
from lerobot.teleoperators import TeleoperatorConfig
from lerobot.teleoperators.so_leader import SOLeader
from lerobot.teleoperators.so_leader.config_so_leader import SOLeaderTeleopConfig

from .config import JOINT_NAMES


@RobotConfig.register_subclass('so102_follower')
@dataclass
class SO102FollowerConfig(SOFollowerRobotConfig):
	"""공식 SO 팔로워 설정을 그대로 사용하는 SO-102 타입."""


@TeleoperatorConfig.register_subclass('so102_leader')
@dataclass
class SO102LeaderConfig(SOLeaderTeleopConfig):
	"""공식 SO 리더 설정을 그대로 사용하는 SO-102 타입."""


def make_native_bus(config: SO102FollowerConfig | SO102LeaderConfig,
	calibration: dict[str, MotorCalibration]) -> FeetechMotorsBus:
	"""공식 정규화 방식으로 1~7번 STS3215 버스를 생성한다.

	Args:
		config: 공식 SO 설정을 상속한 리더 또는 팔로워 설정.
		calibration: LeRobot 기본 형식의 관절 보정값.

	Returns:
		손목 추가 축과 그리퍼를 포함한 7축 모터 버스.
	"""
	body_mode = (MotorNormMode.DEGREES if config.use_degrees
		else MotorNormMode.RANGE_M100_100)
	motors = {name: Motor(index, 'sts3215',
		MotorNormMode.RANGE_0_100 if name == 'gripper' else body_mode)
		for index, name in enumerate(JOINT_NAMES, 1)}
	return FeetechMotorsBus(config.port, motors, calibration=calibration)


class SO102Follower(SOFollower):
	"""공식 SOFollower의 동작을 상속하고 모터 구성만 7축으로 바꾼다."""

	name = 'so102_follower'
	config_class = SO102FollowerConfig

	def __init__(self, config: SO102FollowerConfig) -> None:
		super().__init__(config)
		self.bus = make_native_bus(config, self.calibration)


class SO102Leader(SOLeader):
	"""공식 SOLeader의 동작을 상속하고 모터 구성만 7축으로 바꾼다."""

	name = 'so102_leader'
	config_class = SO102LeaderConfig

	def __init__(self, config: SO102LeaderConfig) -> None:
		super().__init__(config)
		self.bus = make_native_bus(config, self.calibration)
