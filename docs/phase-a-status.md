# 阶段 A 现状核验记录

日期：2026-10-12。
对应《EmbodiedSense 正式开发执行方案》阶段 A 第 3–4 步：
在现有环境运行测试与诊断，并列出"已实现 / 文档宣称待复核 / 尚未实现"三栏。

本文只记录**实际运行**和**实际读源码**得到的结果，不引用文档结论作为事实。

## 1. 运行的命令与实测结果

| 命令 | 结果 |
|---|---|
| `uv run pytest -q` | **37 项通过**（原 20 + 本阶段新增 17），无失败、无跳过 |
| `uv run ruff check .` | All checks passed |
| `uv run ruff format --check .` | 35 files already formatted |
| `uv run embodiedsense doctor` | 正常输出，见下 |
| `uv run embodiedsense run --config configs/tum_rgbd.yaml --data-root data/rgbd_dataset_freiburg1_xyz --out outputs/phase-a-regression --limit 20` | 20 组观测，287,228 个三维点，状态全部 WARN |
| `uv run embodiedsense replay --recording outputs/phase-a-regression --speed 0` | 回放 20 组观测，与写入一致 |

`doctor` 实测：Python 3.12.5，Windows 11，24 逻辑 CPU，15.78 GiB RAM，
RTX 4060 Laptop GPU 8188 MiB 可枚举（`gpu_compute_tested: false`），
`core_device: CPU`，磁盘可用 472.11 GiB。
依赖：NumPy 2.5.3，OpenCV headless 4.14.0.94，PyYAML 6.0.3，Rerun SDK 0.29.2，
pyarrow 26.0.0。

**回归结论：现有 MVP 1 的接口与测试保持正常，阶段 A 通过条件满足。**
本次 TUM 回归写入新目录，未覆盖 `outputs/` 下任何历史记录。

## 2. A 栏：已实现，且本轮已实测验证

| 能力 | 代码位置 | 验证方式 |
|---|---|---|
| 索引读取与时间戳异常标记（重复 / 倒退 / 间隔过大） | `sensors/rgbd_loader.py:read_index` | 单测参数化覆盖四类非法输入 |
| 纳秒精度时间戳（Decimal 解析，int64 范围校验） | 同上 | 单测 |
| 离线最近邻同步，容差含边界，等距取较早者 | `processing/sync.py:associate` | 单测边界条件 |
| 深度复用被允许并显式标记 `DEPTH_REUSED` | `core/pipeline.py` | 集成测试 |
| 质量校验与状态（`OK/WARN/INVALID/MISSING`） | `processing/validation.py:validate` | 单测 + 集成测试 |
| 质量标记：`RGB_FORMAT_OR_SIZE`、`DEPTH_FORMAT_OR_SIZE`、`NONFINITE_DEPTH`、`UNSYNCED`、`INVALID_DEPTH_PIXELS`、`NO_VALID_DEPTH` | 同上 | 单测 + 集成测试 |
| 深度有效比例指标 `valid_depth_fraction` | 同上 | 单测断言 0.75 |
| 深度单位换算（uint16 ÷ depth_scale → 米） | 同上 | 单测 |
| 反投影生成米制彩色点云（含 stride 与深度范围过滤） | `processing/spatial.py:project_depth` | 单测数值断言 |
| 刚体变换校验（非刚体矩阵拒绝） | `processing/spatial.py:transform_points` | 单测 |
| 自包含记录（manifest + 逐帧 SHA256 + 完整性标志） | `storage.py:RecordingWriter` | 集成测试 |
| 回放校验（校验和、完整性、越界防护） | `storage.py:replay` | 集成测试 |
| 二进制彩色 PLY 导出 | `storage.py:write_ply` | 集成测试 |
| 拒绝覆盖已有运行目录（退出码 2） | `cli.py:run` | 集成测试 |
| CLI：`doctor` / `make-fixture` / `run` / `replay` | `cli.py` | 集成测试 + 本轮实跑 |
| 核心不导入 Rerun | `core/pipeline.py` | 子进程断言 `'rerun' not in sys.modules` |
| 无界面 Rerun 导出 | `visualization/viewer.py:RerunWriter` | 集成测试（rrd > 100 字节） |
| 合成 RGB-D fixture 生成 | `sensors/synthetic.py:make_fixture` | 集成测试 + CLI |
| **LeRobot v3 适配器**（列流 / 列 episode / 读单 episode / 交付状态） | `adapters/lerobot_v3.py` | 17 项新增测试 + 真实 pusht 实测 |

## 3. B 栏：文档有声明，但本轮未独立复核

这些是历史记录或未在本轮重跑的项目。**引用时应说明是历史值，不是本轮实测。**

| 声明 | 出处 | 状态 |
|---|---|---|
| TUM 120 组观测：1,731,525 点、同步差 P95 16.86815 ms、8.26 组/s、RSS 峰值 100.60 MiB | `docs/verification.md` | 本轮只跑 20 组，**120 组的数字未复现** |
| 120 帧均 WARN、其中 9 帧含 `DEPTH_REUSED` | 同上 | 未复核该细分计数 |
| `rerun rrd verify` 通过（1 file verified without error） | 同上 | 本轮未重跑 Rerun 导出与校验 |
| `uv build` 成功生成 wheel 与 sdist | 同上 | 本轮未重跑 |
| GitHub Actions 在 Windows/Linux 上执行 | `.github/workflows/ci.yml` | 只审阅了配置；**远端执行结果未核实**，且本轮修改了该文件，新配置尚未在远端跑过 |
| Rerun GUI 视觉验收 | `docs/verification.md` 明确表示未做 | 保持"未做"，本项目不声称做过 |

## 4. C 栏：文档宣称但源码中**尚未实现**

以下由读源码确认（含目录存在性检查），不是推测。

### 4.1 需要纠正的一处文档过度声明

`docs/project-plan.md` 第 234 行写道：

> 本项目做的是图像的基本统计（梯度算清晰度、亮度分布判过曝、统计黑帧比例）

**源码中不存在这些实现。** 在 `src/` 全量检索 `blur` / `BLACK` / `overexpos` / `Laplacian`
等关键词**零匹配**；`processing/validation.py` 只做格式、尺寸、有限性、深度范围检查。
同一文档第 70 行也把"模糊/黑帧"列为待解决的质量问题。

**处理建议：** 阶段 C/D 实现视频质检后再恢复该表述，或现在就把它降级为"计划中"。
在实现之前，不应在任何对外材料中声称具备清晰度与过曝检测能力。

### 4.2 尚未实现清单

| 方案条目 | 内容 | 现状 |
|---|---|---|
| FR-07 / FR-08 / FR-10 | 统一 Findings 口径、跨工具报告 | 无 `quality/`、`reporting/` 目录 |
| FR-09 | LeRobot v3 导出器（保留视频） | 无 `exporters/` 目录；当前只有**读取** |
| FR-10 | physlint / embodied-sync 子进程集成 | 无 `integrations/` 目录 |
| 阶段 D | 交接保真检查（流/帧/时间戳/容器） | 未实现 |
| FR-12 | 处理后数据服务接口 | 无 `api/` 目录 |
| FR-13 | MCP / Agent 只读工具 | 未实现 |
| FR-11 | 下游影响评测（预注册实验） | 未实现 |
| — | IMU / LiDAR / EuRoC / KITTI 适配器 | 未实现 |
| — | 图像模糊、过曝、黑帧检测 | 未实现（见 4.1） |
| — | 特征 `timestamp` 跨来源语义归一 | 适配器只如实标注，未做统一 |

`roadmap.md` 列出的 5 项后续里程碑（AI 插件、空间状态、模型增强实验、ROS 2、边缘部署）
同样均未实现，该文档本身已如实标注。

## 5. 结论与关卡判定

- 阶段 A **通过**：现有功能未被破坏，接口与测试正常，三栏已列清。
- 阶段 B 切片 1 **已在同一轮完成**，证据见 `docs/verification-lerobot-adapter.md`。
- 一处文档过度声明（4.1）已记录，**未擅自修改** `project-plan.md`——
  按方案通用工程规则"不自动改需求"，是否修正该表述由项目所有者决定。

## 6. 复现命令

```powershell
cd D:\EmbodiedSense
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv run embodiedsense doctor
uv run embodiedsense run --config configs/tum_rgbd.yaml `
  --data-root data/rgbd_dataset_freiburg1_xyz --out outputs/phase-a-regression --limit 20
uv run embodiedsense replay --recording outputs/phase-a-regression --speed 0
```
