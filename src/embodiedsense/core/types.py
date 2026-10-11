from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class CameraCalibration:
    width: int
    height: int
    fx: float
    fy: float
    cx: float
    cy: float
    depth_scale: float
    calibration_id: str
    frame_id: str = "camera_optical"
    model: str = "registered_pinhole"

    def __post_init__(self):
        values = [self.fx, self.fy, self.cx, self.cy, self.depth_scale]
        if not np.isfinite(values).all() or min(self.fx, self.fy, self.depth_scale) <= 0:
            raise ValueError("Calibration must be finite with positive focal lengths/depth scale")
        if self.width <= 0 or self.height <= 0 or not self.calibration_id or not self.frame_id:
            raise ValueError("Calibration dimensions and identifiers are required")
        # A principal point (cx, cy) outside the image plane indicates a mis-entered
        # calibration and would silently skew every projected point cloud.
        if not 0 <= self.cx <= self.width or not 0 <= self.cy <= self.height:
            raise ValueError("Principal point (cx, cy) must lie inside the image plane")
        if self.model != "registered_pinhole":
            raise ValueError("MVP supports registered pinhole images only")


@dataclass(frozen=True)
class FrameRef:
    timestamp_ns: int
    original_timestamp: str
    path: Path
    relative_path: str
    sequence_id: int
    flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class SensorPacket:
    modality: str
    sensor_id: str
    source_id: str
    timestamp_ns: int
    original_timestamp: str
    frame_id: str
    calibration_id: str
    payload_ref: str
    sequence_id: int
    payload: np.ndarray | None = field(repr=False)
    clock_domain: str = "epoch"
    arrival_timestamp_ns: int | None = None
    schema_version: str = "1.0"
    flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class QualityReport:
    status: str
    flags: tuple[str, ...]
    metrics: dict[str, float]


@dataclass(frozen=True)
class RGBDObservation:
    rgb: SensorPacket
    depth: SensorPacket | None
    calibration: CameraCalibration
    quality: QualityReport
    sync_delta_ns: int | None
    points_m: np.ndarray = field(repr=False)
    colors_rgb: np.ndarray = field(repr=False)
