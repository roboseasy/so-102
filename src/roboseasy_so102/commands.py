"""고객용 명령행과 동작 명령의 명시적 실행 구분."""

import argparse
from dataclasses import asdict
from importlib.metadata import PackageNotFoundError, version
import json
import logging
import math
import os
from pathlib import Path
import signal
import sys
import xml.etree.ElementTree as ET

from .config import ArmConfig, JOINT_NAMES, Settings, load_settings


def parser() -> argparse.ArgumentParser:
	"""명령행 도움말과 옵션을 구성한다."""
	root = argparse.ArgumentParser(description='로보시지 SO-102 도구')
	root.add_argument('--verbose', action='store_true')
	subs = root.add_subparsers(dest='command', required=True)
	model = subs.add_parser('model', help='URDF와 메시 검사')
	model.add_argument('--urdf', type=Path)
	init = subs.add_parser('init', help='기본 설정 생성 (기존 파일 보존)')
	init.add_argument('--output', type=Path, default=Path('config/so102.json'))
	for name, help_text in (
		('check', '설정 검사'), ('doctor', '설치와 포트 접근 확인'),
		('diagnose', '장치에 쓰지 않고 원시 위치 조회'),
		('state', '보정값을 적용한 위치 조회'),
		('calibrate', '장치별 보정'), ('teleoperate', '리더–팔로워 제어'),
		('setup-motor', '모터 한 개의 ID·통신 속도 설정'),
	):
		command = subs.add_parser(name, help=help_text)
		command.add_argument('--config', type=Path,
			default=Path('config/so102.json'))
		if name in ('diagnose', 'state', 'calibrate', 'setup-motor'):
			command.add_argument('--role', choices=('leader', 'follower'),
				required=True)
		if name in ('calibrate', 'teleoperate', 'setup-motor'):
			command.add_argument('--execute', action='store_true',
				help='실제 장치 쓰기와 토크 변경 허용')
		if name == 'setup-motor':
			command.add_argument('--joint', choices=JOINT_NAMES, required=True)
		if name == 'calibrate':
			command.add_argument('--overwrite', action='store_true')
		if name == 'teleoperate':
			command.add_argument('--simulate', action='store_true')
			command.add_argument('--duration', type=float, default=0.0,
				help='실행 초; 기본 0은 Ctrl+C까지 실행')
	return root


def doctor(settings: Settings) -> bool:
	"""설치와 장치 파일만 확인하며 포트를 열지 않는다."""
	is_ok = sys.version_info >= (3, 12)
	active_env = os.environ.get('CONDA_DEFAULT_ENV', '미활성')
	print(f'Conda 환경: {active_env} (권장: lerobot060)')
	print(f'Python 실행 파일: {sys.executable}')
	print(f'Python: {sys.version.split()[0]}')
	try:
		installed = version('lerobot')
		print(f'LeRobot: {installed} (필요: 0.6.0)')
		is_ok = is_ok and installed == '0.6.0'
	except PackageNotFoundError:
		print('LeRobot: 미설치 — pip install ".[robot]"')
		is_ok = False
	for role in ('leader', 'follower'):
		arm = getattr(settings, role)
		has_access = os.path.exists(arm.port) and os.access(
			arm.port, os.R_OK | os.W_OK)
		print(f'{role}: {arm.port} — '
			f'{"접근 가능" if has_access else "장치/권한 확인 필요"}')
		is_ok = is_ok and has_access
	return is_ok


def execute(args: argparse.Namespace) -> None:
	"""검증한 옵션에 따라 요청된 작업을 실행한다."""
	if args.command == 'model':
		from .cli import inspect_model, model_path

		joints = inspect_model(args.urdf or model_path())
		print('SO-102 · 단일 모델 · 그리퍼 포함 7개 관절')
		print('URDF 범위(deg) — 모터 보정 제어 한계와 구분하세요.')
		for name, lower, upper in joints:
			print(f'{name:16s} {math.degrees(lower):8.2f} ~ '
				f'{math.degrees(upper):8.2f}')
		print('관절·메시 경로 검증 완료')
		return
	if args.command == 'init':
		settings = Settings(ArmConfig('/dev/ttyACM0', 'so102_leader'),
			ArmConfig('/dev/ttyACM1', 'so102_follower'), Path('../calibration'))
		data = asdict(settings)
		data['calibration_dir'] = str(settings.calibration_dir)
		args.output.parent.mkdir(parents=True, exist_ok=True)
		with args.output.open('x') as stream:
			json.dump(data, stream, ensure_ascii=False, indent=2)
		print(f'설정 생성: {args.output}')
		return
	settings = load_settings(args.config)
	if args.command == 'check':
		print('설정 검사 완료: 서로 다른 포트, 7개 모터, 제어 제한 유효')
		return
	if args.command == 'doctor':
		if not doctor(settings):
			raise RuntimeError('환경 확인이 필요합니다. docs/1. usage.md 참고')
		return
	if args.command == 'teleoperate':
		from .runtime import SimulatedArm, teleoperate

		if args.simulate:
			leader = SimulatedArm(settings.leader, 'leader')
			follower = SimulatedArm(settings.follower, 'follower')
			print('가상 장치 모드: 실제 포트에 연결하지 않습니다.')
		else:
			if not args.execute:
				raise ValueError('실제 구동에는 --execute가 필요합니다.')
			from .hardware import Arm

			leader = Arm(settings.leader, 'leader', settings.calibration_dir)
			follower = Arm(settings.follower, 'follower',
				settings.calibration_dir)
		count = teleoperate(settings, leader, follower, args.duration)
		print(f'텔레오퍼레이션 완료: {count}개 명령')
		return
	if args.command in ('calibrate', 'setup-motor') and not args.execute:
		raise ValueError('보정값 쓰기에는 --execute가 필요합니다.')
	from .hardware import Arm

	arm = Arm(getattr(settings, args.role), args.role,
		settings.calibration_dir)
	try:
		if args.command == 'setup-motor':
			print(f'{args.role}: {args.joint} 모터 한 개만 보드에 연결하세요.')
			answer = input('다른 모터가 분리되었으면 SINGLE을 입력: ')
			if answer != 'SINGLE':
				raise ValueError('단일 모터 연결 확인이 취소되었습니다.')
			arm.bus.setup_motor(args.joint)
			print(f'{args.joint}: ID={arm.config.motor_ids[args.joint]} 설정 완료')
			return
		arm.connect()
		if args.command == 'calibrate':
			arm.calibrate(is_overwrite=args.overwrite)
		elif args.command == 'diagnose':
			print('원시 위치 (encoder counts):')
			print(json.dumps(arm.raw_positions(), indent=2))
		else:
			arm.load_calibration()
			print('보정 위치 (몸체 deg, gripper 0~100):')
			print(json.dumps(arm.read(), indent=2))
	finally:
		arm.close(disable_torque=arm.is_active)


def run() -> None:
	"""오류 메시지와 종료 신호를 처리하는 CLI 진입점."""
	root = parser()
	args = root.parse_args()
	logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

	def stop(signum: int, frame: object) -> None:
		raise KeyboardInterrupt

	previous = signal.signal(signal.SIGTERM, stop)
	try:
		execute(args)
	except KeyboardInterrupt:
		root.exit(130, '사용자 중단: 장치 정리를 시도했습니다.\n')
	except (OSError, ValueError, KeyError, TypeError, RuntimeError,
		ImportError, PackageNotFoundError, ET.ParseError) as error:
		if args.verbose:
			logging.exception('실행 실패')
		root.exit(1, f'오류: {error}\n')
	finally:
		signal.signal(signal.SIGTERM, previous)
