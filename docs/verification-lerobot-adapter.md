# LeRobot v3 适配器验证记录

日期：2026-10-12。以下结果由命令实际运行产生，不是目标值。
本文是新增记录，不修改 `docs/verification.md` 中 MVP 1 的历史证据。

对应开发方案阶段 B 切片 1：列出数据集模态与 episode，并读取一条含视频、
动作和状态的 episode，记录全部流是否保留。

## 环境

Windows 11，Python 3.12.5。uv 0.13.0。NumPy 2.5.3，OpenCV headless 4.14.0.94，
pyarrow 26.0.0。核心在 CPU 上运行，未使用 GPU。

`observation.image` 的编码是 **av1**，本机 OpenCV 能正确解码（已实测）。

## 数据来源

`lerobot/pusht`（HuggingFace，`codebase_version: v3.0`）的**最小子集**，
只取 adapter 需要的 6 个文件，共 **7,680,844 字节**，不是完整仓库。

```powershell
uv run python scripts/download_lerobot_sample.py
```

脚本逐文件校验 SHA256（实测值写在脚本内，不是发布方签名）。
文件清单与哈希见 `scripts/download_lerobot_sample.py` 的 `MANIFEST`。

**网络说明：** 本机通过本地加速器访问 HuggingFace。数据文件走
`huggingface.co/datasets/<id>/resolve/main/...` 通道可正常下载；
`huggingface.co/api/...` 端点在本机返回 401，与本项目无关，仅记录备查。

## 自动验证

- `pytest -q`：**37 项通过**（原 20 项 + 新增 17 项），无失败、无跳过。
- `ruff check .` 与 `ruff format --check .`：通过。
- 新增测试文件：`tests/test_lerobot_adapter.py`（17 项，其中 1 项针对真实子集，
  子集不存在时自动跳过）。

**跨平台稳健性：** 依赖视频文件的测试先探测本机 OpenCV 能否写出 mp4
（`lerobot_fixture.mp4_encoder_available()`）；不能写时这些测试**跳过而非失败**，
避免 CI 在缺少编码器的平台上产生与环境相关的红灯。
该守卫本身也有单元测试覆盖（`test_platform_without_mp4_encoder_is_detected`）。
本机实测编码器可用，因此 17 项全部实际执行，无跳过。

合成 fixture（`tests/lerobot_fixture.py`）构造格式忠实的 v3 数据集，
且**视频帧的灰度值编码了全局帧号**，因此测试能验证 episode 与视频帧的映射，
而不只是"能读出来"。这是纯合成数据，已在文件头标注。

覆盖的关键断言：

| 断言 | 目的 |
|---|---|
| 视频流 `in_parquet == False` | 证明相机流不在 parquet 里，是丢失的根源 |
| episode 1 解出帧号 4–6（不是 0–2） | 证明 `from_timestamp → 帧号` 映射正确 |
| `read_episode` 前后 parquet 与视频 SHA256 不变 | 证明原始数据未被原地修改 |
| 删除 mp4 后视频报 `NOT_PRESENT`，state/action 仍 `DELIVERED` | 缺失流必须被报告，不能被当成读取成功 |
| `max_frames=2` 时只解 2 帧且 `video_truncated=True` | 解码内存有上界 |

## 真实数据实测

命令：

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
| 其中数值流 | 7（含 `observation.state`、`action`、`timestamp`） |
| 其中计数流 | 4（`episode_index`、`frame_index`、`index`、`task_index`） |
| `observation.image` 是否在 parquet 中 | 否 |
| `observation.image` 编码 | av1 |
| 交付状态 | 11/11 流 `DELIVERED`，视频覆盖 206/206 episode |

读取 episode 0：

| 指标 | 实测值 |
|---|---:|
| 帧数 | 161 |
| `observation.state` 形状 | (161, 2) |
| `action` 形状 | (161, 2) |
| 视频解码形状（`--max-frames 3`） | (3, 96, 96, 3) |
| 时间戳范围 | 0.0 – 16.0 s |
| `clock_domain` | `episode_relative` |
| 时间戳单位 | second |
| 单位来源 | `dataset_defined`（适配器不做换算） |

episode 1 的视频切片起点为 16.1 s，与其 161 帧的前序 episode 长度一致，
说明分块视频按 episode 顺序拼接、按时间区间切分。

## 本次核验到的三个格式事实

这三条都是读真实数据得到的，不是根据文档推断：

1. **视频流是独立文件，不在 parquet 中。** `info.json` 声明
   `observation.image` 为 `dtype: video`，但 data parquet 只有数值列。
   只复制 parquet 的转换会**静默丢掉整条相机流**，这正是本项目要测量的交接缺陷。
2. **`timestamp` 是 episode 内相对秒，从 0 开始，不是 epoch 时钟。**
   适配器显式记录 `clock_domain = episode_relative`，不把它当成墙钟时间使用。
   这是上游数据集的真实约定，与 TUM 的绝对时间戳语义不同。
3. **`info.json` 声明的大小与实际文件不符。**
   元数据写 `data_files_size_in_mb: 100`、`video_files_size_in_mb: 500`，
   而本数据集实际发布的文件是 0.67 MB 与 6.9 MB。
   适配器因此**分别报告"声明值"和"实际列出值"**，不信任声明。
   （这里只陈述观察到的差异，不断言上游字段的确切含义。）

## 局限

- 只下载了 `chunk-000/file-000`。对 pusht 而言这已包含全部 25,650 帧
  （parquet 行数与 `total_frames` 相等，视频帧数亦相等），但该结论**只对本数据集成立**，
  不代表其他数据集的分块方式。
- 只解码了 episode 0（161 帧）与 episode 1（118 帧）用于验证，未遍历全部 206 个 episode。
- 未接入 LeRobot 官方库；适配器直接读取 v3 磁盘格式。理由是官方包会引入训练栈，
  而 Core 保持 CPU-first、模型无关。**因此本适配器不声称覆盖 v3 的全部变体。**
- 未做下游训练或策略实验。本记录只支持"数据被完整读入"这一结论，
  不支持任何"模型表现提升"的结论。
- 视频逐帧解码，未做随机访问性能基准；`max_frames` 只用于限制单次解码量。

## 复现命令

```powershell
cd D:\EmbodiedSense
uv run python scripts/download_lerobot_sample.py
uv run pytest -q
uv run embodiedsense inspect-lerobot --data-root data/pusht --episode 0 --max-frames 3
uv run embodiedsense inspect-lerobot --data-root data/pusht --episode 0 --no-video
```

`--no-video` 实测结果：`image_shapes` 为空、`video_truncated` 为 `false`、
`frames_read` 仍为 161，视频交付状态为 `DELIVERED`，原因为
"chunk video present; decoding not requested"。
即"不请求解码"与"流缺失"被明确区分，二者不会混淆。
