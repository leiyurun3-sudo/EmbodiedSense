"""Portable, self-contained recordings; original input is never modified."""

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from time import perf_counter, sleep

import numpy as np

from embodiedsense import __version__
from embodiedsense.core.config import PipelineConfig
from embodiedsense.core.types import CameraCalibration, QualityReport, RGBDObservation, SensorPacket


def packet_metadata(packet):
    if packet is None:
        return None
    return {
        name: getattr(packet, name) for name in packet.__dataclass_fields__ if name != "payload"
    }


def describe(obs):
    return {
        "rgb": packet_metadata(obs.rgb),
        "depth": packet_metadata(obs.depth),
        "quality": asdict(obs.quality),
        "sync_delta_ns": obs.sync_delta_ns,
        "point_count": len(obs.points_m),
    }


class RecordingWriter:
    def __init__(self, path: Path, config: PipelineConfig, source: str):
        self.path = path
        path.mkdir(parents=True, exist_ok=False)
        (path / "frames").mkdir()
        self.manifest = {
            "schema_version": "1.0",
            "software_version": __version__,
            "source_id": source,
            "config": asdict(config),
            "complete": False,
        }
        self._write_manifest()
        self.events = (path / "observations.jsonl").open("x", encoding="utf-8")
        self.count = 0

    def _write_manifest(self):
        (self.path / "manifest.json").write_text(
            json.dumps(self.manifest, indent=2, allow_nan=False), encoding="utf-8"
        )

    def write(self, obs):
        relative = f"frames/{self.count:06d}.npz"
        target = self.path / relative
        arrays = {"points_m": obs.points_m, "colors_rgb": obs.colors_rgb}
        if obs.rgb.payload is not None:
            arrays["rgb"] = obs.rgb.payload
        if obs.depth is not None and obs.depth.payload is not None:
            arrays["depth"] = obs.depth.payload
        np.savez_compressed(target, **arrays)
        row = describe(obs)
        row.update(payload_file=relative, sha256=hashlib.sha256(target.read_bytes()).hexdigest())
        self.events.write(json.dumps(row, allow_nan=False) + "\n")
        self.count += 1

    def close(self, complete=True):
        self.events.close()
        self.manifest.update(complete=complete, observations=self.count)
        self._write_manifest()


def replay(path: str | Path, speed=0.0):
    """Read an immutable recording. speed=0 means unpaced offline iteration."""
    if not np.isfinite(speed) or speed < 0:
        raise ValueError("speed must be finite and nonnegative")
    root = Path(path).resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "1.0" or not manifest.get("complete"):
        raise ValueError("Unsupported or incomplete recording")
    camera = CameraCalibration(**manifest["config"]["camera"])
    start = perf_counter()
    initial = None
    count = 0
    with (root / "observations.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            payload_path = (root / row["payload_file"]).resolve()
            if not payload_path.is_relative_to(root):
                raise ValueError("Recording payload escapes recording directory")
            if hashlib.sha256(payload_path.read_bytes()).hexdigest() != row["sha256"]:
                raise ValueError("Recording checksum mismatch")
            with np.load(payload_path, allow_pickle=False) as arrays:
                packets = []
                for modality in ("rgb", "depth"):
                    meta = row[modality]
                    if meta is None:
                        packets.append(None)
                    else:
                        meta["flags"] = tuple(meta["flags"])
                        payload = arrays[modality] if modality in arrays else None
                        if payload is not None:
                            payload.setflags(write=False)
                        packets.append(SensorPacket(**meta, payload=payload))
                timestamp = packets[0].timestamp_ns
                initial = timestamp if initial is None else initial
                if speed:
                    delay = (timestamp - initial) / 1e9 / speed - (perf_counter() - start)
                    if delay > 0:
                        sleep(delay)
                quality = row["quality"]
                quality["flags"] = tuple(quality["flags"])
                points, colors = arrays["points_m"], arrays["colors_rgb"]
                points.setflags(write=False)
                colors.setflags(write=False)
                yield RGBDObservation(
                    *packets, camera, QualityReport(**quality), row["sync_delta_ns"], points, colors
                )
                count += 1
    if count != manifest["observations"]:
        raise ValueError("Recording event count mismatch")


def write_ply(path: Path, points, colors):
    """Binary PLY for standard 3D applications."""
    points = np.asarray(points)
    colors = np.asarray(colors)
    if points.ndim != 2 or points.shape[1] != 3 or not np.isfinite(points).all():
        raise ValueError("Expected finite Nx3 points")
    if colors.ndim != 2 or colors.shape[1] != 3:
        raise ValueError("Expected Nx3 colors")
    if len(colors) != len(points):
        raise ValueError("points and colors must have the same length")
    vertices = np.empty(
        len(points),
        dtype=[
            ("x", "<f4"),
            ("y", "<f4"),
            ("z", "<f4"),
            ("red", "u1"),
            ("green", "u1"),
            ("blue", "u1"),
        ],
    )
    for index, name in enumerate(("x", "y", "z")):
        vertices[name] = points[:, index]
    for index, name in enumerate(("red", "green", "blue")):
        vertices[name] = colors[:, index]
    header = (
        f"ply\nformat binary_little_endian 1.0\nelement vertex {len(points)}\n"
        "property float x\nproperty float y\nproperty float z\n"
        "property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n"
    )
    with path.open("xb") as stream:
        stream.write(header.encode("ascii"))
        stream.write(vertices.tobytes())
