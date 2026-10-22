"""장비 연결 없이 배포 모델을 검증하는 명령행 도구."""

import math
from pathlib import Path
import sysconfig
import xml.etree.ElementTree as ET

from .config import JOINT_NAMES


def model_path() -> Path:
	"""소스 체크아웃 또는 설치된 배포 모델의 위치를 반환한다."""
	paths = (
		Path(__file__).resolve().parents[2]
		/ 'soarm101_7dof_description/urdf/soarm101_7dof_relative.urdf',
		Path(sysconfig.get_path('data'))
		/ 'share/roboseasy_so102/urdf/soarm101_7dof_relative.urdf',
	)
	for path in paths:
		if path.is_file():
			return path
	raise FileNotFoundError('설치된 SO-102 모델을 찾을 수 없습니다.')


def inspect_model(path: Path) -> list[tuple[str, float, float]]:
	"""단일 모델의 관절과 메시를 검증하고 라디안 범위를 반환한다.

	Args:
		path: 상대 메시 경로를 사용하는 URDF 파일.

	Returns:
		관절 이름, 하한, 상한 목록.

	Raises:
		ValueError: 관절 구성 또는 범위가 올바르지 않은 경우.
		FileNotFoundError: 참조하는 메시가 없는 경우.
	"""
	root = ET.parse(path).getroot()
	joints = [j for j in root.findall('joint') if j.get('type') != 'fixed']
	if tuple(j.get('name') for j in joints) != JOINT_NAMES:
		raise ValueError('단일 SO-102의 7개 관절 이름·순서와 다릅니다.')
	for mesh in root.findall('.//mesh'):
		filename = mesh.get('filename', '')
		if not filename or not (path.parent / filename).is_file():
			raise FileNotFoundError(f'메시 파일을 찾을 수 없습니다: {filename}')
	result = []
	for joint in joints:
		limit = joint.find('limit')
		if limit is None:
			raise ValueError(f'{joint.get("name")}: 관절 제한 누락')
		lower = float(limit.attrib['lower'])
		upper = float(limit.attrib['upper'])
		if not all(map(math.isfinite, (lower, upper))) or lower >= upper:
			raise ValueError(f'{joint.get("name")}: 잘못된 관절 제한')
		result.append((joint.attrib['name'], lower, upper))
	return result


def main() -> None:
	"""모델 검사·설정·진단·보정·텔레오퍼레이션 진입점."""
	from .commands import run

	run()


if __name__ == '__main__':
	main()
