"""Build a minimal, format-faithful LeRobot v3 dataset for offline tests.

This fixture exists so the adapter tests never need the network or the multi-GB
public datasets. It is synthetic data and is labelled as such: values are a
deterministic pattern, not measurements.

The video frames encode the global frame index as a uniform grey level
(``index * 10 % 256``). That makes video seeking observable: a test can decode an
episode and check the grey level matches the frame the episode metadata points
at, which is exactly the episode -> video mapping that silently breaks in
cross-tool handoffs.
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

TASK = "Push the T-shaped block onto the T-shaped target."
VIDEO_KEY = "observation.image"
STATE_DIM = 2
FRAME_GREY_STEP = 10


class VideoEncoderUnavailable(RuntimeError):
    """Raised when this platform's OpenCV cannot write a chunk video."""


def mp4_encoder_available(size: int = 8, fps: float = 5.0) -> bool:
    """Probe whether an mp4 chunk video can be written here.

    OpenCV builds differ across platforms, so tests that need a real video file
    skip rather than fail when this returns False.
    """
    import tempfile

    try:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "probe.mp4"
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (size, size))
            if not writer.isOpened():
                writer.release()
                return False
            try:
                writer.write(np.zeros((size, size, 3), np.uint8))
            finally:
                writer.release()
            return path.is_file() and path.stat().st_size > 0
    except (cv2.error, OSError):
        return False


def _features(size: int, fps: float) -> dict:
    return {
        VIDEO_KEY: {
            "dtype": "video",
            "shape": [size, size, 3],
            "names": ["height", "width", "channel"],
            "video_info": {
                "video.fps": fps,
                "video.codec": "mp4v",
                "video.pix_fmt": "yuv420p",
                "video.is_depth_map": False,
                "has_audio": False,
            },
        },
        "observation.state": {
            "dtype": "float32",
            "shape": [STATE_DIM],
            "names": {"motors": ["motor_0", "motor_1"]},
            "fps": fps,
        },
        "action": {
            "dtype": "float32",
            "shape": [STATE_DIM],
            "names": {"motors": ["motor_0", "motor_1"]},
            "fps": fps,
        },
        "episode_index": {"dtype": "int64", "shape": [1], "names": None, "fps": fps},
        "frame_index": {"dtype": "int64", "shape": [1], "names": None, "fps": fps},
        "timestamp": {"dtype": "float32", "shape": [1], "names": None, "fps": fps},
        "next.reward": {"dtype": "float32", "shape": [1], "names": None, "fps": fps},
        "next.done": {"dtype": "bool", "shape": [1], "names": None, "fps": fps},
        "next.success": {"dtype": "bool", "shape": [1], "names": None, "fps": fps},
        "index": {"dtype": "int64", "shape": [1], "names": None, "fps": fps},
        "task_index": {"dtype": "int64", "shape": [1], "names": None, "fps": fps},
    }


def grey_for_frame(global_index: int) -> int:
    """Grey level written into the video for a given global frame index."""
    return (global_index * FRAME_GREY_STEP) % 256


def build_lerobot_v3(
    root: str | Path,
    *,
    episode_lengths: tuple[int, ...] = (4, 3),
    fps: float = 5.0,
    size: int = 16,
    write_video: bool = True,
) -> Path:
    """Create the fixture at ``root`` and return the resolved root path."""
    root = Path(root).resolve()
    (root / "meta" / "episodes" / "chunk-000").mkdir(parents=True, exist_ok=True)
    (root / "data" / "chunk-000").mkdir(parents=True, exist_ok=True)
    if write_video:
        (root / "videos" / VIDEO_KEY / "chunk-000").mkdir(parents=True, exist_ok=True)

    info = {
        "codebase_version": "v3.0",
        "robot_type": "synthetic_fixture",
        "total_episodes": len(episode_lengths),
        "total_frames": int(sum(episode_lengths)),
        "total_tasks": 1,
        "chunks_size": 1000,
        "fps": fps,
        "splits": {"train": f"0:{len(episode_lengths)}"},
        "data_path": "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
        "video_path": ("videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4"),
        "features": _features(size, fps),
        "data_files_size_in_mb": 0,
        "video_files_size_in_mb": 0,
    }
    (root / "meta" / "info.json").write_text(
        json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # ---- data parquet: one row per frame, in episode order ------------------
    states, actions, episode_index, frame_index, timestamps = [], [], [], [], []
    rewards, dones, successes, indexes, task_indexes = [], [], [], [], []
    global_index = 0
    for episode, length in enumerate(episode_lengths):
        for frame in range(length):
            states.append([float(frame), float(episode)])
            actions.append([float(frame) + 0.5, float(episode) + 0.25])
            episode_index.append(episode)
            frame_index.append(frame)
            timestamps.append(np.float32(frame / fps))
            rewards.append(0.0)
            dones.append(1.0 if frame == length - 1 else 0.0)
            successes.append(0.0)
            indexes.append(global_index)
            task_indexes.append(0)
            global_index += 1

    data_table = pa.table(
        {
            "observation.state": pa.array(states, pa.list_(pa.float32(), STATE_DIM)),
            "action": pa.array(actions, pa.list_(pa.float32(), STATE_DIM)),
            "episode_index": pa.array(episode_index, pa.int64()),
            "frame_index": pa.array(frame_index, pa.int64()),
            "timestamp": pa.array(timestamps, pa.float32()),
            "next.reward": pa.array(rewards, pa.float32()),
            "next.done": pa.array([bool(value) for value in dones], pa.bool_()),
            "next.success": pa.array([bool(value) for value in successes], pa.bool_()),
            "index": pa.array(indexes, pa.int64()),
            "task_index": pa.array(task_indexes, pa.int64()),
        }
    )
    pq.write_table(data_table, root / "data" / "chunk-000" / "file-000.parquet")

    # ---- episode metadata ---------------------------------------------------
    episode_rows = {
        "episode_index": [],
        "length": [],
        "dataset_from_index": [],
        "dataset_to_index": [],
        "data/chunk_index": [],
        "data/file_index": [],
        f"videos/{VIDEO_KEY}/chunk_index": [],
        f"videos/{VIDEO_KEY}/file_index": [],
        f"videos/{VIDEO_KEY}/from_timestamp": [],
        f"videos/{VIDEO_KEY}/to_timestamp": [],
        "tasks": [],
    }
    cursor = 0
    for episode, length in enumerate(episode_lengths):
        episode_rows["episode_index"].append(episode)
        episode_rows["length"].append(length)
        episode_rows["dataset_from_index"].append(cursor)
        episode_rows["dataset_to_index"].append(cursor + length)
        episode_rows["data/chunk_index"].append(0)
        episode_rows["data/file_index"].append(0)
        episode_rows[f"videos/{VIDEO_KEY}/chunk_index"].append(0)
        episode_rows[f"videos/{VIDEO_KEY}/file_index"].append(0)
        episode_rows[f"videos/{VIDEO_KEY}/from_timestamp"].append(cursor / fps)
        episode_rows[f"videos/{VIDEO_KEY}/to_timestamp"].append((cursor + length) / fps)
        episode_rows["tasks"].append([TASK])
        cursor += length

    episode_table = pa.table(
        {
            "episode_index": pa.array(episode_rows["episode_index"], pa.int64()),
            "length": pa.array(episode_rows["length"], pa.int64()),
            "dataset_from_index": pa.array(episode_rows["dataset_from_index"], pa.int64()),
            "dataset_to_index": pa.array(episode_rows["dataset_to_index"], pa.int64()),
            "data/chunk_index": pa.array(episode_rows["data/chunk_index"], pa.int64()),
            "data/file_index": pa.array(episode_rows["data/file_index"], pa.int64()),
            f"videos/{VIDEO_KEY}/chunk_index": pa.array(
                episode_rows[f"videos/{VIDEO_KEY}/chunk_index"], pa.int64()
            ),
            f"videos/{VIDEO_KEY}/file_index": pa.array(
                episode_rows[f"videos/{VIDEO_KEY}/file_index"], pa.int64()
            ),
            f"videos/{VIDEO_KEY}/from_timestamp": pa.array(
                episode_rows[f"videos/{VIDEO_KEY}/from_timestamp"], pa.float64()
            ),
            f"videos/{VIDEO_KEY}/to_timestamp": pa.array(
                episode_rows[f"videos/{VIDEO_KEY}/to_timestamp"], pa.float64()
            ),
            "tasks": pa.array([[TASK]] * len(episode_lengths), pa.list_(pa.string())),
        }
    )
    pq.write_table(episode_table, root / "meta" / "episodes" / "chunk-000" / "file-000.parquet")

    tasks_table = pa.table(
        {"task_index": pa.array([0], pa.int64()), "task": pa.array([TASK], pa.string())}
    )
    pq.write_table(tasks_table, root / "meta" / "tasks.parquet")
    (root / "meta" / "stats.json").write_text(
        json.dumps({"synthetic_fixture": True}, indent=2), encoding="utf-8"
    )

    # ---- chunk video: one continuous file, episodes concatenated ------------
    if write_video:
        video_path = root / "videos" / VIDEO_KEY / "chunk-000" / "file-000.mp4"
        writer = cv2.VideoWriter(
            str(video_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (size, size)
        )
        if not writer.isOpened():  # pragma: no cover - environment dependent
            raise VideoEncoderUnavailable("this platform's OpenCV cannot write an mp4 chunk video")
        try:
            for index in range(int(sum(episode_lengths))):
                grey = grey_for_frame(index)
                writer.write(np.full((size, size, 3), grey, np.uint8))
        finally:
            writer.release()

    return root
