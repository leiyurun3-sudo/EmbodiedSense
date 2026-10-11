from collections import Counter
from dataclasses import replace
from pathlib import Path

import numpy as np

from embodiedsense.core.config import PipelineConfig
from embodiedsense.core.types import RGBDObservation
from embodiedsense.processing.spatial import project_depth
from embodiedsense.processing.sync import associate
from embodiedsense.processing.validation import validate
from embodiedsense.sensors.rgbd_loader import load_packet, read_index


class RGBDPipeline:
    """Lazy image decoding; holds metadata indexes, at most one RGB-D pair in core."""

    def __init__(self, data_root: str | Path, config: PipelineConfig):
        self.root = Path(data_root).resolve()
        self.config = config
        gap = int(config.max_gap_ms * 1_000_000)
        self.rgb_refs = read_index(self.root, "rgb.txt", gap)
        self.depth_refs = read_index(self.root, "depth.txt", gap)

    def summary(self) -> dict:
        """Lightweight dataset health summary that never decodes an image.

        Reuses the index reader to report stream sizes, span, anomalies and
        sync coverage, so a dataset can be sanity-checked before a full
        (image-decoding) run on costly data.
        """
        rgb, depth = self.rgb_refs, self.depth_refs
        span_ns = rgb[-1].timestamp_ns - rgb[0].timestamp_ns if rgb else 0

        def anomalies(stream):
            return Counter(f for ref in stream for f in ref.flags)

        pairs = list(associate(rgb, depth, int(self.config.max_delta_ms * 1_000_000)))
        matched = sum(1 for _, depth_ref in pairs if depth_ref is not None)
        distinct_depth = len({d.sequence_id for _, d in pairs if d is not None})
        return {
            "rgb_frames": len(rgb),
            "depth_frames": len(depth),
            "span_ns": span_ns,
            "rgb_unmatched": len(pairs) - matched,
            "rgb_anomalies": dict(anomalies(rgb)),
            "depth_anomalies": dict(anomalies(depth)),
            "rgb_matched_fraction": matched / len(pairs) if pairs else 0.0,
            "depth_reuse_count": max(0, matched - distinct_depth),
        }

    def iter_observations(self, limit: int | None = None):
        if limit is not None and limit <= 0:
            raise ValueError("limit must be positive")
        config = self.config
        used = set()
        for index, (rgb_ref, depth_ref) in enumerate(
            associate(self.rgb_refs, self.depth_refs, int(config.max_delta_ms * 1_000_000))
        ):
            if limit is not None and index >= limit:
                break
            rgb = load_packet(rgb_ref, "rgb", self.root.name, config.camera)
            depth = None
            delta = None
            if depth_ref is not None:
                depth = load_packet(depth_ref, "depth", self.root.name, config.camera)
                delta = depth.timestamp_ns - rgb.timestamp_ns
                if depth_ref.sequence_id in used:
                    depth = replace(depth, flags=depth.flags + ("DEPTH_REUSED",))
                used.add(depth_ref.sequence_id)
            quality = validate(rgb, depth, config)
            points = np.empty((0, 3), dtype=np.float32)
            colors = np.empty((0, 3), dtype=np.uint8)
            if quality.status in ("OK", "WARN"):
                points, colors = project_depth(
                    depth.payload,
                    rgb.payload,
                    config.camera,
                    config.min_depth_m,
                    config.max_depth_m,
                    config.stride,
                )
            points.setflags(write=False)
            colors.setflags(write=False)
            yield RGBDObservation(rgb, depth, config.camera, quality, delta, points, colors)
