"""하드웨어와 독립적인 관절 명령 검증 및 제한."""

import math
from collections.abc import Mapping

from .config import JOINT_NAMES, Settings, positive


def positions(values: Mapping[str, float]) -> dict[str, float]:
	"""모든 관절의 유한한 위치값을 요구한다."""
	if set(values) != set(JOINT_NAMES):
		raise ValueError('명령/상태에 7개 관절이 정확히 필요합니다.')
	result = {}
	for name in JOINT_NAMES:
		value = values[name]
		if isinstance(value, bool) or not isinstance(value, (float, int)):
			raise ValueError(f'{name}: 숫자 위치가 필요합니다.')
		if not math.isfinite(value):
			raise ValueError(f'{name}: 비정상 위치값')
		result[name] = float(value)
	return result


def map_action(values: Mapping[str, float], settings: Settings
	) -> dict[str, float]:
	"""리더의 위치에 관절별 방향·비율·오프셋을 적용한다."""
	return positions({name: value * settings.scale[name]
		+ settings.offset[name] for name, value in positions(values).items()})


def limit_action(target: Mapping[str, float], present: Mapping[str, float],
	bounds: Mapping[str, tuple[float, float]], max_step: float
	) -> dict[str, float]:
	"""범위를 벗어난 입력을 거부하고 현재 위치 대비 변화량을 제한한다."""
	positive(max_step, 'max_step')
	target, present = positions(target), positions(present)
	if set(bounds) != set(JOINT_NAMES):
		raise ValueError('관절 한계가 누락되었습니다.')
	result = {}
	for name in JOINT_NAMES:
		lower, upper = bounds[name]
		if not all(map(math.isfinite, (lower, upper))) or lower >= upper:
			raise ValueError(f'{name}: 잘못된 한계')
		if not lower <= present[name] <= upper:
			raise ValueError(f'{name}: 현재 위치가 보정 범위를 벗어났습니다.')
		if not lower <= target[name] <= upper:
			raise ValueError(f'{name}: 목표 위치가 팔로워 범위를 벗어났습니다.')
		delta = max(-max_step, min(max_step, target[name] - present[name]))
		result[name] = present[name] + delta
	return result
