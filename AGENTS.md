# EmbodiedSense contributor instructions

- Current scope includes the RGB-D MVP 1 and LeRobot v3 reader; see README and docs/roadmap.md.
- Data delivery is a primary output. Findings are a parallel diagnostic output, not a replacement.
- Source data is read-only; derived data may be written to a new directory with provenance.
- Distinguish file presence, partial reading and complete delivery. Never synthesize indexes silently.
- Use commands and APIs. Do not manipulate the user's screen or launch viewers automatically.
- Prefer project/data/output/environment/cache storage on D: for this Windows machine.
- Keep the core independent of Rerun, ROS and GPU dependencies.
- Preserve source timestamps, units, calibration IDs and raw files.
- Synthetic samples and estimated values must be labelled explicitly.
- Run relevant numeric and integration tests, ruff check and format before committing.
- Do not commit data/, outputs/, environments, credentials or personal machine paths.
- Report actual measured results and clearly identify unimplemented modules.
