"""STL과 URDF의 영점 자세를 README 이미지로 렌더링한다."""

import argparse
from pathlib import Path
import struct
import xml.etree.ElementTree as ET

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
import numpy as np


def transform(origin: ET.Element | None) -> np.ndarray:
	"""URDF xyz/rpy로 동차 변환을 계산한다."""
	matrix = np.eye(4)
	if origin is None:
		return matrix
	roll, pitch, yaw = map(float, origin.get('rpy', '0 0 0').split())
	cx, cy, cz = np.cos([roll, pitch, yaw])
	sx, sy, sz = np.sin([roll, pitch, yaw])
	rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
	ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
	rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
	matrix[:3, :3] = rz @ ry @ rx
	matrix[:3, 3] = list(map(float, origin.get('xyz', '0 0 0').split()))
	return matrix


def render(urdf: Path, output: Path) -> None:
	"""영점 관절 자세의 실제 메시 형상을 렌더링한다."""
	robot = ET.parse(urdf).getroot()
	links = {link.get('name') for link in robot.findall('link')}
	children = {joint.find('child').get('link')
		for joint in robot.findall('joint')}
	roots = links - children
	if len(roots) != 1:
		raise ValueError('단일 루트 링크가 필요합니다.')
	poses = {roots.pop(): np.eye(4)}
	pending = list(robot.findall('joint'))
	while pending:
		has_progress = False
		for joint in pending[:]:
			parent = joint.find('parent').get('link')
			if parent in poses:
				poses[joint.find('child').get('link')] = (
					poses[parent] @ transform(joint.find('origin')))
				pending.remove(joint)
				has_progress = True
		if not has_progress:
			raise ValueError('URDF 트리가 연결되지 않았습니다.')
	colors = {'printed': [.82, .82, .80], 'motor': [.18, .18, .20]}
	figure = plt.figure(figsize=(12, 7), facecolor='#f2f5f8')
	axes = figure.add_subplot(111, projection='3d')
	points = []
	for link in robot.findall('link'):
		for visual in link.findall('visual'):
			mesh = visual.find('geometry/mesh')
			if mesh is None:
				continue
			data = (urdf.parent / mesh.attrib['filename']).read_bytes()
			count = struct.unpack_from('<I', data, 80)[0]
			dtype = np.dtype([('normal', '<f4', 3),
				('vertices', '<f4', (3, 3)), ('attribute', '<u2')])
			vertices = np.frombuffer(data, dtype=dtype, count=count,
				offset=84)['vertices'].astype(float)
			vertices *= np.array(list(map(float,
				mesh.get('scale', '1 1 1').split())))
			pose = poses[link.attrib['name']] @ transform(visual.find('origin'))
			vertices = vertices @ pose[:3, :3].T + pose[:3, 3]
			points.append(vertices.reshape(-1, 3))
			material = visual.find('material')
			color = colors.get(material.get('name')
				if material is not None else '', [.55, .62, .70])
			normals = np.cross(vertices[:, 1] - vertices[:, 0],
				vertices[:, 2] - vertices[:, 0])
			normals /= np.maximum(np.linalg.norm(normals, axis=1,
				keepdims=True), 1e-12)
			light = np.array([.3, -.5, .8])
			light /= np.linalg.norm(light)
			shade = .65 + .35 * np.maximum(normals @ light, 0)
			axes.add_collection3d(Poly3DCollection(vertices,
				facecolors=np.array(color)[None, :] * shade[:, None],
				edgecolors='none', rasterized=True))
	all_points = np.concatenate(points)
	low, high = all_points.min(axis=0), all_points.max(axis=0)
	center, radius = (low + high) / 2, (high - low).max() * .52
	axes.set_xlim(center[0] - radius, center[0] + radius)
	axes.set_ylim(center[1] - radius, center[1] + radius)
	axes.set_zlim(center[2] - radius, center[2] + radius)
	axes.set_box_aspect((1, 1, 1), zoom=1.5)
	axes.set_proj_type('ortho')
	axes.view_init(elev=20, azim=-55)
	axes.set_axis_off()
	axes.set_facecolor('#f2f5f8')
	figure.text(.05, .91, 'SO-102', fontsize=32, weight='bold',
		color='#18283a')
	figure.text(.05, .85, '7 joints including gripper | Single-arm URDF',
		fontsize=12, color='#546578')
	figure.subplots_adjust(left=0, right=1, bottom=0, top=.82)
	output.parent.mkdir(parents=True, exist_ok=True)
	figure.savefig(output, dpi=150, facecolor=figure.get_facecolor())
	plt.close(figure)


def main() -> None:
	"""저장소의 기본 단일 모델을 렌더링한다."""
	base = Path(__file__).resolve().parents[1]
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument('--urdf', type=Path, default=base
		/ 'soarm101_7dof_description/urdf/soarm101_7dof_relative.urdf')
	parser.add_argument('--output', type=Path,
		default=base / 'docs/images/so102_urdf.png')
	args = parser.parse_args()
	render(args.urdf, args.output)


if __name__ == '__main__':
	main()
