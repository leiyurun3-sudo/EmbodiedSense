"""LeRobot v3 dataset adapter: read the on-disk format directly.

Why this module reads files instead of importing ``lerobot``: the official package
pulls in the training stack (torch and friends), while EmbodiedSense Core is
CPU-first and model-independent. The v3 layout is read here with ``pyarrow`` and
OpenCV only, so the adapter stays inside the project's dependency discipline.

Format facts recorded from a real dataset (``lerobot/pusht``, v3.0), not assumed:

* ``meta/info.json`` declares every feature, including ``observation.image`` with
  ``dtype == "video"``.
* Numeric features (``observation.state``, ``action``, ...) live in the data
  parquet. Video features do **not**: they live in a separate ``.mp4`` per chunk.
  A converter that copies only the parquet therefore drops the camera stream
  silently, which is the handoff defect this project measures.
* ``timestamp`` is **seconds relative to the start of each episode**, not an
  epoch clock. The adapter records that clock domain explicitly and never
  pretends the value is wall-clock time.
* Each episode's slice of a chunk video is given by
  ``videos/<key>/from_timestamp`` and ``to_timestamp``; the first frame index is
  ``round(from_timestamp * fps)``.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

STREAM_VIDEO = "video"
STREAM_NUMERIC = "numeric"
STREAM_INDEX = "index"

DELIVERED = "DELIVERED"
NOT_PRESENT = "NOT_PRESENT"
UNAVAILABLE = "UNAVAILABLE"

# Bookkeeping counters, not measurements.
INDEX_FEATURES = frozenset({"episode_index", "frame_index", "index", "task_index"})

VIDEO_SLICE_COLUMNS = ("chunk_index", "file_index", "from_timestamp", "to_timestamp")

REQUIRED_EPISODE_COLUMNS = (
    "episode_index",
    "length",
    "dataset_from_index",
    "dataset_to_index",
    "data/chunk_index",
    "data/file_index",
)


class LeRobotFormatError(ValueError):
    """Raised when a directory does not look like a readable LeRobot v3 dataset."""


@dataclass(frozen=True)
class StreamDescriptor:
    """One declared feature, with where its payload actually lives."""

    key: str
    kind: str
    dtype: str
    shape: tuple[int, ...]
    fps: float | None
    names: tuple[str, ...] | None
    video_codec: str | None
    in_parquet: bool
    video_file: Path | None

    @property
    def is_video(self) -> bool:
        return self.kind == STREAM_VIDEO


@dataclass(frozen=True)
class VideoSlice:
    """Where one episode's frames sit inside a chunk video."""

    stream_key: str
    path: Path
    from_timestamp_s: float
    to_timestamp_s: float

    def start_frame(self, fps: float) -> int:
        return int(round(self.from_timestamp_s * fps))


@dataclass(frozen=True)
class EpisodeRecord:
    """Episode boundaries taken from the dataset's own metadata."""

    episode_index: int
    length: int
    dataset_from_index: int
    dataset_to_index: int
    tasks: tuple[str, ...]
    data_file: Path
    video_slices: dict[str, VideoSlice]


@dataclass(frozen=True)
class StreamDelivery:
    """Whether one stream actually reached the reader, and why not if it did not.

    ``NOT_PRESENT`` means the stream is declared but no payload was found on
    disk. It is deliberately distinct from a detection failure: a missing stream
    must never be counted as a clean read.
    """

    key: str
    status: str
    reason: str
    delivered_episodes: int = 0
    total_episodes: int = 0


@dataclass(frozen=True)
class EpisodeData:
    """One episode, read but not converted: original names, units and timestamps."""

    record: EpisodeRecord
    timestamp_s: np.ndarray
    frame_index: np.ndarray
    numeric: dict[str, np.ndarray]
    images: dict[str, np.ndarray]
    delivery: tuple[StreamDelivery, ...]
    video_truncated: bool
    clock_domain: str = "episode_relative"
    timestamp_unit: str = "second"
    units: str = "dataset_defined"

    @property
    def frame_count(self) -> int:
        return int(self.timestamp_s.shape[0])


def _require_pyarrow():
    try:
        import pyarrow.parquet as pq
    except ImportError as error:  # pragma: no cover - exercised only without the extra
        raise LeRobotFormatError(
            "Reading LeRobot v3 datasets needs pyarrow. Install it with "
            "`uv sync --extra lerobot` (or `pip install pyarrow`)."
        ) from error
    return pq


def _shape_tuple(spec: dict[str, Any]) -> tuple[int, ...]:
    shape = spec.get("shape") or []
    return tuple(int(value) for value in shape)


def _names_tuple(spec: dict[str, Any]) -> tuple[str, ...] | None:
    names = spec.get("names")
    if isinstance(names, dict):
        flat: list[str] = []
        for value in names.values():
            if isinstance(value, list):
                flat.extend(str(item) for item in value)
            else:
                flat.append(str(value))
        return tuple(flat) if flat else None
    if isinstance(names, list):
        return tuple(str(item) for item in names)
    return None


class LeRobotV3Dataset:
    """Read-only view of a LeRobot v3 dataset directory.

    The adapter never writes to the dataset. Everything it reports keeps the
    original feature names so a downstream consumer can trace a value back to the
    file it came from.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        info_path = self.root / "meta" / "info.json"
        if not info_path.is_file():
            raise LeRobotFormatError(f"missing dataset metadata: {info_path}")
        import json

        try:
            self.info: dict[str, Any] = json.loads(info_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise LeRobotFormatError(f"unreadable dataset metadata: {info_path}") from error

        version = str(self.info.get("codebase_version", ""))
        if not version.startswith("v3"):
            raise LeRobotFormatError(f"unsupported codebase_version: {version!r} (expected v3.x)")
        features = self.info.get("features")
        if not isinstance(features, dict) or not features:
            raise LeRobotFormatError("dataset metadata declares no features")
        self.features: dict[str, Any] = features
        self.fps = float(self.info.get("fps", 0.0)) or None
        self._episode_files = sorted((self.root / "meta" / "episodes").rglob("*.parquet"))
        if not self._episode_files:
            raise LeRobotFormatError("no episode metadata under meta/episodes")
        self._records: tuple[EpisodeRecord, ...] | None = None

    # ---------------------------------------------------------------- streams

    def _data_template(self) -> str:
        template = self.info.get("data_path")
        if not isinstance(template, str) or not template:
            raise LeRobotFormatError("dataset metadata has no data_path template")
        return template

    def _video_template(self) -> str:
        template = self.info.get("video_path")
        if not isinstance(template, str) or not template:
            raise LeRobotFormatError("dataset metadata has no video_path template")
        return template

    def data_file_for(self, chunk_index: int, file_index: int) -> Path:
        relative = self._data_template().format(chunk_index=chunk_index, file_index=file_index)
        return self.root / relative

    def video_file_for(self, stream_key: str, chunk_index: int, file_index: int) -> Path:
        relative = self._video_template().format(
            video_key=stream_key, chunk_index=chunk_index, file_index=file_index
        )
        return self.root / relative

    def _parquet_columns(self) -> set[str]:
        """Columns of the first available data parquet, used for presence checks."""
        pq = _require_pyarrow()
        for record in self.episodes():
            if record.data_file.is_file():
                try:
                    return set(pq.ParquetFile(record.data_file).schema_arrow.names)
                except (OSError, ValueError):
                    return set()
        return set()

    def streams(self) -> tuple[StreamDescriptor, ...]:
        """Every declared feature, classified by where its payload lives."""
        columns = self._parquet_columns()
        descriptors = []
        for key, spec in self.features.items():
            spec = spec if isinstance(spec, dict) else {}
            dtype = str(spec.get("dtype", "unknown"))
            is_video = dtype == STREAM_VIDEO
            if is_video:
                kind = STREAM_VIDEO
            elif key in INDEX_FEATURES:
                kind = STREAM_INDEX
            else:
                kind = STREAM_NUMERIC
            video_info = spec.get("video_info") if isinstance(spec.get("video_info"), dict) else {}
            descriptors.append(
                StreamDescriptor(
                    key=key,
                    kind=kind,
                    dtype=dtype,
                    shape=_shape_tuple(spec),
                    fps=float(spec["fps"]) if isinstance(spec.get("fps"), (int, float)) else None,
                    names=_names_tuple(spec),
                    video_codec=(
                        str(video_info["video.codec"]) if "video.codec" in video_info else None
                    ),
                    in_parquet=key in columns,
                    video_file=None,
                )
            )
        return tuple(descriptors)

    def stream_keys(self) -> tuple[str, ...]:
        return tuple(self.features)

    def video_stream_keys(self) -> tuple[str, ...]:
        return tuple(
            key for key, spec in self.features.items() if spec.get("dtype") == STREAM_VIDEO
        )

    # --------------------------------------------------------------- episodes

    def _read_episode_metadata(self) -> tuple[EpisodeRecord, ...]:
        pq = _require_pyarrow()
        records: list[EpisodeRecord] = []
        for path in self._episode_files:
            schema = pq.ParquetFile(path).schema_arrow.names
            missing = [name for name in REQUIRED_EPISODE_COLUMNS if name not in schema]
            if missing:
                raise LeRobotFormatError(f"{path.name} lacks episode columns: {missing}")
            wanted = [name for name in REQUIRED_EPISODE_COLUMNS if name in schema]
            if "tasks" in schema:
                wanted.append("tasks")
            video_columns = {
                key: [
                    f"videos/{key}/{name}"
                    for name in VIDEO_SLICE_COLUMNS
                    if f"videos/{key}/{name}" in schema
                ]
                for key in self.video_stream_keys()
            }
            for key, columns in video_columns.items():
                wanted.extend(columns)
            table = pq.read_table(path, columns=sorted(set(wanted)))
            for row in table.to_pylist():
                slices: dict[str, VideoSlice] = {}
                for key, columns in video_columns.items():
                    present = {name.split("/")[-1] for name in columns}
                    if not present.issuperset(VIDEO_SLICE_COLUMNS):
                        continue
                    slices[key] = VideoSlice(
                        stream_key=key,
                        path=self.video_file_for(
                            key,
                            int(row[f"videos/{key}/chunk_index"]),
                            int(row[f"videos/{key}/file_index"]),
                        ),
                        from_timestamp_s=float(row[f"videos/{key}/from_timestamp"]),
                        to_timestamp_s=float(row[f"videos/{key}/to_timestamp"]),
                    )
                tasks = row.get("tasks") or []
                records.append(
                    EpisodeRecord(
                        episode_index=int(row["episode_index"]),
                        length=int(row["length"]),
                        dataset_from_index=int(row["dataset_from_index"]),
                        dataset_to_index=int(row["dataset_to_index"]),
                        tasks=tuple(str(task) for task in tasks),
                        data_file=self.data_file_for(
                            int(row["data/chunk_index"]), int(row["data/file_index"])
                        ),
                        video_slices=slices,
                    )
                )
        if not records:
            raise LeRobotFormatError("episode metadata contains no rows")
        records.sort(key=lambda record: record.episode_index)
        return tuple(records)

    def episodes(self) -> tuple[EpisodeRecord, ...]:
        if self._records is None:
            self._records = self._read_episode_metadata()
        return self._records

    def episode_indexes(self) -> tuple[int, ...]:
        return tuple(record.episode_index for record in self.episodes())

    def episode(self, episode_index: int) -> EpisodeRecord:
        for record in self.episodes():
            if record.episode_index == episode_index:
                return record
        raise KeyError(f"episode {episode_index} is not in this dataset")

    def stream_manifest(self) -> tuple[StreamDelivery, ...]:
        """Dataset-level check of which declared streams can actually be read.

        This is the guard against silent stream loss: a video stream that is
        declared in ``info.json`` but has no file on disk is reported as
        ``NOT_PRESENT`` rather than disappearing from the output.
        """
        records = self.episodes()
        columns = self._parquet_columns()
        total = len(records)
        deliveries = []
        for key, spec in self.features.items():
            if spec.get("dtype") == STREAM_VIDEO:
                available = sum(
                    1
                    for record in records
                    if (slice_ := record.video_slices.get(key)) is not None
                    and slice_.path.is_file()
                )
                if available == total:
                    status, reason = DELIVERED, "chunk video present for every episode"
                elif available == 0:
                    status, reason = NOT_PRESENT, "no chunk video found on disk"
                else:
                    status, reason = UNAVAILABLE, f"chunk video present for {available}/{total}"
            elif key in columns:
                status, reason = DELIVERED, "column present in data parquet"
            else:
                status, reason = NOT_PRESENT, "column absent from data parquet"
            deliveries.append(
                StreamDelivery(
                    key=key,
                    status=status,
                    reason=reason,
                    delivered_episodes=available if spec.get("dtype") == STREAM_VIDEO else total,
                    total_episodes=total,
                )
            )
        return tuple(deliveries)

    # ------------------------------------------------------------- read frame

    def _read_numeric(self, record: EpisodeRecord, columns: list[str]) -> dict[str, np.ndarray]:
        pq = _require_pyarrow()
        table = pq.read_table(
            record.data_file,
            columns=columns,
            filters=[("episode_index", "==", record.episode_index)],
        )
        if table.num_rows == 0:
            raise LeRobotFormatError(
                f"episode {record.episode_index} has no rows in {record.data_file.name}"
            )
        result: dict[str, np.ndarray] = {}
        for name in columns:
            column = table.column(name).to_pylist()
            array = np.asarray(column)
            if array.ndim > 1 and array.shape[-1] == 1:
                array = array.reshape(array.shape[0], -1)
            array.setflags(write=False)
            result[name] = array
        return result

    def _decode_video(
        self, spec: VideoSlice, count: int, max_frames: int | None
    ) -> tuple[np.ndarray, str | None, bool]:
        """Decode at most ``count`` frames, honouring ``max_frames`` as a bound."""
        fps = self.fps or 0.0
        if not spec.path.is_file():
            return np.empty((0, 0, 0, 3), np.uint8), "chunk video file not found", False
        capture = cv2.VideoCapture(str(spec.path))
        if not capture.isOpened():
            return np.empty((0, 0, 0, 3), np.uint8), "chunk video could not be opened", False
        try:
            if fps > 0:
                capture.set(cv2.CAP_PROP_POS_FRAMES, spec.start_frame(fps))
            wanted = count if max_frames is None else min(count, max_frames)
            frames = []
            for _ in range(wanted):
                ok, frame = capture.read()
                if not ok:
                    break
                frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        finally:
            capture.release()
        truncated = max_frames is not None and count > max_frames
        if not frames:
            return np.empty((0, 0, 0, 3), np.uint8), "no frames decoded from chunk video", truncated
        stacked = np.stack(frames)
        stacked.setflags(write=False)
        if len(frames) < wanted:
            return stacked, f"decoded {len(frames)} of {wanted} frames", truncated
        return stacked, None, truncated

    def read_episode(
        self,
        episode_index: int,
        *,
        decode_video: bool = True,
        max_frames: int | None = None,
    ) -> EpisodeData:
        """Read one episode, reporting per-stream delivery.

        Video frames are decoded lazily per request and bounded by ``max_frames``
        so a high-resolution stream cannot pull an unbounded array into memory.
        """
        if max_frames is not None and max_frames <= 0:
            raise ValueError("max_frames must be positive")
        record = self.episode(episode_index)
        if not record.data_file.is_file():
            raise LeRobotFormatError(f"data file not found: {record.data_file}")

        available = set(_require_pyarrow().ParquetFile(record.data_file).schema_arrow.names)
        wanted = [
            key
            for key, spec in self.features.items()
            if spec.get("dtype") != STREAM_VIDEO and key in available
        ]
        if "timestamp" not in wanted:
            raise LeRobotFormatError("data parquet has no timestamp column")
        numeric = self._read_numeric(record, sorted(wanted))

        timestamp = numeric["timestamp"].astype(np.float64).reshape(-1)
        frame_index = (
            numeric["frame_index"].reshape(-1)
            if "frame_index" in numeric
            else np.arange(timestamp.shape[0], dtype=np.int64)
        )

        images: dict[str, np.ndarray] = {}
        deliveries: list[StreamDelivery] = []
        truncated = False
        for key, spec in self.features.items():
            if spec.get("dtype") == STREAM_VIDEO:
                video_slice = record.video_slices.get(key)
                if video_slice is None:
                    deliveries.append(
                        StreamDelivery(key, NOT_PRESENT, "episode metadata has no video slice")
                    )
                    continue
                if not decode_video:
                    status = DELIVERED if video_slice.path.is_file() else NOT_PRESENT
                    reason = (
                        "chunk video present; decoding not requested"
                        if status == DELIVERED
                        else "chunk video file not found"
                    )
                    deliveries.append(StreamDelivery(key, status, reason, 1, 1))
                    continue
                frames, problem, was_truncated = self._decode_video(
                    video_slice, record.length, max_frames
                )
                truncated = truncated or was_truncated
                if problem is None or frames.shape[0] > 0:
                    images[key] = frames
                if problem is None:
                    deliveries.append(
                        StreamDelivery(key, DELIVERED, "decoded from chunk video", 1, 1)
                    )
                else:
                    status = UNAVAILABLE if frames.shape[0] else NOT_PRESENT
                    deliveries.append(StreamDelivery(key, status, problem, 0, 1))
            elif key in numeric:
                deliveries.append(StreamDelivery(key, DELIVERED, "read from data parquet", 1, 1))
            else:
                deliveries.append(
                    StreamDelivery(key, NOT_PRESENT, "column absent from data parquet")
                )

        return EpisodeData(
            record=record,
            timestamp_s=timestamp,
            frame_index=np.asarray(frame_index),
            numeric={key: value for key, value in numeric.items() if key != "timestamp"},
            images=images,
            delivery=tuple(deliveries),
            video_truncated=truncated,
        )

    # ----------------------------------------------------------------- report

    def describe(self) -> dict[str, Any]:
        """Machine-readable summary: streams, episodes, and delivery status."""
        records = self.episodes()
        return {
            "root": str(self.root),
            "codebase_version": self.info.get("codebase_version"),
            "fps": self.fps,
            "declared_episodes": self.info.get("total_episodes"),
            "declared_frames": self.info.get("total_frames"),
            "listed_episodes": len(records),
            "listed_frames": int(sum(record.length for record in records)),
            "streams": [
                {
                    "key": descriptor.key,
                    "kind": descriptor.kind,
                    "dtype": descriptor.dtype,
                    "shape": list(descriptor.shape),
                    "video_codec": descriptor.video_codec,
                    "in_parquet": descriptor.in_parquet,
                }
                for descriptor in self.streams()
            ],
            "delivery": [
                {
                    "key": item.key,
                    "status": item.status,
                    "reason": item.reason,
                    "delivered_episodes": item.delivered_episodes,
                    "total_episodes": item.total_episodes,
                }
                for item in self.stream_manifest()
            ],
        }
