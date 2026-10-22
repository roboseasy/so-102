"""SO-102 장치와 제어 설정의 엄격한 검증."""

from dataclasses import dataclass, field
import json
import math
from pathlib import Path

JOINT_NAMES = (
	'shoulder_pan', 'shoulder_lift', 'elbow_flex', 'wrist_flex',
	'wrist_yaw', 'wrist_roll', 'gripper',
)


def positive(value: float, name: str) -> float:
	"""유한한 양수를 검증한다."""
	if isinstance(value, bool) or not isinstance(value, (int, float)):
		raise ValueError(f'{name}: 숫자가 필요합니다.')
	if not math.isfinite(value) or value <= 0:
		raise ValueError(f'{name}: 유한한 양수가 필요합니다.')
	return float(value)


@dataclass
class ArmConfig:
	"""한 장치의 포트와 모터 ID 구성."""

	port: str
	id: str
	motor_ids: dict[str, int] = field(default_factory=lambda: {
		name: index for index, name in enumerate(JOINT_NAMES, 1)
	})

	def validate(self) -> None:
		"""모터 누락·ID 충돌 및 파일명으로 사용할 장치 ID를 검사한다."""
		if not isinstance(self.port, str) or not self.port.strip():
			raise ValueError('장치 포트가 비어 있습니다.')
		if (not isinstance(self.id, str) or not self.id
			or any(c not in 'abcdefghijklmnopqrstuvwxyz'
				'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in self.id)):
			raise ValueError('장치 id는 영문·숫자·밑줄·하이픈만 허용합니다.')
		if (not isinstance(self.motor_ids, dict)
			or set(self.motor_ids) != set(JOINT_NAMES)):
			raise ValueError('7개 관절의 motor_ids가 모두 필요합니다.')
		ids = list(self.motor_ids.values())
		if any(type(item) is not int or not 1 <= item <= 253 for item in ids):
			raise ValueError('모터 ID는 1~253 정수여야 합니다.')
		if len(set(ids)) != len(ids):
			raise ValueError('같은 버스의 모터 ID가 중복되었습니다.')


@dataclass
class Settings:
	"""리더–팔로워 제어 설정. 몸체 단위는 도, 그리퍼는 0~100."""

	leader: ArmConfig
	follower: ArmConfig
	calibration_dir: Path
	fps: float = 30.0
	max_step: float = 2.0
	startup_tolerance: float = 10.0
	max_cycle_seconds: float = 0.25
	disable_torque_on_exit: bool = True
	scale: dict[str, float] = field(default_factory=lambda: {
		name: 1.0 for name in JOINT_NAMES
	})
	offset: dict[str, float] = field(default_factory=lambda: {
		name: 0.0 for name in JOINT_NAMES
	})

	def validate(self) -> None:
		"""포트 충돌, 잘못된 수치 및 관절 매핑을 거부한다."""
		self.leader.validate()
		self.follower.validate()
		if (Path(self.leader.port).resolve()
			== Path(self.follower.port).resolve()):
			raise ValueError('리더와 팔로워는 서로 다른 포트를 사용해야 합니다.')
		for name in ('fps', 'max_step', 'startup_tolerance',
			'max_cycle_seconds'):
			positive(getattr(self, name), name)
		if self.max_cycle_seconds < 1 / self.fps:
			raise ValueError('max_cycle_seconds는 한 제어 주기 이상이어야 합니다.')
		if type(self.disable_torque_on_exit) is not bool:
			raise ValueError('disable_torque_on_exit는 true/false여야 합니다.')
		for mapping in (self.scale, self.offset):
			if (not isinstance(mapping, dict)
				or set(mapping) != set(JOINT_NAMES)):
				raise ValueError('scale과 offset에 7개 관절이 필요합니다.')
			for value in mapping.values():
				if (isinstance(value, bool)
					or not isinstance(value, (int, float))
					or not math.isfinite(value)):
					raise ValueError('매핑 값은 유한한 숫자여야 합니다.')
		if any(value == 0 for value in self.scale.values()):
			raise ValueError('scale은 0일 수 없습니다.')


def load_settings(path: Path) -> Settings:
	"""JSON 설정을 읽고 상대 보정 경로를 설정 파일 기준으로 해석한다."""
	data = json.loads(path.read_text())
	if not isinstance(data, dict):
		raise ValueError('설정 파일의 최상위 값은 JSON 객체여야 합니다.')
	try:
		data['leader'] = ArmConfig(**data['leader'])
		data['follower'] = ArmConfig(**data['follower'])
		directory = Path(data.pop(
			'calibration_dir', '../calibration')).expanduser()
		if not directory.is_absolute():
			directory = path.resolve().parent / directory
		settings = Settings(calibration_dir=directory, **data)
		settings.validate()
		return settings
	except (KeyError, TypeError) as error:
		raise ValueError(f'잘못된 설정 형식: {error}') from error
