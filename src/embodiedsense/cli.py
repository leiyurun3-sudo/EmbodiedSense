import argparse
import json
import platform
import shutil
import subprocess
import sys
from collections import Counter
from dataclasses import asdict
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from time import perf_counter

import numpy as np
import psutil

from embodiedsense import __version__
from embodiedsense.core.config import load_config
from embodiedsense.core.pipeline import RGBDPipeline
from embodiedsense.sensors.synthetic import make_fixture
from embodiedsense.storage import RecordingWriter, replay, write_ply


def doctor():
    deps = {}
    for name in ("numpy", "opencv-python-headless", "pyyaml", "rerun-sdk"):
        try:
            deps[name] = version(name)
        except PackageNotFoundError:
            deps[name] = None
    gpu = "not detected"
    if shutil.which("nvidia-smi"):
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            gpu = result.stdout.strip() if result.returncode == 0 else "query unavailable"
        except (OSError, subprocess.TimeoutExpired):
            gpu = "query unavailable"
    return {
        "python": platform.python_version(),
        "os": platform.platform(),
        "cpu": platform.processor(),
        "cpu_count": psutil.cpu_count(),
        "ram_gib": round(psutil.virtual_memory().total / 2**30, 2),
        "free_disk_gib": round(shutil.disk_usage(Path.cwd()).free / 2**30, 2),
        "gpu_inventory": gpu,
        "gpu_compute_tested": False,
        "core_device": "CPU",
        "dependencies": deps,
    }


def run(args):
    config = load_config(args.config)
    pipeline = RGBDPipeline(args.data_root, config)
    output = Path(args.out)
    writer = RecordingWriter(output, config, pipeline.root.name)
    viz = None
    counters, flags = Counter(), Counter()
    latencies, deltas = [], []
    total_points = 0
    started = perf_counter()
    process = psutil.Process()
    peak_rss = process.memory_info().rss
    success = False
    try:
        if args.rerun:
            from embodiedsense.visualization.viewer import RerunWriter

            viz = RerunWriter(output / "scene.rrd")
        iterator = iter(pipeline.iter_observations(args.limit))
        while True:
            tick = perf_counter()
            obs = next(iterator, None)
            if obs is None:
                break
            latencies.append((perf_counter() - tick) * 1000)
            writer.write(obs)
            if viz:
                viz.write(obs)
            if args.ply and len(obs.points_m):
                write_ply(
                    output / "frames" / f"{writer.count - 1:06d}.ply", obs.points_m, obs.colors_rgb
                )
            counters[obs.quality.status] += 1
            flags.update(obs.quality.flags)
            total_points += len(obs.points_m)
            if obs.sync_delta_ns is not None:
                deltas.append(abs(obs.sync_delta_ns) / 1e6)
            peak_rss = max(peak_rss, process.memory_info().rss)
        success = True
    finally:
        writer.close(complete=success)
        if viz:
            viz.close()
    elapsed = perf_counter() - started
    report = {
        "version": __version__,
        "source_id": pipeline.root.name,
        "observations": writer.count,
        "status_counts": dict(counters),
        "flag_counts": dict(flags),
        "total_points": total_points,
        "matched_observations": len(deltas),
        "sync_abs_p95_ms": float(np.percentile(deltas, 95)) if deltas else None,
        "decode_validate_project_p95_ms": float(np.percentile(latencies, 95)),
        "wall_seconds_including_outputs": elapsed,
        "observations_per_second_including_outputs": writer.count / elapsed,
        "sampled_peak_rss_mib": peak_rss / 2**20,
        "config": asdict(config),
        "environment": doctor(),
    }
    (output / "report.json").write_text(
        json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
    )
    (output / "report.md").write_text(
        "# EmbodiedSense run\n\n"
        f"Source: {pipeline.root.name}\n\n"
        f"Observations: {writer.count}; points: {total_points}\n\n"
        f"Status: {dict(counters)}\n\nFlags: {dict(flags)}\n\n"
        "See report.json for measured timings, hardware and exact configuration.\n"
        "Timings measure this offline run, not real-time robot control.\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "recording": str(output),
                "observations": writer.count,
                "points": total_points,
                "status": dict(counters),
            },
            indent=2,
        )
    )


def main():
    parser = argparse.ArgumentParser(description="EmbodiedSense RGB-D Core")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Read-only environment diagnostics")
    fixture = sub.add_parser("make-fixture", help="Create labelled synthetic RGB-D data")
    fixture.add_argument("--out", type=Path, required=True)
    fixture.add_argument("--frames", type=int, default=12)
    execute = sub.add_parser("run", help="Process TUM-format registered RGB-D data")
    execute.add_argument("--config", required=True)
    execute.add_argument("--data-root", required=True)
    execute.add_argument("--out", required=True, help="New output directory; never overwrite")
    execute.add_argument("--limit", type=int)
    execute.add_argument(
        "--rerun", action="store_true", help="Save scene.rrd without opening a window"
    )
    execute.add_argument("--ply", action="store_true", help="Also save colored binary PLY frames")
    inspect = sub.add_parser("summary", help="Sanity-check a dataset index without decoding images")
    inspect.add_argument("--config", required=True)
    inspect.add_argument("--data-root", required=True)
    playback = sub.add_parser("replay", help="Replay a self-contained recording")
    playback.add_argument("--recording", required=True)
    playback.add_argument("--speed", type=float, default=0)
    playback.add_argument("--rerun-out", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "doctor":
            print(json.dumps(doctor(), indent=2))
        elif args.command == "make-fixture":
            make_fixture(args.out, args.frames)
            print(f"Synthetic fixture: {args.out}")
        elif args.command == "run":
            run(args)
        elif args.command == "summary":
            config = load_config(args.config)
            pipeline = RGBDPipeline(args.data_root, config)
            print(json.dumps(pipeline.summary(), indent=2))
        elif args.command == "replay":
            viz = None
            if args.rerun_out:
                if args.rerun_out.exists():
                    raise FileExistsError(args.rerun_out)
                from embodiedsense.visualization.viewer import RerunWriter

                viz = RerunWriter(args.rerun_out)
            count = 0
            try:
                for obs in replay(args.recording, args.speed):
                    if viz:
                        viz.write(obs)
                    count += 1
            finally:
                if viz:
                    viz.close()
            print(json.dumps({"replayed_observations": count}))
    except (OSError, ValueError, KeyError, ImportError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
