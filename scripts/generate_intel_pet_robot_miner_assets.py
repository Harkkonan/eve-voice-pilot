"""Generate Intel Pet robot-miner sprite frames from local CC0 source packs.

This is a development helper, not runtime code. It expects the raw packs under
``local_archives/intel_pet_cc0_packs`` and writes the committed transparent PNG
frames used by the Tkinter Intel Pet overlay.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image, ImageDraw, ImageFont

try:
    import trimesh
except ImportError as exc:  # pragma: no cover - dev helper only
    raise SystemExit("Install the optional build helper first: .\\.venv\\Scripts\\python.exe -m pip install trimesh") from exc


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "local_archives" / "intel_pet_cc0_packs"
SPRITE_DIR = ROOT / "src" / "eve_voice_pilot" / "static" / "intel-pet"
CANVAS_SIZE = (160, 128)
RENDER_SCALE = 3

MECH_PATH = SOURCE_ROOT / "animated_mech_textured_gltf" / "Mike.gltf"
ROBOT_PATH = SOURCE_ROOT / "lowpoly_robot" / "lowpoly-robot-1125852" / "OBJ" / "Robot.obj"
LEFT_PICK_PATH = SOURCE_ROOT / "low_poly_tools" / "gltf" / "axe01.gltf"
RIGHT_PICK_PATH = SOURCE_ROOT / "low_poly_tools" / "gltf" / "axe02.gltf"
SCIFI_EYE_PATH = SOURCE_ROOT / "scifi_essentials" / "sci-fi-essentials-kit-12009762" / "glTF" / "Enemy_EyeDrone.gltf"
SCIFI_MINE_PATH = SOURCE_ROOT / "scifi_essentials" / "sci-fi-essentials-kit-12009762" / "glTF" / "Prop_Mine.gltf"


@dataclass(frozen=True)
class RenderMesh:
    vertices: np.ndarray
    faces: np.ndarray
    color: tuple[int, int, int]
    alpha: int = 255


def rotation_x(angle: float) -> np.ndarray:
    c = math.cos(angle)
    s = math.sin(angle)
    return np.array(((1, 0, 0), (0, c, -s), (0, s, c)), dtype=float)


def rotation_y(angle: float) -> np.ndarray:
    c = math.cos(angle)
    s = math.sin(angle)
    return np.array(((c, 0, s), (0, 1, 0), (-s, 0, c)), dtype=float)


def rotation_z(angle: float) -> np.ndarray:
    c = math.cos(angle)
    s = math.sin(angle)
    return np.array(((c, -s, 0), (s, c, 0), (0, 0, 1)), dtype=float)


def load_meshes(path: Path, color: tuple[int, int, int], alpha: int = 255) -> list[RenderMesh]:
    if not path.exists():
        raise SystemExit(f"Missing source asset: {path}")
    scene = trimesh.load(path, force="scene")
    meshes: list[RenderMesh] = []
    for mesh in scene.dump():
        if not hasattr(mesh, "vertices") or not hasattr(mesh, "faces"):
            continue
        vertices = np.asarray(mesh.vertices, dtype=float)
        faces = np.asarray(mesh.faces, dtype=int)
        if len(vertices) and len(faces):
            meshes.append(RenderMesh(vertices=vertices, faces=faces, color=color, alpha=alpha))
    if not meshes:
        raise SystemExit(f"No renderable meshes found in: {path}")
    return meshes


def normalized(meshes: Iterable[RenderMesh], *, height: float, color: tuple[int, int, int], alpha: int = 255) -> list[RenderMesh]:
    meshes = list(meshes)
    vertices = np.vstack([mesh.vertices for mesh in meshes])
    mins = vertices.min(axis=0)
    maxs = vertices.max(axis=0)
    center = (mins + maxs) / 2.0
    size = maxs - mins
    scale = height / max(size[1], 0.001)
    result = []
    for mesh in meshes:
        v = mesh.vertices.copy()
        v[:, 0] = (v[:, 0] - center[0]) * scale
        v[:, 1] = (v[:, 1] - mins[1]) * scale
        v[:, 2] = (v[:, 2] - center[2]) * scale
        result.append(RenderMesh(vertices=v, faces=mesh.faces, color=color, alpha=alpha))
    return result


def transformed(
    meshes: Iterable[RenderMesh],
    *,
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0),
    rotate: tuple[float, float, float] = (0.0, 0.0, 0.0),
    translate: tuple[float, float, float] = (0.0, 0.0, 0.0),
    color: tuple[int, int, int] | None = None,
    alpha: int | None = None,
) -> list[RenderMesh]:
    sx, sy, sz = scale
    rx, ry, rz = (math.radians(value) for value in rotate)
    matrix = rotation_z(rz) @ rotation_y(ry) @ rotation_x(rx)
    offset = np.array(translate, dtype=float)
    result = []
    for mesh in meshes:
        v = mesh.vertices.copy()
        v *= np.array((sx, sy, sz), dtype=float)
        v = v @ matrix.T
        v += offset
        result.append(
            RenderMesh(
                vertices=v,
                faces=mesh.faces,
                color=color or mesh.color,
                alpha=mesh.alpha if alpha is None else alpha,
            )
        )
    return result


def project(points: np.ndarray, *, size: tuple[int, int]) -> np.ndarray:
    width, height = size
    scale = 36.0 * RENDER_SCALE
    x = (points[:, 0] + points[:, 2] * 0.34) * scale + width * RENDER_SCALE / 2.0
    y = (-points[:, 1] + points[:, 2] * 0.16) * scale + height * RENDER_SCALE * 0.96
    return np.column_stack((x, y))


def shaded_color(color: tuple[int, int, int], normal: np.ndarray, depth: float) -> tuple[int, int, int]:
    light = np.array((-0.45, 0.72, 0.53), dtype=float)
    light /= np.linalg.norm(light)
    normal = normal / max(np.linalg.norm(normal), 0.001)
    dot = max(float(np.dot(normal, light)), 0.0)
    intensity = 0.47 + dot * 0.43 + max(min(depth, 1.2), -1.2) * 0.035
    return tuple(max(0, min(255, int(component * intensity))) for component in color)


def draw_meshes(base: Image.Image, meshes: Iterable[RenderMesh]) -> None:
    draw = ImageDraw.Draw(base, "RGBA")
    faces_to_draw = []
    for mesh in meshes:
        projected = project(mesh.vertices, size=CANVAS_SIZE)
        for face in mesh.faces:
            verts = mesh.vertices[face]
            v1 = verts[1] - verts[0]
            v2 = verts[2] - verts[0]
            normal = np.cross(v1, v2)
            if np.linalg.norm(normal) < 0.0001:
                continue
            depth = float(verts[:, 2].mean() - verts[:, 0].mean() * 0.08)
            faces_to_draw.append((depth, projected[face], mesh.color, mesh.alpha, normal))
    for depth, polygon, color, alpha, normal in sorted(faces_to_draw, key=lambda item: item[0]):
        fill = (*shaded_color(color, normal, depth), alpha)
        draw.polygon([tuple(point) for point in polygon], fill=fill)


def draw_lasers(draw: ImageDraw.ImageDraw, *, frame: int, scale: int) -> None:
    phase = frame % 3
    offsets = ((0, -2), (1, 0), (-1, 2))[phase]
    start_x = 80 * scale
    start_y = (56 + offsets[1]) * scale
    for yoff in (-2, 3):
        y = start_y + yoff * scale
        draw.line((start_x, y, 144 * scale, y - (9 + phase * 2) * scale), fill=(47, 202, 255, 230), width=2 * scale)
        draw.line((start_x, y + scale, 142 * scale, y - (6 + phase) * scale), fill=(255, 76, 55, 180), width=scale)


def draw_bursts(draw: ImageDraw.ImageDraw, *, frame: int, scale: int) -> None:
    burst_points = ((23, 76), (137, 72), (36, 60), (126, 56))
    for index, (x, y) in enumerate(burst_points):
        if (frame + index) % 2:
            continue
        r = (4 + (frame + index) % 3) * scale
        cx = x * scale
        cy = y * scale
        color = (238, 62, 46, 230)
        draw.line((cx - r, cy, cx + r, cy), fill=color, width=scale)
        draw.line((cx, cy - r, cx, cy + r), fill=color, width=scale)
        draw.ellipse((cx - r // 2, cy - r // 2, cx + r // 2, cy + r // 2), outline=(255, 191, 83, 220), width=scale)


def draw_label(draw: ImageDraw.ImageDraw, *, scale: int) -> None:
    try:
        font = ImageFont.truetype("arialbd.ttf", 11 * scale)
    except OSError:
        font = ImageFont.load_default()
    x = 58 * scale
    y = 67 * scale
    for dy in (-scale, scale):
        draw.text((x, y + dy), "NOM", font=font, fill=(16, 23, 34, 180))
        draw.text((x, y + dy + 13 * scale), "NOM", font=font, fill=(16, 23, 34, 180))
    draw.text((x, y), "NOM", font=font, fill=(213, 191, 124, 255))
    draw.text((x, y + 13 * scale), "NOM", font=font, fill=(213, 191, 124, 255))


def make_frame(
    frame: int,
    *,
    mech: list[RenderMesh],
    robot: list[RenderMesh],
    left_pick: list[RenderMesh],
    right_pick: list[RenderMesh],
    eye: list[RenderMesh],
    mine: list[RenderMesh],
) -> Image.Image:
    image = Image.new("RGBA", (CANVAS_SIZE[0] * RENDER_SCALE, CANVAS_SIZE[1] * RENDER_SCALE), (0, 0, 0, 0))
    meshes: list[RenderMesh] = []
    morph = min(1.0, max(0.0, frame / 2.0, (11 - frame) / 2.0 if frame > 8 else 1.0))
    squat = 0.76 + 0.06 * math.sin(frame * 0.9)
    bob = math.sin(frame * 0.75) * 0.04
    swing_left = [32, 4, -26, -42, -16, 30, -30, 20, -18, 12, 34, 6][frame]
    swing_right = [-26, -44, 18, 34, -32, -8, 24, -36, 26, -22, -38, -4][frame]

    meshes += transformed(mech, scale=(0.58 * morph, 0.50 * squat, 0.74), rotate=(0, -14, 0), translate=(0.0, 0.02 + bob, -0.04), alpha=205)
    meshes += transformed(robot, scale=(0.72, 0.70 * squat, 0.68), rotate=(0, -7, 0), translate=(0.0, -0.03 + bob, 0.08))
    meshes += transformed(mine, scale=(0.42, 0.25, 0.35), rotate=(0, 20, 0), translate=(0.0, 1.28 + bob, -0.20), color=(171, 118, 52), alpha=230)
    meshes += transformed(eye, scale=(0.40, 0.18, 0.18), rotate=(0, -8, 0), translate=(0.0, 2.25 + bob, -0.22), color=(88, 177, 213), alpha=240)
    meshes += transformed(left_pick, scale=(2.6, 1.65, 1.65), rotate=(4, -8, 105 + swing_left), translate=(-1.12, 1.03 + bob, 0.08), color=(143, 91, 40))
    meshes += transformed(right_pick, scale=(2.5, 1.55, 1.55), rotate=(-4, 12, -99 + swing_right), translate=(1.12, 1.07 + bob, 0.05), color=(143, 91, 40))

    draw_meshes(image, meshes)
    draw = ImageDraw.Draw(image, "RGBA")
    draw_label(draw, scale=RENDER_SCALE)
    if frame in {4, 5, 8, 9, 10}:
        draw_lasers(draw, frame=frame, scale=RENDER_SCALE)
    draw_bursts(draw, frame=frame, scale=RENDER_SCALE)
    image = image.resize(CANVAS_SIZE, Image.Resampling.NEAREST)
    return image


def generate() -> None:
    mech = normalized(load_meshes(MECH_PATH, (67, 83, 108)), height=3.1, color=(67, 83, 108), alpha=220)
    robot = normalized(load_meshes(ROBOT_PATH, (132, 115, 88)), height=3.0, color=(132, 115, 88), alpha=250)
    left_pick = normalized(load_meshes(LEFT_PICK_PATH, (150, 92, 40)), height=1.0, color=(150, 92, 40), alpha=255)
    right_pick = normalized(load_meshes(RIGHT_PICK_PATH, (150, 92, 40)), height=1.0, color=(150, 92, 40), alpha=255)
    eye = normalized(load_meshes(SCIFI_EYE_PATH, (90, 181, 216)), height=1.0, color=(90, 181, 216), alpha=240)
    mine = normalized(load_meshes(SCIFI_MINE_PATH, (172, 127, 62)), height=1.0, color=(172, 127, 62), alpha=230)
    SPRITE_DIR.mkdir(parents=True, exist_ok=True)
    for frame in range(12):
        image = make_frame(
            frame,
            mech=mech,
            robot=robot,
            left_pick=left_pick,
            right_pick=right_pick,
            eye=eye,
            mine=mine,
        )
        path = SPRITE_DIR / f"robot-miner-frame-{frame:02d}.png"
        image.save(path)
        print(path.relative_to(ROOT), path.stat().st_size)


if __name__ == "__main__":
    generate()
