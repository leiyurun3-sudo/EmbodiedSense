# EmbodiedSense

RGB-D data infrastructure for robotics. MVP 1: ingestion, timestamp association,
quality diagnostics, calibrated point-cloud projection, replay and Rerun recording.

EmbodiedSense 将已有 RGB 图像和深度图接入统一流水线，匹配原始时间戳，
检查数据质量，并将有效像素投影成带颜色的三维点。输出可供另一个 Python 程序直接读取。

当前实现的是 **MVP 1 / Core v0.1**。后续 AI、ROS 2 和边缘部署路线见
[开发路线](docs/roadmap.md)。核心不需要 GPU 或实体机器人。

## 本机快速使用

项目位于 `D:\EmbodiedSense`。打开 PowerShell：

```powershell
cd D:\EmbodiedSense
.venv\Scripts\embodiedsense.exe doctor
.venv\Scripts\embodiedsense.exe replay --recording outputs/tum-v01 --speed 0
```

已经下载的数据位于 `data/rgbd_dataset_freiburg1_xyz`；已生成的真实数据结果位于
`outputs/tum-v01`。这些大文件留在本地，不提交 Git。

重新运行前 120 组真实数据，输出必须使用一个尚不存在的目录：

```powershell
.venv\Scripts\embodiedsense.exe run --config configs/tum_rgbd.yaml --data-root data/rgbd_dataset_freiburg1_xyz --out outputs/tum-new --limit 120 --rerun --ply
```

`--rerun` 保存记录，不打开窗口。需要查看时由你执行：

```powershell
.venv\Scripts\python.exe -m rerun outputs/tum-v01/scene.rrd
```

## 从 GitHub 安装

需要 Python 3.12 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)。

```powershell
git clone https://github.com/leiyurun3-sudo/EmbodiedSense.git
cd EmbodiedSense
uv sync --locked --extra viz
uv run embodiedsense doctor
uv run embodiedsense make-fixture --out data/synthetic
uv run embodiedsense run --config configs/synthetic.yaml --data-root data/synthetic --out outputs/synthetic --rerun --ply
uv run embodiedsense replay --recording outputs/synthetic --speed 0
uv run pytest -q
```

只安装 CPU 核心时使用 `uv sync --locked`，不加 `--rerun` 也能完整处理和回放。
Rerun 在没有安装时不影响核心 API；可视化测试会明确跳过。

本机的 uv 在 `D:\DevTools\uv\bin\uv.exe`，也可以用 `./dev.ps1` 替代 `uv`。
这个脚本把本项目的缓存和临时文件安排在 D 盘，不修改系统 PATH 或显卡设置。

## 获取真实数据

```powershell
uv run python scripts/download_tum.py
uv run embodiedsense run --config configs/tum_rgbd.yaml --data-root data/rgbd_dataset_freiburg1_xyz --out outputs/tum --limit 120 --rerun --ply
```

下载器仅下载 TUM 的 `fr1/xyz`，约 428 MiB 压缩包，解压后需要更多空间。
如果包已下载则核验 SHA256 后复用；已有数据集目录不会被覆盖。
网络失败时保留 `.part` 文件供排查，当前下载器尚不支持断点续传。

深度编码、图像预配准和推荐内参依据
[TUM 官方格式说明](https://cvg.cit.tum.de/data/datasets/rgbd-dataset/file_formats)。
PNG 深度除以 5000 转换成米，0 表示缺失；不再次应用已经由数据发布方实施的深度缩放修正。
默认配置使用官方推荐的 ROS-default pinhole 参数，属于近似投影，不能当作本项目重新实测的标定。

## 作为 Python 库使用

```python
from embodiedsense.core.config import load_config
from embodiedsense.core.pipeline import RGBDPipeline

pipeline = RGBDPipeline("data/rgbd_dataset_freiburg1_xyz", load_config("configs/tum_rgbd.yaml"))
for obs in pipeline.iter_observations(limit=10):
    print(obs.rgb.timestamp_ns, obs.sync_delta_ns, obs.quality.status)
    print(obs.points_m.shape, obs.calibration.frame_id)
```

`obs.rgb`、`obs.depth` 保留原始时间戳文本、纳秒值、路径、传感器 ID 和标定 ID；
`obs.quality` 提供状态、异常标记及有效深度比例。数组设为只读，原始数据文件不修改。
默认坐标为相机光学坐标：x 向右、y 向下、z 向前，单位米。
`transform_points(points, T_target_source)` 可应用显式给定的刚体变换。

## 同步和质量行为

- 离线最近邻匹配，默认最大时间差 20 ms，边界包含，相等距离选更早时间戳。
- 允许深度帧复用，输出 `DEPTH_REUSED`；每张 RGB 都产生结果，未匹配为 `MISSING/UNSYNCED`。
- 在排序前检测时间戳倒退、重复和超过阈值的间隔，保留源文件行序。
- 间隔异常不等于已证明丢帧，`TIMESTAMP_GAP` 只报告观测到的间隔。
- 损坏图像、错误尺寸/格式、无效深度得到明确状态，单个坏帧不终止整个图像处理流。
- 损坏的索引、缺失标定或无效配置会拒绝启动，并报告具体原因。
- 无效像素不产生点云；`WARN` 可包含正常的局部深度空洞，不能简单解读为整帧不可用。

这是离线回放原型：匹配可能使用参考帧之后的深度，不能直接当作因果在线同步器。
图像索引常驻内存，像素数据逐组解码；没有构建世界地图或估计相机位姿。

## 输出内容

每次运行创建一个新目录，包含：

| 文件 | 内容 |
|---|---|
| `manifest.json` | 软件版本、配置、来源、完成状态 |
| `observations.jsonl` | 原始时间戳、质量事件、配对误差、帧校验值 |
| `frames/*.npz` | 无损 RGB/深度载荷、点云和颜色，可独立回放 |
| `frames/*.ply` | 可选彩色点云，兼容常用三维软件 |
| `scene.rrd` | 可选 Rerun 图像、深度、点云和质量时间轴 |
| `report.json` / `report.md` | 本次运行的统计、测得的时延和环境 |

回放会核验载荷 SHA256、完成状态和事件数量。
`--speed 0` 尽快离线回放；`--speed 1` 按传感器时间差进行节奏控制。

## 测试与验证

```powershell
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```

覆盖纳秒精度、容差边界、等距匹配、图像损坏、乱序/重复时间戳、NaN、
深度尺度与坐标数值、色彩通道、刚体变换、回放一致性和校验失败。
GitHub Actions 在 Windows 和 Linux 运行合成数据测试，无需下载真实数据或使用 GPU。
实际本机验证见 [验收记录](docs/verification.md)。

## LeRobot v3 数据读取（阶段 B）

```powershell
./dev.ps1 sync --locked --extra viz --extra lerobot
./dev.ps1 run embodiedsense inspect-lerobot --data-root data/pusht --episode 0 --max-frames 3
```

`LeRobotV3Dataset.read_episode()` 返回图像、数值序列和来源元信息，保持原始流名。
2026-10-12 复核修复见 [审查与验证记录](docs/codex-review-20261012.md)。
交付状态的接口语义已收紧：

- `PRESENT`：文件存在，但未解码；清单不能证明所有视频可读。
- `PARTIAL`：按请求只读了一部分，或解码提前结束；结合 reason 和帧数区分两者。
- `DELIVERED`：指定 episode 的完整视频已解码，或数值 shape/有限性等契约通过。
- `NOT_PRESENT`：源文件、列或元数据引用缺失。
- `UNAVAILABLE`：输入存在，但格式、数值或解码未满足读取契约。

`present_episodes` 与 `delivered_episodes` 分开统计；`read_frames`、`expected_frames`
与 `inspection_basis` 明确本次验证范围。数值清单按数据文件、episode 分批扫描，
不再把第一份文件的列结构套到全数据集。单位缺失为 `stream_units[key] = None`。
坏的可选数值流不输出伪造数组，仍可读取其他流；关键索引或时间损坏会明确拒绝该 episode。

当前只支持已验证的 CFR、一视频帧对一数值行布局；使用顺序解码定位，避免未验证的 seek。
跳到靠后 episode 需要解码/跳过前缀，尚未实现高性能 PTS/VFR 或多速率视频采样映射。
PushT 不含深度，不能从此读取器获得米制 3D 点云。TUM 几何链路保持可用。

下一轮主线是统一处理后数据服务（FR-12），不是扩大质检报告数量。
详见 [DeepSeek 下一轮指令](docs/deepseek-next-instructions.md)。

## 目录与参考

`core/` 数据契约与流水线；`sensors/` 文件读取和合成样例；
`processing/` 同步、质量与几何；`storage.py` 独立记录；
`visualization/` Rerun 接口；`tests/` 数值与端到端测试。

Rerun 用于可视化，OpenCV 用于图像解码，NumPy 用于数值计算。
本项目没有复制这些项目的算法源码。代码采用 MIT；数据和依赖的许可分别见
[数据与依赖说明](docs/DATA_LICENSES.md)。

