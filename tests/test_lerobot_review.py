"""Regression cases found by review; all modified inputs are synthetic fixtures."""

import json
from dataclasses import replace

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from lerobot_fixture import VIDEO_KEY, build_lerobot_v3

from embodiedsense.adapters import (
    NOT_PRESENT,
    PARTIAL,
    PRESENT,
    UNAVAILABLE,
    LeRobotFormatError,
    LeRobotV3Dataset,
)


@pytest.fixture
def root(tmp_path):
    return build_lerobot_v3(tmp_path / "dataset", write_video=False)


def data_path(root):
    return root / "data/chunk-000/file-000.parquet"


def update_info(root, **values):
    path = root / "meta/info.json"
    info = json.loads(path.read_text())
    info.update(values)
    path.write_text(json.dumps(info))


@pytest.mark.parametrize("version", ["v30", "v3anything", "v3", "v2.1"])
def test_version_is_not_a_prefix(root, version):
    update_info(root, codebase_version=version)
    with pytest.raises(LeRobotFormatError, match="codebase_version"):
        LeRobotV3Dataset(root)


@pytest.mark.parametrize("fps", [0, -1, float("nan"), float("inf")])
def test_invalid_fps(root, fps):
    update_info(root, fps=fps)
    with pytest.raises(LeRobotFormatError, match="fps"):
        LeRobotV3Dataset(root)


def test_missing_frame_index_is_not_invented(root):
    table = pq.read_table(data_path(root)).drop(["frame_index"])
    pq.write_table(table, data_path(root))
    with pytest.raises(LeRobotFormatError, match="frame_index"):
        LeRobotV3Dataset(root).read_episode(0, decode_video=False)


def test_row_count_is_checked_before_video(root):
    table = pq.read_table(data_path(root))
    pq.write_table(table.slice(1), data_path(root))
    with pytest.raises(LeRobotFormatError, match="rows differ"):
        LeRobotV3Dataset(root).read_episode(0)


@pytest.mark.parametrize(
    "column,values",
    [
        ("frame_index", [0, 0, 2, 3, 0, 1, 2]),
        ("timestamp", [0, 0.2, 0.1, 0.6, 0, 0.2, 0.4]),
    ],
)
def test_repeated_index_and_backward_time_are_not_aligned(root, column, values):
    table = pq.read_table(data_path(root))
    array = pa.array(values, type=table.schema.field(column).type)
    table = table.set_column(table.schema.get_field_index(column), column, array)
    pq.write_table(table, data_path(root))
    with pytest.raises(LeRobotFormatError):
        LeRobotV3Dataset(root).read_episode(0)


@pytest.mark.parametrize(
    "values",
    [
        [None] * 7,
        [[float("nan"), 0]] * 7,
        [[1], [1, 2], [1], [1], [1], [1], [1]],
    ],
)
def test_bad_optional_values_do_not_claim_delivery(root, values):
    table = pq.read_table(data_path(root))
    table = table.set_column(
        table.schema.get_field_index("action"),
        "action",
        pa.array(values, type=pa.list_(pa.float32())),
    )
    pq.write_table(table, data_path(root))
    dataset = LeRobotV3Dataset(root)
    data = dataset.read_episode(0, decode_video=False)
    deliveries = {item.key: item for item in data.delivery}
    assert deliveries["action"].status == UNAVAILABLE
    assert "action" not in data.numeric
    assert data.numeric["observation.state"].dtype == np.float32
    manifest = {item.key: item for item in dataset.stream_manifest()}
    assert manifest["action"].status == UNAVAILABLE
    assert manifest["action"].delivered_episodes == 0


def test_missing_numeric_column_has_zero_delivered_episodes(root):
    table = pq.read_table(data_path(root)).drop(["action"])
    pq.write_table(table, data_path(root))
    item = {item.key: item for item in LeRobotV3Dataset(root).stream_manifest()}["action"]
    assert item.status == NOT_PRESENT
    assert item.delivered_episodes == item.read_frames == 0


def test_multiple_chunks_do_not_use_first_file_schema(root):
    meta_path = root / "meta/episodes/chunk-000/file-000.parquet"
    meta = pq.read_table(meta_path)
    data = pq.read_table(data_path(root))
    second_path = root / "data/chunk-001/file-000.parquet"
    second_path.parent.mkdir()
    pq.write_table(data.slice(4), second_path)
    pq.write_table(data.slice(0, 4).drop(["action"]), data_path(root))
    meta = meta.set_column(
        meta.schema.get_field_index("data/chunk_index"),
        "data/chunk_index",
        pa.array([0, 1], pa.int64()),
    )
    pq.write_table(meta.slice(0, 1), meta_path)
    second_meta = root / "meta/episodes/chunk-001/file-000.parquet"
    second_meta.parent.mkdir()
    pq.write_table(meta.slice(1), second_meta)
    dataset = LeRobotV3Dataset(root)
    assert next(item for item in dataset.streams() if item.key == "action").in_parquet
    manifest = {item.key: item for item in dataset.stream_manifest()}
    assert manifest["action"].status == UNAVAILABLE
    assert manifest["action"].delivered_episodes == 1
    assert "action" in dataset.read_episode(1, decode_video=False).numeric
    assert "action" not in dataset.read_episode(0, decode_video=False).numeric
    second_path.unlink()
    assert {x.key: x for x in dataset.stream_manifest()}[
        "observation.state"
    ].delivered_episodes == 1


def test_path_escape_is_rejected(root):
    update_info(root, data_path="../outside/file-{file_index}.parquet")
    with pytest.raises(LeRobotFormatError, match="escapes"):
        LeRobotV3Dataset(root).episodes()


def test_null_video_slice_is_missing_not_a_typeerror(root):
    path = root / "meta/episodes/chunk-000/file-000.parquet"
    table = pq.read_table(path)
    key = f"videos/{VIDEO_KEY}/from_timestamp"
    table = table.set_column(table.schema.get_field_index(key), key, pa.array([None, 0.8]))
    pq.write_table(table, path)
    assert VIDEO_KEY not in LeRobotV3Dataset(root).episode(0).video_slices


class Capture:
    """Lossless fake CFR decoder; set() always fails and must never be used."""

    def __init__(self, count=7):
        self.position = 0
        self.count = count
        self.released = False

    def isOpened(self):
        return True

    def get(self, prop):
        return 5.0

    def set(self, *args):
        raise AssertionError("unchecked seeking must not be used")

    def grab(self):
        if self.position >= self.count:
            return False
        self.position += 1
        return True

    def read(self):
        if self.position >= self.count:
            return False, None
        frame = np.full((16, 16, 3), self.position, np.uint8)
        self.position += 1
        return True, frame

    def release(self):
        self.released = True


def setup_capture(root, monkeypatch, count=7):
    import embodiedsense.adapters.lerobot_v3 as adapter

    dataset = LeRobotV3Dataset(root)
    path = dataset.episode(0).video_slices[VIDEO_KEY].path
    path.parent.mkdir(parents=True)
    path.write_bytes(b"SYNTHETIC mock video")
    instances = []

    def create(path):
        capture = Capture(count)
        instances.append(capture)
        return capture

    monkeypatch.setattr(adapter.cv2, "VideoCapture", create)
    return dataset, instances


def test_exact_sequential_episode_offset(root, monkeypatch):
    dataset, instances = setup_capture(root, monkeypatch)
    data = dataset.read_episode(1)
    np.testing.assert_array_equal(data.images[VIDEO_KEY][:, 0, 0, 0], [4, 5, 6])
    assert instances[0].released


def test_present_preview_partial_and_corruption_are_distinct(root, monkeypatch):
    dataset, _ = setup_capture(root, monkeypatch, count=2)
    manifest = {item.key: item for item in dataset.stream_manifest()}[VIDEO_KEY]
    assert manifest.status == PRESENT and manifest.delivered_episodes == 0
    data = dataset.read_episode(0)
    delivery = next(item for item in data.delivery if item.key == VIDEO_KEY)
    assert delivery.status == PARTIAL
    assert delivery.read_frames == 2 and delivery.expected_frames == 4
    assert delivery.delivered_episodes == 0
    data = dataset.read_episode(0, decode_video=False)
    assert next(item for item in data.delivery if item.key == VIDEO_KEY).status == PRESENT


def test_intentional_preview_is_not_complete_delivery(root, monkeypatch):
    dataset, _ = setup_capture(root, monkeypatch)
    data = dataset.read_episode(0, max_frames=2)
    delivery = next(item for item in data.delivery if item.key == VIDEO_KEY)
    assert delivery.status == PARTIAL and delivery.read_frames == 2
    assert data.video_truncated


def test_short_slice_cannot_read_into_next_episode(root, monkeypatch):
    dataset, _ = setup_capture(root, monkeypatch)
    video = dataset.episode(0).video_slices[VIDEO_KEY]
    frames, problem, _ = dataset._decode_video(replace(video, to_timestamp_s=0.2), 4, None)
    assert len(frames) == 0 and "boundaries" in problem


def test_per_camera_fps_is_explicitly_checked(root, monkeypatch):
    dataset, _ = setup_capture(root, monkeypatch)
    dataset.features[VIDEO_KEY]["video_info"]["video.fps"] = 10
    video = dataset.episode(0).video_slices[VIDEO_KEY]
    frames, problem, _ = dataset._decode_video(replace(video, to_timestamp_s=0.4), 4, None)
    assert len(frames) == 0 and "sampling map" in problem


def test_bad_required_metadata_has_domain_error(root):
    path = root / "meta/episodes/chunk-000/file-000.parquet"
    table = pq.read_table(path)
    table = table.set_column(
        table.schema.get_field_index("length"), "length", pa.array([None, 3], pa.int64())
    )
    pq.write_table(table, path)
    with pytest.raises(LeRobotFormatError, match="metadata integer"):
        LeRobotV3Dataset(root).episodes()


def test_two_video_chunks_have_independent_offsets(root, monkeypatch):
    import embodiedsense.adapters.lerobot_v3 as adapter

    meta_path = root / "meta/episodes/chunk-000/file-000.parquet"
    table = pq.read_table(meta_path)
    changes = {
        f"videos/{VIDEO_KEY}/chunk_index": pa.array([0, 1], pa.int64()),
        f"videos/{VIDEO_KEY}/from_timestamp": pa.array([0.0, 0.0]),
        f"videos/{VIDEO_KEY}/to_timestamp": pa.array([0.8, 0.6]),
    }
    for key, array in changes.items():
        table = table.set_column(table.schema.get_field_index(key), key, array)
    pq.write_table(table, meta_path)
    dataset = LeRobotV3Dataset(root)
    for record in dataset.episodes():
        path = record.video_slices[VIDEO_KEY].path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"SYNTHETIC mock video")

    class ChunkCapture(Capture):
        def __init__(self, second):
            super().__init__(3 if second else 4)
            self.offset = 4 if second else 0

        def read(self):
            ok, frame = super().read()
            return ok, frame + self.offset if ok else None

    monkeypatch.setattr(
        adapter.cv2, "VideoCapture", lambda path: ChunkCapture("chunk-001" in str(path))
    )
    first, second = dataset.read_episode(0), dataset.read_episode(1)
    np.testing.assert_array_equal(first.images[VIDEO_KEY][:, 0, 0, 0], [0, 1, 2, 3])
    np.testing.assert_array_equal(second.images[VIDEO_KEY][:, 0, 0, 0], [4, 5, 6])
