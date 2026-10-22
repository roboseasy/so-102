"""로보시지 SO-102 모델 및 제어 도구."""

from typing import Any


def __getattr__(name: str) -> Any:
	"""LeRobot 팩터리 요청 시에만 무거운 어댑터를 가져온다."""
	if name in ('SO102Leader', 'SO102LeaderConfig',
		'SO102Follower', 'SO102FollowerConfig'):
		from . import lerobot_devices

		return getattr(lerobot_devices, name)
	raise AttributeError(name)
