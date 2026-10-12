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

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

STREAM_VIDEO = "video"
STREAM_NUMERIC = "numeric"
STREAM_INDEX = "index"

DELIVERED = "DELIVERED"
PRESENT = "PRESENT"
PARTIAL = "PARTIAL"
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
    present_episodes: int = 0
    read_frames: int = 0
    expected_frames: int = 0
    inspection_basis: str = "file_presence"


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
    stream_units: dict[str, str | None] = field(default_factory=dict)

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


def _numeric_array(values, spec: dict[str, Any]) -> np.ndarray:
    """Validate the declared contract; never fill nulls or invent missing samples."""
    try:
        array = np.asarray(values)
    except (ValueError, TypeError) as error:
        raise LeRobotFormatError("ragged or unreadable numeric values") from error
    if array.dtype.kind not in "biuf" or not np.isfinite(array).all():
        raise LeRobotFormatError("null, nonnumeric or nonfinite values")
    shape = _shape_tuple(spec)
    actual = array.shape[1:]
    if actual != shape and not (shape == (1,) and actual == ()):
        raise LeRobotFormatError(f"shape {actual} differs from declared {shape}")
    try:
        dtype = np.dtype(spec["dtype"])
    except (KeyError, TypeError, ValueError) as error:
        raise LeRobotFormatError("unsupported numeric dtype") from error
    if dtype.kind not in "biuf":
        raise LeRobotFormatError("unsupported numeric dtype")
    if dtype.kind in "iu" and array.dtype.kind not in "iu":
        raise LeRobotFormatError("noninteger values for integer stream")
    if dtype.kind == "b" and array.dtype.kind != "b":
        raise LeRobotFormatError("nonboolean values for boolean stream")
    if dtype.kind in "iu":
        limits = np.iinfo(dtype)
        if (array < limits.min).any() or (array > limits.max).any():
            raise LeRobotFormatError("integer value outside declared dtype range")
    with np.errstate(over="ignore", invalid="ignore"):
        array = array.astype(dtype)
    if not np.isfinite(array).all():
        raise LeRobotFormatError("value outside declared dtype range")
    array.setflags(write=False)
    return array


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
        if re.fullmatch(r"v3\.\d+(?:\.\d+)?", version) is None:
            raise LeRobotFormatError(f"unsupported codebase_version: {version!r} (expected v3.x)")
        features = self.info.get("features")
        if not isinstance(features, dict) or not features:
            raise LeRobotFormatError("dataset metadata declares no features")
        self.features: dict[str, Any] = features
        if not all(isinstance(spec, dict) for spec in features.values()):
            raise LeRobotFormatError("feature specifications must be objects")
        try:
            self.fps = float(self.info.get("fps", 0.0))
        except (TypeError, ValueError) as error:
            raise LeRobotFormatError("dataset fps must be numeric") from error
        if not np.isfinite(self.fps) or self.fps <= 0:
            raise LeRobotFormatError("dataset fps must be finite and positive")
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
        return self._contained_path(relative)

    def video_file_for(self, stream_key: str, chunk_index: int, file_index: int) -> Path:
        relative = self._video_template().format(
            video_key=stream_key, chunk_index=chunk_index, file_index=file_index
        )
        return self._contained_path(relative)

    def _contained_path(self, relative: str) -> Path:
        path = (self.root / relative).resolve()
        if Path(relative).is_absolute() or not path.is_relative_to(self.root):
            raise LeRobotFormatError("payload path escapes dataset directory")
        return path

    def _video_fps(self, key: str) -> float:
        info = self.features[key].get("video_info") or {}
        if not isinstance(info, dict):
            raise LeRobotFormatError(f"{key}: video_info must be an object")
        try:
            fps = float(info.get("video.fps", self.fps))
        except (TypeError, ValueError) as error:
            raise LeRobotFormatError(f"{key}: video fps must be numeric") from error
        if not np.isfinite(fps) or fps <= 0:
            raise LeRobotFormatError(f"{key}: video fps must be finite and positive")
        return fps

    def _parquet_columns(self) -> set[str]:
        """Union across distinct files; this is schema presence, not delivery."""
        pq = _require_pyarrow()
        columns = set()
        for path in sorted({record.data_file for record in self.episodes()}):
            if path.is_file():
                try:
                    columns.update(pq.ParquetFile(path).schema_arrow.names)
                except (OSError, ValueError):
                    continue
        return columns

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
                for name in REQUIRED_EPISODE_COLUMNS:
                    value = row[name]
                    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                        raise LeRobotFormatError(f"invalid episode metadata integer: {name}")
                slices: dict[str, VideoSlice] = {}
                for key, columns in video_columns.items():
                    present = {name.split("/")[-1] for name in columns}
                    if not present.issuperset(VIDEO_SLICE_COLUMNS):
                        continue
                    if any(row[f"videos/{key}/{name}"] is None for name in VIDEO_SLICE_COLUMNS):
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
        if len({record.episode_index for record in records}) != len(records):
            raise LeRobotFormatError("duplicate episode metadata")
        for record in records:
            if (
                record.length <= 0
                or record.dataset_to_index - record.dataset_from_index != record.length
            ):
                raise LeRobotFormatError("episode length and dataset boundaries disagree")
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
        """Scan numeric values in bounded batches; video files are only PRESENT.

        Complete video delivery requires read_episode decoding, not file existence.
        No decoded video is retained by this manifest operation.
        """
        records = self.episodes()
        total = len(records)
        expected = sum(record.length for record in records)
        numeric_keys = [key for key in self.features if key not in self.video_stream_keys()]
        counts = {(record.episode_index, key): 0 for record in records for key in numeric_keys}
        present = {key: set() for key in numeric_keys}
        pq = _require_pyarrow()
        for path in sorted({record.data_file for record in records}):
            if not path.is_file():
                continue
            try:
                parquet = pq.ParquetFile(path)
                available = set(parquet.schema_arrow.names)
                keys = [key for key in numeric_keys if key in available]
                file_episodes = {r.episode_index for r in records if r.data_file == path}
                for key in keys:
                    present[key].update(file_episodes)
                if "episode_index" not in available:
                    continue
                for batch in parquet.iter_batches(
                    batch_size=1024, columns=sorted(set(keys + ["episode_index"]))
                ):
                    groups: dict[int, list] = {}
                    for row in batch.to_pylist():
                        index = row["episode_index"]
                        if index in file_episodes:
                            groups.setdefault(index, []).append(row)
                    for index, rows in groups.items():
                        for key in keys:
                            try:
                                array = _numeric_array(
                                    [row[key] for row in rows], self.features[key]
                                )
                            except LeRobotFormatError:
                                continue
                            counts[index, key] += len(array)
            except (OSError, ValueError):
                continue
        deliveries = []
        for key, spec in self.features.items():
            if spec.get("dtype") == STREAM_VIDEO:
                available = sum(
                    1
                    for record in records
                    if (video_slice := record.video_slices.get(key)) is not None
                    and video_slice.path.is_file()
                )
                if available == total:
                    status, reason = PRESENT, "video files exist; decoding not verified"
                elif available == 0:
                    status, reason = NOT_PRESENT, "no chunk video found on disk"
                else:
                    status, reason = PARTIAL, f"video files present for {available}/{total}"
                complete, frames, basis = 0, 0, "file_presence"
            else:
                available = len(present[key])
                complete = sum(counts[r.episode_index, key] == r.length for r in records)
                frames = sum(counts[r.episode_index, key] for r in records)
                basis = "numeric_values"
                if complete == total:
                    status, reason = DELIVERED, "declared numeric shape and values verified"
                elif not available:
                    status, reason = NOT_PRESENT, "column or file absent for every episode"
                else:
                    status, reason = UNAVAILABLE, f"complete numeric payload for {complete}/{total}"
            deliveries.append(
                StreamDelivery(
                    key=key,
                    status=status,
                    reason=reason,
                    delivered_episodes=complete,
                    total_episodes=total,
                    present_episodes=available,
                    read_frames=frames,
                    expected_frames=expected,
                    inspection_basis=basis,
                )
            )
        return tuple(deliveries)

    # ------------------------------------------------------------- read frame

    def _read_numeric(self, record: EpisodeRecord, columns: list[str]):
        pq = _require_pyarrow()
        table = pq.read_table(
            record.data_file,
            columns=columns,
            filters=[("episode_index", "==", record.episode_index)],
        )
        if table.num_rows != record.length:
            raise LeRobotFormatError(
                f"episode {record.episode_index}: {table.num_rows} rows "
                f"differ from length {record.length}"
            )
        result: dict[str, np.ndarray] = {}
        problems = {}
        for name in columns:
            column = table.column(name).to_pylist()
            try:
                result[name] = _numeric_array(column, self.features[name])
            except LeRobotFormatError as error:
                problems[name] = str(error)
        return result, problems

    def _decode_video(
        self, spec: VideoSlice, count: int, max_frames: int | None
    ) -> tuple[np.ndarray, str | None, bool]:
        """Decode at most ``count`` frames, honouring ``max_frames`` as a bound."""
        fps = self._video_fps(spec.stream_key)
        empty = np.empty((0, *_shape_tuple(self.features[spec.stream_key])), np.uint8)
        start = spec.start_frame(fps) if np.isfinite(spec.from_timestamp_s) else -1
        stop = int(round(spec.to_timestamp_s * fps)) if np.isfinite(spec.to_timestamp_s) else -1
        if start < 0 or stop - start != count:
            return empty, "video slice boundaries differ from episode length", False
        if not np.isclose(spec.to_timestamp_s * fps, stop, atol=1e-3, rtol=0):
            return empty, "video slice end is not on the CFR frame grid", False
        if not np.isclose(fps, self.fps, atol=1e-3):
            return empty, "unequal video/numeric rates need an explicit sampling map", False
        if not np.isclose(spec.from_timestamp_s * fps, start, atol=1e-3, rtol=0):
            return empty, "video slice start is not on the CFR frame grid", False
        if not spec.path.is_file():
            return empty, "chunk video file not found", False
        capture = cv2.VideoCapture(str(spec.path))
        if not capture.isOpened():
            capture.release()
            return empty, "chunk video could not be opened", False
        try:
            reported_fps = capture.get(cv2.CAP_PROP_FPS)
            if not np.isfinite(reported_fps) or not np.isclose(reported_fps, fps, atol=1e-3):
                return empty, "decoder fps differs from declared video fps", False
            # Sequential ordinal positioning avoids an unchecked GOP/frame seek.
            # This CFR subset favors correctness over speed; VFR/PTS support is deferred.
            for _ in range(start):
                if not capture.grab():
                    return empty, "video ends before episode slice", False
            wanted = count if max_frames is None else min(count, max_frames)
            frames = []
            for _ in range(wanted):
                ok, frame = capture.read()
                if not ok:
                    break
                if frame.shape != _shape_tuple(self.features[spec.stream_key]):
                    break
                frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        finally:
            capture.release()
        truncated = max_frames is not None and count > max_frames
        if not frames:
            return empty, "no frames decoded from chunk video", truncated
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
        if not {"timestamp", "frame_index", "episode_index"}.issubset(wanted):
            raise LeRobotFormatError(
                "data parquet requires timestamp, frame_index and episode_index"
            )
        numeric, problems = self._read_numeric(record, sorted(wanted))
        if any(key in problems for key in ("timestamp", "frame_index", "episode_index")):
            raise LeRobotFormatError(f"invalid required index/time stream: {problems}")

        timestamp = numeric["timestamp"].astype(np.float64).reshape(-1)
        frame_index = numeric["frame_index"].reshape(-1)
        if not np.array_equal(frame_index, np.arange(record.length)):
            raise LeRobotFormatError("frame_index is missing, repeated or out of source order")
        if not np.all(np.diff(timestamp) > 0) or timestamp[0] < 0:
            raise LeRobotFormatError(
                "episode timestamps must be finite, nonnegative and increasing"
            )
        if "index" in numeric and not np.array_equal(
            numeric["index"].reshape(-1),
            np.arange(record.dataset_from_index, record.dataset_to_index),
        ):
            raise LeRobotFormatError("global index disagrees with episode metadata")
        timestamp.setflags(write=False)

        images: dict[str, np.ndarray] = {}
        deliveries: list[StreamDelivery] = []
        truncated = False
        for key, spec in self.features.items():
            if spec.get("dtype") == STREAM_VIDEO:
                video_slice = record.video_slices.get(key)
                if video_slice is None:
                    deliveries.append(
                        StreamDelivery(
                            key,
                            NOT_PRESENT,
                            "episode metadata has no video slice",
                            0,
                            1,
                            0,
                            0,
                            record.length,
                        )
                    )
                    continue
                if not decode_video:
                    status = PRESENT if video_slice.path.is_file() else NOT_PRESENT
                    reason = (
                        "chunk video present; decoding not requested"
                        if status == PRESENT
                        else "chunk video file not found"
                    )
                    deliveries.append(
                        StreamDelivery(
                            key,
                            status,
                            reason,
                            0,
                            1,
                            int(status == PRESENT),
                            0,
                            record.length,
                            "file_presence",
                        )
                    )
                    continue
                frames, problem, was_truncated = self._decode_video(
                    video_slice, record.length, max_frames
                )
                truncated = truncated or was_truncated
                if problem is None or frames.shape[0] > 0:
                    images[key] = frames
                if problem is None and not was_truncated:
                    deliveries.append(
                        StreamDelivery(
                            key,
                            DELIVERED,
                            "decoded complete episode",
                            1,
                            1,
                            1,
                            len(frames),
                            record.length,
                            "decoded_full",
                        )
                    )
                else:
                    status = (
                        PARTIAL
                        if frames.shape[0]
                        else (UNAVAILABLE if video_slice.path.is_file() else NOT_PRESENT)
                    )
                    deliveries.append(
                        StreamDelivery(
                            key,
                            status,
                            problem or "bounded preview; full video not verified",
                            0,
                            1,
                            int(video_slice.path.is_file()),
                            len(frames),
                            record.length,
                            "decoded_prefix",
                        )
                    )
            elif key in numeric:
                deliveries.append(
                    StreamDelivery(
                        key,
                        DELIVERED,
                        "numeric values verified",
                        1,
                        1,
                        1,
                        record.length,
                        record.length,
                        "numeric_values",
                    )
                )
            elif key in problems:
                deliveries.append(
                    StreamDelivery(
                        key, UNAVAILABLE, problems[key], 0, 1, 1, 0, record.length, "numeric_values"
                    )
                )
            else:
                deliveries.append(
                    StreamDelivery(
                        key,
                        NOT_PRESENT,
                        "column absent from data parquet",
                        0,
                        1,
                        0,
                        0,
                        record.length,
                        "numeric_values",
                    )
                )

        return EpisodeData(
            record=record,
            timestamp_s=timestamp,
            frame_index=np.asarray(frame_index),
            numeric={key: value for key, value in numeric.items() if key != "timestamp"},
            images=images,
            delivery=tuple(deliveries),
            video_truncated=truncated,
            stream_units={
                key: spec.get("unit", spec.get("units")) for key, spec in self.features.items()
            },
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
                    "present_episodes": item.present_episodes,
                    "read_frames": item.read_frames,
                    "expected_frames": item.expected_frames,
                    "inspection_basis": item.inspection_basis,
                }
                for item in self.stream_manifest()
            ],
        }
