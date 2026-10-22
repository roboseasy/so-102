"""LeRobot v0.6.0이 자동 발견하는 SO-102 리더·팔로워 플러그인."""

from roboseasy_so102.lerobot_devices import (
	SO102Follower,
	SO102FollowerConfig,
	SO102Leader,
	SO102LeaderConfig,
)

__all__ = [
	'SO102Follower', 'SO102FollowerConfig',
	'SO102Leader', 'SO102LeaderConfig',
]
