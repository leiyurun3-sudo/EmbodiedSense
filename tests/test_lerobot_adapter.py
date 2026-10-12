"""Adapter tests. Synthetic fixture by default; real pusht subset when present."""

from __future__ import annotations

import hashlib
from pathlib import Path

import lerobot_fixture
import numpy as np
import pytest
from lerobot_fixture import (
    VIDEO_KEY,
    VideoEncoderUnavailable,
    build_lerobot_v3,
    grey_for_frame,
    mp4_encoder_available,
)

from embodiedsense.adapters import (
    DELIVERED,
    NOT_PRESENT,
    STREAM_VIDEO,
    LeRobotFormatError,
    LeRobotV3Dataset,
)

REAL_DATASET = Path(__file__).resolve().parents[1] / "data" / "pusht"

# OpenCV builds differ across platforms; tests needing a real chunk video skip
# instead of failing where no mp4 encoder exists.
VIDEO_OK = mp4_encoder_available()
needs_video = pytest.mark.skipif(not VIDEO_OK, reason="OpenCV cannot write mp4 here")


@pytest.fixture
def dataset_root(tmp_path) -> Path:
    return build_lerobot_v3(
        tmp_path / "dataset",
        episode_lengths=(4, 3),
        fps=5.0,
        size=16,
        write_video=VIDEO_OK,
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------- streams


def test_lists_every_declared_stream(dataset_root):
    dataset = LeRobotV3Dataset(dataset_root)
    keys = dataset.stream_keys()
    assert VIDEO_KEY in keys
    assert "observation.state" in keys
    assert "action" in keys
    assert "timestamp" in keys

    streams = {descriptor.key: descriptor for descriptor in dataset.streams()}
    video = streams[VIDEO_KEY]
    assert video.kind == STREAM_VIDEO
    assert video.video_codec == "mp4v"
    assert video.shape == (16, 16, 3)
    assert streams["observation.state"].kind == "numeric"
    assert streams["episode_index"].kind == "index"


def test_video_stream_is_not_stored_in_parquet(dataset_root):
    """The camera stream lives in a separate file; copying only parquet loses it."""
    dataset = LeRobotV3Dataset(dataset_root)
    streams = {descriptor.key: descriptor for descriptor in dataset.streams()}
    assert streams[VIDEO_KEY].in_parquet is False
    assert streams["observation.state"].in_parquet is True


# -------------------------------------------------------------------- episodes


def test_episode_enumeration(dataset_root):
    dataset = LeRobotV3Dataset(dataset_root)
    assert dataset.episode_indexes() == (0, 1)
    first = dataset.episode(0)
    second = dataset.episode(1)
    assert first.length == 4
    assert second.length == 3
    assert first.dataset_from_index == 0
    assert first.dataset_to_index == 4
    assert second.dataset_from_index == 4
    assert first.tasks and "T-shaped" in first.tasks[0]


def test_unknown_episode_raises(dataset_root):
    with pytest.raises(KeyError):
        LeRobotV3Dataset(dataset_root).episode(99)


# ------------------------------------------------------------ reading episodes


@needs_video
def test_read_episode_delivers_state_action_and_video(dataset_root):
    dataset = LeRobotV3Dataset(dataset_root)
    data = dataset.read_episode(0)
    assert data.frame_count == 4
    assert data.numeric["observation.state"].shape == (4, 2)
    assert data.numeric["action"].shape == (4, 2)
    assert data.images[VIDEO_KEY].shape == (4, 16, 16, 3)
    assert data.video_truncated is False
    delivered = {item.key: item.status for item in data.delivery}
    assert delivered[VIDEO_KEY] == DELIVERED
    assert delivered["observation.state"] == DELIVERED
    assert delivered["action"] == DELIVERED


def test_episode_timestamps_are_monotonic_and_relative(dataset_root):
    data = LeRobotV3Dataset(dataset_root).read_episode(1)
    assert data.frame_index.tolist() == [0, 1, 2]
    assert data.timestamp_s.tolist() == pytest.approx([0.0, 0.2, 0.4], abs=1e-6)
    assert np.all(np.diff(data.timestamp_s) > 0)
    # Relative to the episode start, not a wall clock: this must stay explicit.
    assert data.clock_domain == "episode_relative"
    assert data.timestamp_unit == "second"


@needs_video
def test_video_frames_match_episode_offsets(dataset_root):
    """Episode 1 must decode the frames the metadata points at, not frames 0-2."""
    dataset = LeRobotV3Dataset(dataset_root)
    first = dataset.read_episode(0).images[VIDEO_KEY]
    second = dataset.read_episode(1).images[VIDEO_KEY]

    expected_first = [grey_for_frame(index) for index in range(4)]
    expected_second = [grey_for_frame(index) for index in range(4, 7)]
    np.testing.assert_allclose([frame.mean() for frame in first], expected_first, atol=24)
    np.testing.assert_allclose([frame.mean() for frame in second], expected_second, atol=24)


@needs_video
def test_max_frames_bounds_decoded_memory(dataset_root):
    data = LeRobotV3Dataset(dataset_root).read_episode(0, max_frames=2)
    assert data.images[VIDEO_KEY].shape[0] == 2
    assert data.video_truncated is True
    # Metadata is still reported in full; only the decoded payload is bounded.
    assert data.frame_count == 4


@needs_video
def test_decode_video_false_reports_presence_without_decoding(dataset_root):
    data = LeRobotV3Dataset(dataset_root).read_episode(0, decode_video=False)
    assert data.images == {}
    delivered = {item.key: item.status for item in data.delivery}
    assert delivered[VIDEO_KEY] == DELIVERED


@needs_video
def test_reading_does_not_modify_the_dataset(dataset_root):
    dataset = LeRobotV3Dataset(dataset_root)
    parquet = dataset_root / "data" / "chunk-000" / "file-000.parquet"
    video = dataset_root / "videos" / VIDEO_KEY / "chunk-000" / "file-000.mp4"
    before = (_sha256(parquet), _sha256(video))
    dataset.read_episode(0)
    assert (_sha256(parquet), _sha256(video)) == before


# --------------------------------------------------- missing data is reported


@needs_video
def test_missing_video_is_reported_not_silently_dropped(tmp_path):
    root = build_lerobot_v3(tmp_path / "dataset", write_video=True)
    (root / "videos" / VIDEO_KEY / "chunk-000" / "file-000.mp4").unlink()

    dataset = LeRobotV3Dataset(root)
    manifest = {item.key: item for item in dataset.stream_manifest()}
    assert manifest[VIDEO_KEY].status == NOT_PRESENT
    assert manifest["observation.state"].status == DELIVERED

    data = dataset.read_episode(0)
    delivered = {item.key: item.status for item in data.delivery}
    assert delivered[VIDEO_KEY] == NOT_PRESENT
    # The numeric streams still arrive: a missing camera must not abort the read.
    assert delivered["observation.state"] == DELIVERED
    assert data.images == {}


def test_missing_data_file_is_reported(tmp_path):
    root = build_lerobot_v3(tmp_path / "dataset")
    (root / "data" / "chunk-000" / "file-000.parquet").unlink()
    dataset = LeRobotV3Dataset(root)
    with pytest.raises(LeRobotFormatError):
        dataset.read_episode(0)


# -------------------------------------------------------------------- metadata


def test_describe_reports_declared_and_listed_counts(dataset_root):
    summary = LeRobotV3Dataset(dataset_root).describe()
    assert summary["codebase_version"] == "v3.0"
    assert summary["declared_episodes"] == 2
    assert summary["listed_episodes"] == 2
    assert summary["listed_frames"] == 7
    assert summary["fps"] == 5.0


def test_non_v3_version_is_rejected(tmp_path):
    root = build_lerobot_v3(tmp_path / "dataset")
    info = root / "meta" / "info.json"
    info.write_text(info.read_text(encoding="utf-8").replace('"v3.0"', '"v2.1"'), encoding="utf-8")
    with pytest.raises(LeRobotFormatError):
        LeRobotV3Dataset(root)


def test_missing_metadata_is_rejected(tmp_path):
    with pytest.raises(LeRobotFormatError):
        LeRobotV3Dataset(tmp_path / "empty")


class _ClosedWriter:
    """Stands in for an OpenCV writer that this platform cannot open."""

    def isOpened(self) -> bool:
        return False

    def write(self, frame) -> None:
        raise AssertionError("write must not be reached when the writer is closed")

    def release(self) -> None:
        return None


def test_platform_without_mp4_encoder_is_detected(monkeypatch, tmp_path):
    """Where no mp4 encoder exists, the probe reports it and the builder refuses.

    Tests that need a chunk video skip on such a platform, so the suite keeps
    reporting real failures rather than environment-specific ones.
    """
    monkeypatch.setattr(lerobot_fixture.cv2, "VideoWriter", lambda *a, **k: _ClosedWriter())
    assert mp4_encoder_available() is False
    with pytest.raises(VideoEncoderUnavailable):
        build_lerobot_v3(tmp_path / "dataset", write_video=True)


# ------------------------------------------------------------- real dataset


@pytest.mark.skipif(not REAL_DATASET.is_dir(), reason="local pusht subset not downloaded")
def test_real_pusht_subset():
    """Verification against the real dataset, when the local subset exists."""
    dataset = LeRobotV3Dataset(REAL_DATASET)
    assert dataset.info["codebase_version"] == "v3.0"
    assert dataset.fps == 10.0
    assert len(dataset.episode_indexes()) == 206
    assert sum(record.length for record in dataset.episodes()) == 25650

    manifest = {item.key: item for item in dataset.stream_manifest()}
    assert manifest[VIDEO_KEY].status == DELIVERED
    # The real dataset stores the camera stream only as video.
    streams = {descriptor.key: descriptor for descriptor in dataset.streams()}
    assert streams[VIDEO_KEY].in_parquet is False
    assert streams[VIDEO_KEY].video_codec == "av1"

    first, second = dataset.read_episode(0), dataset.read_episode(1)
    assert first.frame_count == 161
    assert second.frame_count == 118
    assert first.numeric["observation.state"].shape == (161, 2)
    assert first.images[VIDEO_KEY].shape == (161, 96, 96, 3)
    # Episode 1 starts after episode 0 inside the same chunk video.
    assert first.record.video_slices[VIDEO_KEY].from_timestamp_s == 0.0
    assert second.record.video_slices[VIDEO_KEY].from_timestamp_s == pytest.approx(16.1)
    assert not np.array_equal(first.images[VIDEO_KEY][0], second.images[VIDEO_KEY][0])
