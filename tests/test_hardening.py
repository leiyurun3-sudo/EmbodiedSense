import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from embodiedsense.core.config import load_config
from embodiedsense.core.pipeline import RGBDPipeline
from embodiedsense.core.types import CameraCalibration, SensorPacket
from embodiedsense.processing.validation import validate
from embodiedsense.sensors.synthetic import make_fixture
from embodiedsense.storage import write_ply

CAMERA = CameraCalibration(64, 48, 60.0, 60.0, 31.5, 23.5, 5000.0, "test")


def _write(tmp_path, body: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_load_config_empty(tmp_path):
    with pytest.raises(ValueError, match="empty"):
        load_config(_write(tmp_path, ""))


def test_load_config_non_mapping(tmp_path):
    with pytest.raises(ValueError, match="mapping"):
        load_config(_write(tmp_path, "- a\n- b\n"))


def test_load_config_missing_camera(tmp_path):
    with pytest.raises(ValueError, match="camera"):
        load_config(_write(tmp_path, "stride: 4\n"))


def test_load_config_missing_file(tmp_path):
    with pytest.raises(ValueError, match="cannot read"):
        load_config(tmp_path / "nope.yaml")


def test_load_config_invalid_yaml(tmp_path):
    with pytest.raises(ValueError, match="invalid YAML"):
        load_config(_write(tmp_path, "camera: [unclosed\n"))


def test_load_config_valid(tmp_path):
    config = load_config("configs/synthetic.yaml")
    assert config.stride == 2 and config.camera.cx == 31.5


def test_principal_point_must_be_in_plane():
    with pytest.raises(ValueError, match="must lie inside"):
        CameraCalibration(64, 48, 60, 60, 5000.0, 23.5, 5000.0, "test")
    with pytest.raises(ValueError, match="must lie inside"):
        CameraCalibration(64, 48, 60, 60, 31.5, -5.0, 5000.0, "test")
    # Boundary and on-plane principal points are accepted.
    CameraCalibration(64, 48, 60, 60, 0.0, 48.0, 5000.0, "test")
    CameraCalibration(64, 48, 60, 60, 64.0, 0.0, 5000.0, "test")


def test_write_ply_length_mismatch(tmp_path):
    target = tmp_path / "o.ply"
    with pytest.raises(ValueError, match="same length"):
        write_ply(target, np.zeros((3, 3), np.float32), np.zeros((2, 3), np.uint8))
    assert not target.exists()


def test_write_ply_bad_colors_and_nonfinite(tmp_path):
    target = tmp_path / "o.ply"
    with pytest.raises(ValueError, match="Nx3 colors"):
        write_ply(target, np.zeros((3, 3), np.float32), np.zeros(3, np.uint8))
    with pytest.raises(ValueError, match="finite Nx3"):
        write_ply(target, np.full((3, 3), np.nan, np.float32), np.zeros((3, 3), np.uint8))


def _packet(payload):
    return SensorPacket(
        modality="rgb",
        sensor_id="rgb",
        source_id="test",
        timestamp_ns=0,
        original_timestamp="0",
        frame_id="camera_optical",
        calibration_id="test",
        payload_ref="img.png",
        sequence_id=0,
        payload=payload,
    )


def test_valid_depth_fraction_always_present():
    config = load_config("configs/synthetic.yaml")
    rgb = _packet(np.zeros((2, 2, 3), np.uint8))
    missing = validate(rgb, None, config)
    assert missing.status == "MISSING" and missing.metrics["valid_depth_fraction"] == 0.0
    malformed = _packet(np.zeros((2, 2), np.uint8))
    report = validate(rgb, malformed, config)
    assert report.metrics["valid_depth_fraction"] == 0.0


def test_summary_counts_and_coverage(tmp_path):
    data = tmp_path / "synthetic"
    make_fixture(data, frames=4)
    summary = RGBDPipeline(data, load_config("configs/synthetic.yaml")).summary()
    assert summary["rgb_frames"] == 4 and summary["depth_frames"] == 4
    assert summary["rgb_matched_fraction"] == 1.0
    assert summary["depth_reuse_count"] == 0
    assert summary["span_ns"] > 0


def test_summary_reports_reuse_and_anomalies(tmp_path):
    data = tmp_path / "synthetic"
    make_fixture(data, frames=4)
    lines = (data / "depth.txt").read_text().splitlines()
    (data / "depth.txt").write_text("\n".join(lines[:2]) + "\n")
    pipeline = RGBDPipeline(data, replace(load_config("configs/synthetic.yaml"), max_delta_ms=100))
    summary = pipeline.summary()
    # lines[:2] keeps only the "# SYNTHETIC DATA" header + depth frame 0,
    # so all 4 RGB frames reuse that one depth frame.
    assert summary["depth_frames"] == 1
    assert summary["rgb_matched_fraction"] == 1.0
    assert summary["depth_reuse_count"] == 3
    assert summary["rgb_unmatched"] == 0


def test_summary_counts_anomalies(tmp_path):
    data = tmp_path / "synthetic"
    make_fixture(data, frames=3)
    (data / "rgb.txt").write_text("1.0 rgb/0.png\n1.0 rgb/0.png\n0.5 rgb/0.png\n")
    summary = RGBDPipeline(data, load_config("configs/synthetic.yaml")).summary()
    assert summary["rgb_anomalies"]["DUPLICATE_TIMESTAMP"] == 1
    assert summary["rgb_anomalies"]["TIMESTAMP_BACKWARD"] == 1


def test_cli_summary(tmp_path):
    data = tmp_path / "synthetic"
    make_fixture(data, frames=3)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "embodiedsense.cli",
            "summary",
            "--config",
            "configs/synthetic.yaml",
            "--data-root",
            str(data),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["rgb_frames"] == 3 and payload["depth_frames"] == 3
