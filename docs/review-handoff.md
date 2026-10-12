# EmbodiedSense 工作交接与复核报告

**用途：** 交给外部代码审查方（Codex）独立复核本次工作。
**日期：** 2026-10-12
**仓库：** `D:\EmbodiedSense` · https://github.com/leiyurun3-sudo/EmbodiedSense
**被测提交：** `f2d0ccc`（远端 `main` 与本地一致）
**工作区状态：** 干净（`git status --short` 无输出）

---

## 0. 给复核者的阅读指引

这份文档分四类内容，**请按标注区别对待**：

| 标注 | 含义 |
|---|---|
| **[实测]** | 本次实际运行命令或读取文件得到的结果，可独立复现 |
| **[读码]** | 通过阅读源码得到的结论，不是运行结果 |
| **[未验证]** | 明确没有验证，不要当作事实采用 |
| **[自评弱点]** | 我自己认为有问题或不够好的地方，请重点审 |

**本报告刻意不引用项目历史文档的结论作为事实。** 凡引用历史声明，都标为 **[未验证]**。
第 10 节列出了我在复核时发现的、**项目自身文档与源码不一致**的地方。

**本次工作的范围边界：** 仓库中 `11c64d6` 及更早的提交（RGB-D MVP 1、各份设计文档、工具链对照表）
**不是本次产物**，我只做了阅读与核验，未改动其内容（除第 10 节记录的一处**尚未修改**的过度声明）。
本次新增的产物全部在 `f154a7b` 与 `f2d0ccc` 两个提交里，详见第 11 节。

---

## 1. 项目背景

EmbodiedSense 是面向机器人 / 具身智能的**多模态数据处理与服务软件**。定位为一个独立的数据处理层：
接收多源观测（图像、深度、动作、状态等），做时间关联、格式与单位规范、质量诊断，
再通过统一接口提供给上层程序或 Agent。

**核心设计约束**（来自仓库 `AGENTS.md` 与开发方案）：

- 核心保持 **CPU-first、模型无关**：不强制依赖 GPU、Rerun、ROS 2、外部网络
- **原始数据只读**：清洗产物必须指向原始来源，不原地修改
- 保留原始时间戳、单位、标定 ID
- 合成数据与估算值必须显式标注
- 提交前必须跑测试与 lint；**报告真实测量结果，未实现的模块必须明确说明**

---

## 2. 本轮任务的来源

依据仓库外的一份开发方案《EmbodiedSense 正式开发执行方案》
（用户提供于 `C:\Users\14725\Downloads\EmbodiedSense_DeepSeek_Development_Plan.md`）。

该方案的关键约束，逐条列出以便复核者判断我是否遵守：

1. **不要一上来实现 B～G 所有阶段**；第一轮只做阶段 A，并据核验结果启动阶段 B 的最小切片。
2. 阶段 A 必须先核验、不得覆盖已有功能；要跑 `pytest`、`ruff`、`doctor`，
   **有真实数据时再跑最小回归流程**。
3. 阶段 A 要列出三栏："仓库确实已实现 / 文档宣称但待复核 / 尚未实现"。
4. 阶段 B 切片 1 只要求：**能完整列出数据集模态与 episode，并读取一条含视频、动作、状态的 episode**。
5. 明确区分 `NOT_DELIVERED`（输入丢失）/ `NOT_RUN`（规则未运行）/ `NOT_DETECTED`（已送达未命中）/ `DETECTED`。
6. 不得覆盖原始数据、`outputs/` 历史记录或用户未提交改动。
7. 禁止为了匹配计划书而大规模迁移旧代码。
8. 不得虚构结果；未执行的不得声称成功。
9. 发现文档与代码冲突时，**列出冲突并请用户选择，不自动改需求**。
10. 切片完成后**等待用户确认**再继续阶段 C。

---

## 3. 执行环境 **[实测]**

| 项目 | 实测值 |
|---|---|
| 操作系统 | Windows 11（`Windows-11-10.0.26200-SP0`） |
| Python | 3.12.5（由 `uv` 管理，`uv` 0.13.0） |
| CPU | 24 逻辑 CPU |
| 内存 | 15.78 GiB |
| GPU | NVIDIA GeForce RTX 4060 Laptop，8188 MiB（可枚举，`gpu_compute_tested: false`） |
| 核心设备 | CPU |
| 磁盘可用 | 472.11 GiB |
| NumPy | 2.5.3 |
| OpenCV headless | 4.14.0.94 |
| PyYAML | 6.0.3 |
| Rerun SDK | 0.29.2 |
| pyarrow | 26.0.0 |

**网络环境说明（与代码无关，但影响数据获取）：** 本机通过本地加速器（SpeedCloud，监听 `127.0.0.1:7892`）访问外网。
诊断结论：Google / GitHub / PyPI / `huggingface.co` 根域名均返回 200；
`huggingface.co/api/...` 在本机返回 401（与本项目无关，仅记录备查）。
**关键点：HuggingFace 数据集文件走 `huggingface.co/datasets/<id>/resolve/main/...` 可正常下载。**
我最初误把 `lerobot/pusht` 当作 model（路径少了 `/datasets/`）而得到 401，这是我的 URL 错误，不是网络故障。

---

## 4. 阶段 A：现状核验 **[实测]**

### 4.1 运行的命令与结果

| 命令 | 结果 |
|---|---|
| `uv run pytest -q` | **37 项通过**，无失败、无跳过 |
| `uv run ruff check .` | `All checks passed!` |
| `uv run ruff format --check .` | `37 files already formatted` |
| `uv run embodiedsense doctor` | 正常输出（内容见第 3 节） |
| `uv run embodiedsense run --config configs/tum_rgbd.yaml --data-root data/rgbd_dataset_freiburg1_xyz --out outputs/phase-a-regression --limit 20` | **20 组观测，287,228 个三维点，状态全部 WARN** |
| `uv run embodiedsense replay --recording outputs/phase-a-regression --speed 0` | **回放 20 组观测**，与写入一致 |

**回归结论：现有 MVP 1 的接口与测试保持正常，阶段 A 通过条件满足。**

**注意合规性：** 本次 TUM 回归写入**新目录** `outputs/phase-a-regression`，
未覆盖 `outputs/` 下任何历史目录（历史目录有 `synthetic-v01`、`tum-v01`、`tum-d-v01`、
`tum-movecheck`、`test-core-2`、`test-d-drive-1`、`test-integration-1`）。

### 4.2 三栏核验清单

完整清单已写入 **[docs/phase-a-status.md](D:/EmbodiedSense/docs/phase-a-status.md)**，此处摘要。

**A 栏（已实现且本轮实测验证）** —— 共 21 项，包括：
索引读取与时间戳异常标记、纳秒精度时间戳、离线最近邻同步（容差含边界、等距取较早者）、
深度复用标记、质量校验与 `OK/WARN/INVALID/MISSING` 状态、
六类质量标记、深度有效比例、深度单位换算、反投影点云、刚体变换校验、
自包含记录（manifest + 逐帧 SHA256）、回放校验、PLY 导出、拒绝覆盖已有输出、
CLI 四个子命令、核心不导入 Rerun、无界面 Rerun 导出、合成 fixture、新增的 LeRobot v3 适配器。

**B 栏（文档有声明，本轮未独立复核）** —— 共 6 项，**请复核者不要把这些当作本轮实测**：

| 声明 | 出处 | 状态 |
|---|---|---|
| TUM 120 组观测：1,731,525 点、同步差 P95 16.86815 ms、8.26 组/s、RSS 峰值 100.60 MiB | `docs/verification.md` | **[未验证]** 本轮只跑 20 组 |
| 120 帧均 WARN、其中 9 帧含 `DEPTH_REUSED` | 同上 | **[未验证]** |
| `rerun rrd verify` 通过 | 同上 | **[未验证]** 本轮未重跑 |
| `uv build` 生成 wheel 与 sdist | 同上 | **[未验证]** 本轮未重跑 |
| GitHub Actions 在 Windows/Linux 执行 | `.github/workflows/ci.yml` | **[未验证]** 只审阅了配置；远端执行结果未核实 |
| Rerun GUI 视觉验收 | 同上 | 项目本身明确声明**未做** |

**C 栏（未实现）** —— 共 10 项，见第 10 节与 `docs/phase-a-status.md`。

---

## 5. 阶段 B 切片 1：LeRobot v3 适配器

### 5.1 交付的能力

新增 `src/embodiedsense/adapters/lerobot_v3.py`（564 行），提供：

| 能力 | 接口 |
|---|---|
| 列出全部声明的流并分类 | `streams()` → `StreamDescriptor`（`video` / `numeric` / `index`） |
| 列出全部 episode | `episodes()`、`episode_indexes()`、`episode(n)` |
| 数据集级交付清单 | `stream_manifest()` → `StreamDelivery` |
| 读取单个 episode | `read_episode(n, decode_video=..., max_frames=...)` → `EpisodeData` |
| 机器可读摘要 | `describe()` |
| CLI | `embodiedsense inspect-lerobot --data-root ... [--episode N] [--max-frames N] [--no-video]` |

### 5.2 真实数据实测 **[实测]**

数据源：`lerobot/pusht`（HuggingFace，`codebase_version: v3.0`）的**最小子集**，
只取 6 个文件、共 7,680,844 字节，不是完整仓库。下载脚本逐文件校验 SHA256。

```powershell
uv run embodiedsense inspect-lerobot --data-root data/pusht --episode 0 --max-frames 3
```

| 指标 | 实测值 |
|---|---:|
| `codebase_version` | v3.0 |
| fps | 10.0 |
| 声明的 episode 数 / 实际列出 | 206 / 206 |
| 声明的帧数 / 实际列出 | 25,650 / 25,650 |
| 列出的流总数 | 11 |
| 其中视频流 | 1（`observation.image`） |
| 其中数值流 | 7 |
| 其中计数流 | 4 |
| `observation.image` 是否在 parquet 中 | **否** |
| `observation.image` 编码 | av1 |
| 数据集级交付状态 | **11/11 流 `DELIVERED`**，视频覆盖 **206/206** episode |

读取 episode 0：

| 指标 | 实测值 |
|---|---:|
| 帧数 | 161 |
| `observation.state` 形状 | (161, 2) |
| `action` 形状 | (161, 2) |
| 视频解码形状（受 `--max-frames 3` 限制） | (3, 96, 96, 3) |
| 时间戳范围 | 0.0 – 16.0 s |
| `clock_domain` | `episode_relative` |
| 时间戳单位 | second |
| 单位来源 | `dataset_defined`（适配器不做换算） |

episode 1 的视频切片起点为 **16.1 s**，与其前序 episode 长度 161 帧一致，
表明分块视频按 episode 顺序拼接、按时间区间切分。 **[实测]**

`--no-video` 实测：`image_shapes` 为空、`video_truncated` 为 `false`、
`frames_read` 仍为 161，视频交付状态为 `DELIVERED`，
原因为 `chunk video present; decoding not requested`。 **[实测]**
即"未请求解码"与"流缺失"被明确区分。

---

## 6. 关键技术发现（读真实文件得到） **[实测]**

这三条不是从文档推断的，是读 pusht 的真实文件得到的。**它们直接支撑阶段 C/D 的设计前提。**

### 6.1 视频流是独立文件，不在 parquet 中

`meta/info.json` 声明 `observation.image` 的 `dtype` 为 `video`，
但 `data/chunk-000/file-000.parquet` 的列只有 10 个数值/计数列，**没有任何图像列**。

**推论：只复制 parquet 的格式转换会静默丢掉整条相机流。**
这对此前"质检工具检出 0/8"的现象提供了机制解释：
故障注入在相机流上时，接收方根本没有收到数据，**不是检测算法漏检**。
这与开发方案阶段 C 要求区分 `NOT_DELIVERED` 与 `NOT_DETECTED` 完全对应。

### 6.2 `timestamp` 是 episode 内相对秒，不是 epoch 时钟

实测 episode 0 的时间戳范围 0.0–16.0 s（161 帧 @ 10 fps），每帧 +0.1 s。

**这是与 TUM 完全不同的时间语义**（TUM 是绝对 Unix 时间戳）。
适配器因此显式记录 `clock_domain = "episode_relative"` 与 `timestamp_unit = "second"`，
不把它当墙钟时间使用。 **[读码]** 适配器未做任何跨来源时间戳归一。

### 6.3 `info.json` 声明的大小与实际文件不符

元数据写 `data_files_size_in_mb: 100`、`video_files_size_in_mb: 500`；
实际发布的文件是 **674,393 字节（约 0.67 MB）与 6,890,970 字节（约 6.9 MB）**。

适配器因此**分别报告"声明值"与"实际列出值"，不信任声明**。 **[读码]**

**限定：** 我只陈述观察到的差异，**不断言上游字段的确切含义**
（该字段可能是其他含义或未更新）。这一点请复核者注意措辞边界。

---

## 7. 自动化测试

### 7.1 测试总量 **[实测]**

- 总计 **37 项通过**，无失败、无跳过
- 其中原始 20 项（RGB-D MVP 1），本阶段新增 **17 项**
- 新增文件：`tests/test_lerobot_adapter.py`（17 项）、`tests/lerobot_fixture.py`（夹具构造器）

### 7.2 新增 17 项清单 **[实测]**

```
test_lists_every_declared_stream
test_video_stream_is_not_stored_in_parquet
test_episode_enumeration
test_unknown_episode_raises
test_read_episode_delivers_state_action_and_video
test_episode_timestamps_are_monotonic_and_relative
test_video_frames_match_episode_offsets
test_max_frames_bounds_decoded_memory
test_decode_video_false_reports_presence_without_decoding
test_reading_does_not_modify_the_dataset
test_missing_video_is_reported_not_silently_dropped
test_missing_data_file_is_reported
test_describe_reports_declared_and_listed_counts
test_non_v3_version_is_rejected
test_missing_metadata_is_rejected
test_platform_without_mp4_encoder_is_detected
test_real_pusht_subset          # 真实子集不存在时自动跳过
```

### 7.3 测试设计要点

**夹具的"可观测性"设计：** `tests/lerobot_fixture.py` 生成的视频中，
**每帧的灰度值编码了该帧的全局帧号**（`index * 10 % 256`）。
因此 `test_video_frames_match_episode_offsets` 可以断言
"episode 1 解出的是第 4–6 帧而不是第 0–2 帧"，
而不是仅仅断言"能读出一些帧"。**这是验证 episode→视频帧映射的关键**，
也正是跨工具交接中最容易静默出错的地方。

**只读性验证：** `test_reading_does_not_modify_the_dataset` 在读取前后
对 data parquet 与视频文件分别计算 SHA256 并比对，确认原始数据未被原地修改。

**缺失必须被报告：** `test_missing_video_is_reported_not_silently_dropped`
删除 mp4 后断言：数据集级与 episode 级都报 `NOT_PRESENT`，
而 `observation.state` / `action` 仍为 `DELIVERED`（缺失相机不应中断读取）。

**内存上界：** `test_max_frames_bounds_decoded_memory` 断言 `max_frames=2` 时只解 2 帧、
`video_truncated=True`，同时元数据仍完整报告 4 帧。

**合成数据已显式标注：** 夹具文件头注明这是确定性合成数据、不是测量值。

---

## 8. 本轮发现并修复的两个真实风险

这两项是我在自查复验时发现的，**开发方案里没有提到**。

### 8.1 CI 可能缺少 pyarrow 依赖

`.github/workflows/ci.yml` 原本执行 `uv sync --locked --extra viz`，
**没有** `--extra lerobot`。我新增的测试需要 pyarrow。

**[实测] 验证过程：** `uv export --group dev --no-hashes` 确认 `pyarrow==26.0.0` 在 `dev` 组中，
而 `uv sync` 默认安装 `dev` 组，因此**CI 实际上不会缺依赖**。

**仍做的修改：** 把 CI 改为 `uv sync --locked --extra viz --extra lerobot`，
使适配器声明的可选依赖被**显式**安装，不再隐式依赖 `dev` 组的默认行为。
（`pyarrow` 同时保留在 `dev` 组中，因为测试需要它。）

### 8.2 跨平台可能产生与环境相关的红灯

CI 矩阵包含 `ubuntu-latest`。我的夹具用 `cv2.VideoWriter` 写 mp4；
**如果该平台的 OpenCV 缺少 mp4 编码器，夹具构造会失败，测试会红**——
而这是环境问题，不是代码缺陷。

**修复：** 新增 `lerobot_fixture.mp4_encoder_available()` 探测函数；
依赖视频文件的 6 项测试加 `@needs_video` 标记，
**无法写 mp4 时跳过而非失败**；夹具在无法打开编码器时抛出专用的
`VideoEncoderUnavailable` 异常（而非裸 `RuntimeError`）。
该守卫本身也有单元测试覆盖（`test_platform_without_mp4_encoder_is_detected`，
通过 monkeypatch 把 `VideoWriter` 换成打不开的假对象）。

**[实测] 本机结果：** 编码器可用，17 项全部实际执行，**无跳过**。

---

## 9. 本轮明确未验证的事项 **[未验证]**

请复核者不要把这些当作已完成。逐条列出：

1. **Linux 与远端 CI 从未运行过新配置。** 我只审阅并修改了 workflow 文件。
   新配置是否需要调整、`ubuntu-latest` 上 mp4 编码器是否存在，都**未知**。
2. **120 组观测的 TUM 数字未复现**（本轮只跑 20 组）。
3. **`uv build`（wheel/sdist）未重跑。**
4. **Rerun 导出与 `rrd verify` 未重跑**；项目的"未做 GUI 视觉验收"立场保持不变。
5. **未做任何下游训练或策略实验。** 本次结果只支持"数据被完整读入"这一结论，
   **不支持任何"模型表现提升"的结论。**
6. **未做性能基准。** 没有测量适配器的读取吞吐、内存峰值或解码耗时。
7. **多 chunk / 多数据文件场景未测试。** pusht 的全部 25,650 帧恰好都在
   `chunk-000/file-000`（parquet 行数与 `total_frames` 相等，视频帧数亦相等），
   **这一结论只对该数据集成立**，不代表其他数据集的分块方式。
8. **只解码了 episode 0（161 帧）与 episode 1（118 帧）**，未遍历全部 206 个 episode。
9. **未在真实数据上做逐帧的"视频帧 ↔ state/action"数值对齐验证**，
   只验证了形状与"两个 episode 首帧不同"。逐帧数值对应只在合成夹具上验证过。

---

## 10. 项目文档与源码不一致之处（**未擅自修改**）

### 10.1 一处过度声明

`docs/project-plan.md` 第 234 行写道：

> 本项目做的是图像的基本统计（梯度算清晰度、亮度分布判过曝、统计黑帧比例）

**但源码中不存在这些实现。** **[实测]** 在 `src/` 全量检索
`blur|BLUR|overexpos|OVEREXPOS|blurred|lapac|Laplacian|模糊|黑帧|清晰度|过曝|sharpness|black_frame`
**零匹配**；`src/embodiedsense/processing/validation.py` 只做格式、尺寸、有限性、深度范围检查
（完整读完该文件，共 43 行）。同一文档第 70 行把"模糊/黑帧"列为**待解决**的质量问题，
与该声明自相矛盾。

**我的处理：** 依据开发方案"不自动改需求"的规则，**我没有修改该文档**，
只在 `docs/phase-a-status.md` 的 C 栏记录，并请用户决定。
**请复核者评判：这个处理是否恰当。**

### 10.2 适配器夹具与真实数据的两处不完全一致 **[自评弱点]**

| 项目 | 真实 pusht | 我的合成夹具 | 影响 |
|---|---|---|---|
| `meta/tasks.parquet` 结构 | `task_index` + pandas 索引列 `__index_level_0__` | `task_index` + `task` 列 | 适配器目前**不读**该文件，故测试未覆盖；但夹具在此文件上**不忠实** |
| `meta/episodes/*.parquet` 列数 | 68 列（含大量 `stats/...`） | 12 列（只有适配器需要的） | 适配器按需读列（容错性好），但夹具**未覆盖 stats 列存在**的场景 |

---

## 11. 提交记录与文件清单 **[实测]**

### 11.1 本次两个提交

```
f2d0ccc  Complete phase A verification and harden adapter tests across platforms
         .github/workflows/ci.yml             |   2 +-
         docs/phase-a-status.md               | 119 +++++
         docs/verification-lerobot-adapter.md |  10 +-
         tests/lerobot_fixture.py             |  30 +-
         tests/test_lerobot_adapter.py        |  55 +-
         5 files changed, 209 insertions(+), 7 deletions(-)

f154a7b  Add LeRobot v3 adapter with explicit stream-delivery reporting
         docs/verification-lerobot-adapter.md     | 132 +++
         pyproject.toml                           |   3 +-
         scripts/download_lerobot_sample.py       |  81 +++
         src/embodiedsense/adapters/__init__.py   |  33 ++
         src/embodiedsense/adapters/lerobot_v3.py | 564 +++
         src/embodiedsense/cli.py                 |  45 ++
         tests/lerobot_fixture.py                 | 217 +++
         tests/test_lerobot_adapter.py            | 224 +++
         uv.lock                                  |   8 +-
         9 files changed, 1305 insertions(+), 2 deletions(-)
```

**提交基线：** 本次工作从 `11c64d6` 开始（`git log` 确认 `f2d0ccc` 的父提交链为
`f2d0ccc → f154a7b → 11c64d6 → d31694b → d7a6c6e → 5d58fe4`）。

### 11.2 新增文件及用途

| 文件 | 用途 |
|---|---|
| `src/embodiedsense/adapters/lerobot_v3.py` | 适配器主体，直读 v3 磁盘格式 |
| `src/embodiedsense/adapters/__init__.py` | 包导出（含状态常量） |
| `tests/lerobot_fixture.py` | 格式忠实的合成 v3 数据集构造器 + 编码器探测 |
| `tests/test_lerobot_adapter.py` | 17 项测试 |
| `scripts/download_lerobot_sample.py` | 可复现下载最小子集，逐文件校验 SHA256 |
| `docs/verification-lerobot-adapter.md` | 适配器验证记录（独立文件，未覆盖历史证据） |
| `docs/phase-a-status.md` | 阶段 A 三栏核查清单 |

### 11.3 修改的文件

| 文件 | 改动 |
|---|---|
| `pyproject.toml` | 新增 `lerobot = ["pyarrow>=15,<30"]` 可选依赖；`dev` 组加 pyarrow |
| `src/embodiedsense/cli.py` | 新增 `inspect-lerobot` 子命令（**惰性导入**适配器，核心不依赖 pyarrow） |
| `.github/workflows/ci.yml` | 显式安装 `--extra lerobot` |
| `uv.lock` | 随 pyproject 更新 |

**未改动**：`core/`、`processing/`、`sensors/`、`storage.py`、`visualization/`
以及既有的 `docs/project-plan.md`、`docs/verification.md`、`docs/roadmap.md`、
`docs/toolchain-integration-matrix.md`、`docs/next-phase-plan.md`、`docs/DATA_LICENSES.md`。

---

## 12. 关键设计决策与理由

### 12.1 不引入官方 `lerobot` 库，改为直读 v3 磁盘格式

**理由：** 官方包会引入训练栈（torch 等）。项目纪律要求 Core 保持 CPU-first、模型无关，
且"质检工具和 LeRobot 可用独立环境/可选依赖，不把它们强制塞入 CPU Core"。
因此适配器只用 `pyarrow` + OpenCV。

**代价（已在验证记录中声明）：** **不声称覆盖 LeRobot v3 的全部变体**；
`lerobot_v3.py` 的文件头注释明确记录了"本模块的格式事实取自真实数据集，不是假设"。

### 12.2 交付状态用三值而非布尔

`DELIVERED` / `NOT_PRESENT` / `UNAVAILABLE`。
**理由：** "这条流没到"与"读到了但读不完整"必须区分；
把未送达算作读取成功，正是本项目要测量的那类缺陷。

**注意：** 我使用了 `NOT_PRESENT` 而非开发方案第 169 行列的 `NOT_DELIVERED`。
方案中那四个状态是为**阶段 C 的 Findings** 定义的，我尚未进入阶段 C，
故没有提前占用那套词汇。**请复核者评判这个取舍是否合理。**

### 12.3 视频帧按需解码且有上界

`read_episode(decode_video=..., max_frames=...)`；
`max_frames` 限制单次解码量，`video_truncated` 如实标记。
**理由：** 高分辨率视频不应一次性拉进内存。

### 12.4 原始数据只读

适配器不写任何文件；`read_episode` 前后 SHA256 不变有测试断言。
下载脚本用 `.part` 临时文件 + 改名，且默认不覆盖已存在文件。

---

## 13. 请复核者重点审查的问题 **[自评弱点]**

以下是我自己识别出的问题与不确定之处，按重要性排序。**请优先审这些。**

### 13.1 一个我确认存在的不一致（请判断修法）

`read_episode` 中，当视频"部分解码成功"时：

```python
status = UNAVAILABLE if frames.shape[0] else NOT_PRESENT
deliveries.append(StreamDelivery(key, status, problem, 0, 1))
```

`delivered_episodes` 被硬编码为 **0**，但当 `frames.shape[0] > 0` 时实际是**部分送达**。
`StreamDelivery` 的字段语义无法表达"部分送达"。
**我判断这是真实缺陷**，但不确定正确修法（新增状态？把计数改成帧数？拆分字段？）。
请给出建议——**是否应现在就修，还是留到阶段 C 统一口径时一并处理？**

### 13.2 视频定位方式的可靠性

用 `capture.set(cv2.CAP_PROP_POS_FRAMES, round(from_timestamp * fps))` 做绝对跳帧。

**未验证：** 在 **av1** 长 GOP 视频上，按帧号定位是否精确？
真实数据上我只验证了起点为 0（episode 0）与起点为 161（episode 1）两种情况，
合成夹具用的是 mp4v。**是否存在更好的定位方式（如按毫秒 seek 后校验）？
是否需要"解出的首帧时间戳与元数据一致性"的断言？**

### 13.3 `frame_index` 缺失时的静默回退 **[自评弱点]**

```python
frame_index = (
    numeric["frame_index"].reshape(-1)
    if "frame_index" in numeric
    else np.arange(timestamp.shape[0], dtype=np.int64)
)
```

缺少 `frame_index` 时静默编造序号。这与项目"错误不得被静默抹除"的原则**相冲突**。
**是否应改为报 flag 或直接报错？**

### 13.4 `stream_manifest` 对数值流的判断过宽 **[自评弱点]**

数值流只要"列存在于 parquet schema"就报 `DELIVERED`，
`delivered_episodes` 直接填 `total`，**并未逐 episode 核对是否真有值**。
一个列全部为 `null` 的数据集会得到"全交付"的错误结论。
**是否需要逐 episode 的空值核查？代价是否可接受？**

### 13.5 `_parquet_columns()` 只看第一个存在的数据文件 **[自评弱点]**

若数据集有多个 data 文件且列不一致，`streams()` 的 `in_parquet` 判断会失真。

### 13.6 多 chunk / 多数据文件覆盖缺口

格式支持"每个 episode 映射到不同的 data/video 文件"，但
**没有任何测试覆盖该场景**（pusht 全部在一个 chunk 内）。
**请建议一个最小可行的多 chunk 夹具方案。**

### 13.7 其他较小的疑点

- 版本校验用 `str(version).startswith("v3")`，会接受 `v30`、`v3anything`。是否应收紧为语义化比较？
- `EpisodeData.units` 是**单一字符串** `dataset_defined`，不区分各流各自的单位，信息量偏弱。
- `_decode_video` 失败时返回形状 `(0, 0, 0, 3)` 的空数组，与真实帧形状不一致，调用方只能靠 `shape[0]` 判断。
- `read_episode` 每次调用都重新读 parquet，**无缓存**；遍历 206 个 episode 会重复解析。
- `pyarrow` 的 `filters=[("episode_index", "==", n)]` **实际下推效果未验证**（我假设它利用 row-group 统计）。
- `_read_numeric` 用 `np.asarray(list)` 处理定长列表；若遇到**变长列表**会产生 object 数组而未被检测。
- `slice_` 这个 walrus 变量名可读性差。

---

## 14. 独立复核方法

```powershell
cd D:\EmbodiedSense

# 1. 环境与基线
git log --oneline -6
git status --short                 # 应为空
uv run embodiedsense doctor

# 2. 全量测试与 lint
uv run pytest -q                   # 预期 37 passed
uv run ruff check .
uv run ruff format --check .

# 3. 真实数据端到端（需先下载最小子集，约 7.7 MB）
uv run python scripts/download_lerobot_sample.py
uv run embodiedsense inspect-lerobot --data-root data/pusht --episode 0 --max-frames 3
uv run embodiedsense inspect-lerobot --data-root data/pusht --episode 0 --no-video

# 4. 阶段 A 的 TUM 回归（真实数据已在 data/rgbd_dataset_freiburg1_xyz）
uv run embodiedsense run --config configs/tum_rgbd.yaml `
  --data-root data/rgbd_dataset_freiburg1_xyz --out outputs/review-check --limit 20
uv run embodiedsense replay --recording outputs/review-check --speed 0

# 5. 复核第 10.1 节的"过度声明"结论（预期：src/ 下无匹配）
#    在 src/ 下检索：blur|BLUR|overexpos|blurred|lapac|Laplacian|模糊|黑帧|清晰度|过曝|sharpness
```

**复核时请注意：**

- `data/` 与 `outputs/` 在 `.gitignore` 中，**不在仓库里**；
  没有本地数据时 `test_real_pusht_subset` 会自动跳过（这会让总数为 36 passed, 1 skipped）。
- **请勿覆盖 `outputs/` 下的历史目录**，用新目录名。
- 若在 Linux 上复核，请留意第 8.2 节的 mp4 编码器问题：
  无法编码时相关测试应**跳过**，若出现**失败**则说明我的守卫有问题，请报告。
- 仓库有 CRLF/LF 转换警告（Windows），与代码正确性无关。

---

## 15. 当前阶段结论与下一步

### 15.1 结论

- **阶段 A 通过**：现有功能未被破坏，接口与测试正常，三栏清单已产出。
- **阶段 B 切片 1 完成**：能列出全部模态与 episode，能读取含视频、动作、状态的 episode，
  有 17 项自动化测试，证据已归档。
- 一处文档过度声明已记录但**未修改**，等待用户决定（第 10.1 节）。
- 本报告第 9 节列出的 9 项内容**尚未验证**，第 13 节列出的 7 类问题**尚未修复**。

### 15.2 下一步（按开发方案，需用户确认后执行）

**阶段 C：跨工具诊断与统一 Findings**，要点：

1. 保持 `physlint`、`embodied-sync` 等第三方工具独立运行，逐个核实版本、许可、参数、实际输入输出
2. 整理为统一诊断项：来源工具、问题类别、严重程度、位置、证据、**检查是否执行**、**数据是否送达**
3. 严格区分 `NOT_DELIVERED` / `NOT_RUN` / `NOT_DETECTED` / `DETECTED`——**四者不可混用**
4. 用已知故障案例生成"故障 × 工具 × 数据送达状态"对照表，核验假阳性、漏报与未送达
5. 验收：两份等价输入得到语义一致的报告；
   **不得把"未送达"的故障统计成"质检算法漏检"**；对历史"0/8"结论以完整链路重新核验，不预设检出数

---

## 附录：本报告的事实来源

| 内容 | 来源 |
|---|---|
| 环境、测试数、TUM 回归数字 | 本次实际运行命令（第 4.1、7.1 节） |
| pusht 数据集的流数、episode 数、形状、时间戳语义 | `uv run embodiedsense inspect-lerobot` 实际输出（第 5.2 节） |
| 视频不在 parquet、声明大小不符 | 直接读 `data/pusht` 下的 parquet 与 `meta/info.json`（第 6 节） |
| 源码 API 面、质量标记清单 | 阅读 `src/embodiedsense/**`（第 4.2 节 A 栏、第 10.1 节） |
| 提交哈希、变更行数 | `git show --stat`（第 11.1 节） |
| 测试清单 | `uv run pytest --collect-only -q`（第 7.2 节） |
| 项目历史声明（120 组观测等） | 仓库 `docs/verification.md`，**已标为未验证**（第 4.2 节 B 栏） |

**本报告未包含任何我未实际运行或未实际读取的结论。**
