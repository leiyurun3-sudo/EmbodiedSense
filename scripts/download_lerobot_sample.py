"""Download a minimal LeRobot v3 subset (lerobot/pusht) under data/ for the adapter.

Only the files the adapter needs are fetched, not the whole dataset. Sizes are
verified against observed SHA256 values so a silent upstream change is visible.

Note: ``meta/info.json`` declares ``data_files_size_in_mb`` 100 and
``video_files_size_in_mb`` 500, while the files actually published for this
dataset are far smaller (see the manifest below). The adapter therefore reports
declared and listed counts separately instead of trusting the declaration.
"""

import argparse
import hashlib
import urllib.request
from pathlib import Path

BASE = "https://huggingface.co/datasets/lerobot/pusht/resolve/main"

# Observed on 2026-10-12; not a publisher signature.
MANIFEST = {
    "meta/info.json": "0b46becc21ad92b117da1e95daf44110bb21d0bde75638b4ad167c8ccd959d38",
    "meta/stats.json": "2a58f97df48893274c587fdf09f5cecb31fe385b1e188a82197a8972ccfb9d6d",
    "meta/tasks.parquet": "95535f5152771bc0a6ab0fd93d23d7c1fecd9b7a13dc117852dfdd94f5b2c711",
    "meta/episodes/chunk-000/file-000.parquet": (
        "bc1226f33d3d1635ec1954f9942073709be8e106fde5f4f16b9d52edc4e0ebc4"
    ),
    "data/chunk-000/file-000.parquet": (
        "9abc0431f12d10c33b5b37b1bf1e6e557c8ae1c8c29fb86658969e48fcbbbf01"
    ),
    "videos/observation.image/chunk-000/file-000.mp4": (
        "f58d11857651dad5983c021b56a50a68a6ce19068834c1fb5cac099219fb3a78"
    ),
}


def _fetch(url: str, target: Path) -> None:
    partial = target.with_suffix(target.suffix + ".part")
    with urllib.request.urlopen(url, timeout=120) as response, partial.open("wb") as output:
        while block := response.read(1024 * 1024):
            output.write(block)
    partial.rename(target)


def main():
    parser = argparse.ArgumentParser(description="Download the LeRobot v3 pusht subset")
    parser.add_argument("--data-dir", type=Path, default=Path("data") / "pusht")
    parser.add_argument("--force", action="store_true", help="Re-download files that already exist")
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip SHA256 checks; also update MANIFEST if upstream data changed",
    )
    args = parser.parse_args()

    root = args.data_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    total = 0
    for relative, expected in MANIFEST.items():
        target = root.joinpath(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not args.force:
            print(f"keep  {relative}")
        else:
            print(f"fetch {relative}", flush=True)
            _fetch(f"{BASE}/{relative}", target)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        size = target.stat().st_size
        total += size
        if not args.no_verify and digest != expected:
            raise ValueError(
                f"{relative}: SHA256 mismatch\n  expected {expected}\n  actual   {digest}\n"
                "Upstream data may have changed; re-run with --no-verify to accept it."
            )
        print(f"      {size:,} bytes  sha256 {digest[:16]}...")

    print(f"\nSubset ready at {root} ({total:,} bytes total)")
    print(f"Try: uv run embodiedsense inspect-lerobot --data-root {root} --episode 0")


if __name__ == "__main__":
    main()
