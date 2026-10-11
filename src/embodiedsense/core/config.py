from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml

from embodiedsense.core.types import CameraCalibration


@dataclass(frozen=True)
class PipelineConfig:
    camera: CameraCalibration
    max_delta_ms: float = 20.0
    min_depth_m: float = 0.2
    max_depth_m: float = 10.0
    stride: int = 4
    max_gap_ms: float = 100.0

    def __post_init__(self):
        values = [self.max_delta_ms, self.min_depth_m, self.max_depth_m, self.max_gap_ms]
        if not np.isfinite(values).all():
            raise ValueError("Thresholds must be finite")
        if self.max_delta_ms < 0 or self.max_gap_ms <= 0:
            raise ValueError("Invalid timestamp tolerance/gap threshold")
        if not 0 < self.min_depth_m < self.max_depth_m:
            raise ValueError("Invalid depth range")
        if not isinstance(self.stride, int) or self.stride < 1:
            raise ValueError("stride must be a positive integer")


def load_config(path: str | Path) -> PipelineConfig:
    """Load a pipeline configuration, giving clear errors instead of raw tracebacks.

    A malformed config (empty file, non-mapping root, missing ``camera`` block,
    or an invalid sub-key) previously surfaced as a cryptic ``AttributeError``,
    ``TypeError`` or bare ``KeyError`` that named neither the file nor the fix.
    """
    location = Path(path)
    try:
        text = location.read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError(f"cannot read config file {location}: {error}") from error
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise ValueError(f"invalid YAML in config file {location}: {error}") from error
    if data is None:
        raise ValueError(f"config file {location} is empty")
    if not isinstance(data, dict):
        raise ValueError(
            f"config file {location} must contain a mapping, got {type(data).__name__}"
        )
    if "camera" not in data:
        raise ValueError(f"config file {location} is missing the required 'camera' block")
    camera = CameraCalibration(**data.pop("camera"))
    return PipelineConfig(camera=camera, **data)
