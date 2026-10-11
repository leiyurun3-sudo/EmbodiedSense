import numpy as np

from embodiedsense.core.config import PipelineConfig
from embodiedsense.core.types import QualityReport, SensorPacket


def validate(rgb: SensorPacket, depth: SensorPacket | None, config: PipelineConfig):
    flags = list(rgb.flags)
    metrics = {}
    invalid = False
    shape = (config.camera.height, config.camera.width)
    if rgb.payload is None:
        invalid = True
    elif rgb.payload.shape != (*shape, 3) or rgb.payload.dtype != np.uint8:
        flags.append("RGB_FORMAT_OR_SIZE")
        invalid = True
    if depth is None:
        flags.append("UNSYNCED")
        metrics["valid_depth_fraction"] = 0.0
        return QualityReport("MISSING", tuple(dict.fromkeys(flags)), metrics)
    flags.extend(depth.flags)
    if depth.payload is None:
        invalid = True
        metrics["valid_depth_fraction"] = 0.0
    elif depth.payload.shape != shape or depth.payload.dtype != np.uint16:
        flags.append("DEPTH_FORMAT_OR_SIZE")
        metrics["valid_depth_fraction"] = 0.0
        invalid = True
        if np.issubdtype(depth.payload.dtype, np.number) and not np.isfinite(depth.payload).all():
            flags.append("NONFINITE_DEPTH")
    else:
        values = depth.payload.astype(np.float32) / config.camera.depth_scale
        valid = (
            np.isfinite(values) & (values >= config.min_depth_m) & (values <= config.max_depth_m)
        )
        metrics["valid_depth_fraction"] = float(valid.mean())
        if not valid.all():
            flags.append("INVALID_DEPTH_PIXELS")
        if not valid.any():
            flags.append("NO_VALID_DEPTH")
            invalid = True
    return QualityReport(
        "INVALID" if invalid else ("WARN" if flags else "OK"),
        tuple(dict.fromkeys(flags)),
        metrics,
    )
