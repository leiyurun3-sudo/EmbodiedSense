# 工具链集成对照表（外部工具实测）

> **适用范围：** 本文评估的是**外部工具链**（physlint / embodied-sync / bagx / hflow）的实测行为，**不是本仓库 MVP 1 的实现**。结论用于判断下一步该做什么、不该做什么。

> **数据来源：全部为实测，不是读 README 抄的。**
> 执行环境：Windows + Python 3.12.14，独立工具链环境 `.venv-toolchain`（准备命令见 §6）
> 执行日期：2026-10-11
> 方法：用同一份**已知注入 8 种故障**的多模态运行数据，横向比较各工具"实际检出了什么"。
> 本文所有数字都可用第 6 节的命令原样复现。

---

## 0. 一句话结论

在同一份带已知故障的数据上，沿当前可行的交接路径：

| 工具 | 检出 | 说明 |
|---|---|---|
| **physlint 0.3.2** | **0 / 8**（拆解见 §0.1） | 对"干净数据"与"注入 8 种故障的数据"，18 条规则的状态与消息**逐条完全一致** |
| **embodied-sync 0.1.3** | **1 / 8**（且是漏报） | 报出 cam_front 缺 1 帧，而注入真值是 **7** 帧 |
| **bagx 0.6.0** | 未测 | 需要 rosbag2 `.db3`/MCAP 输入，本轮无可用输入 |
| **hflow 0.2.21** | **无法运行** | Windows 下 `ModuleNotFoundError: No module named 'fcntl'`（Unix 专有），Linux-only |

**但这不是"工具不好"，而是"交接坏了"。** 两个工具之间目前**没有可用的通用容器**：

- **LeRobot 路径**：`embodied-sync` 能用，但导出时**丢弃全部相机流**（视频载荷不转码）→ physlint 的 3 条视频规则永远 `not_run`，而我的 4 种故障恰好注在相机流上；
- **MCAP 路径**：`embsync --adapter mcap` 写出的**根本不是 MCAP**，而是一个自称 `embodied_sync.mcap.contract.v0` 的 9.4 万行 JSON 文件 → physlint 报 `invalid MCAP, invalid magic`。

> 这正好是 EmbodiedSense 存在的理由：**每个工具单独看都合格，接起来就漏。** 而这个"接不上"的损失目前没有人测量。

### 0.1 "0/8" 必须拆开看（v1.1 修正）

把"未送达"与"漏检"混为一谈，会**同时高估算法缺陷、低估交接缺陷**。按故障是否真的抵达 physlint 拆解：

| 分类 | 数量 | 具体故障 | 含义 |
|---|---|---|---|
| **未送达**（交接丢失） | **5 / 8** | `cam_front` 的 jitter / dropped_frames / burst_stall / duplicate_samples；`cam_wrist` 的 fixed_latency | 导出后的 LeRobot 数据集只含 `actions`/`robot_state`/`tactile` + 4 个记账字段，**相机流一条都没进去、视频文件数为 0**，physlint 从未收到这些故障 |
| **已送达但未检出**（算法漏检） | **3 / 8** | `robot_state` 的 clock_drift / missing_interval；`tactile` 的 non_monotonic | 数据确实进入了 physlint 的输入，18 条规则仍全部通过 |

证据：`meta/info.json` 的 `features` 仅列出 3 条数值流；数据集目录内 `.mp4` 数量为 **0**；
而原始 run 有 7 条流（actions / audio / cam_front / cam_wrist / events / robot_state / tactile）。

**因此正确表述是：交接环节丢掉了 5/8 的故障可观测性，工具在真正收到的 3 处上全部漏检。**

---

## 1. 环境与版本（实测）

| 组件 | 版本 |
|---|---|
| Python | 3.12.14 |
| physlint | 0.3.2（PyPI 自带 32 条规则；LeRobot 档位实际规划 18 条） |
| embodied-sync | 0.1.3 |
| bagx | 0.6.0 |
| hflow | 0.2.21 |
| 依赖 | mcap 1.5.0 / mcap-ros2-support 0.5.7 / pyarrow 26.0.0 / opencv-python-headless 5.0.0.93 / numpy 2.4.6 |

## 2. 工具可用性实测

| 工具 | 许可 | GitHub stars | 下载/月 | Windows | 真实 CLI 能力（实测） |
|---|---|---|---|---|---|
| physlint | MIT | 4 | 172 | ✅ | `check / inspect / rules / explain / init / compare / baseline` |
| embodied-sync | Apache-2.0 | 7 | 62 | ✅ | `synth / corrupt / align / report / calibrate / import-lerobot / import-qut / audit-lerobot-pts / inspect-dataset / verify / infer-import / import-auto / export-lerobot / export-umi` |
| bagx | MIT | — | 70 | ✅ | `benchmark / eval / report / diff / compare / sync / export / anomaly / scenario / ask / scene / **mcp**` |
| hflow | — | 293 | — | ❌ **崩溃** | 无法运行（`fcntl`） |

**两个意外发现：**

1. **`bagx` 已经自带 MCP server（`bagx mcp`）和一个 LLM 问答命令（`bagx ask`）** —— "把质检能力包成 agent 工具"这个想法，bagx 已经实现了。
2. **这批里最有名的 hflow（293 stars，其余全是 0–7 stars）在 Windows 上完全跑不起来** —— 说明这个生态的可用性远低于 star 数给人的印象。

## 3. 跨工具交接实测

### 3.1 LeRobot 路径（`embodied-sync → LeRobot v3 → physlint`）

流程：`synth → corrupt → align → export-lerobot → physlint check`

**发现 A：导出丢弃相机流。**
```
UserWarning: export_lerobot_dataset: skipping non-numeric stream 'cam_wrist'
             (video/image payload refs are not transcoded)
UserWarning: export_lerobot_dataset: skipping non-numeric stream 'events'
```
导出后的数据集只有 `actions / robot_state / tactile` + 记账列。**7 条流变 3 条。**

**发现 B：流命名不匹配，干净数据被判失败。**
physlint 默认契约要求 `action` 与 `observation.state`，而 embodied-sync 导出的是 `actions` 与 `robot_state`：

```
manifest.required_streams  failed  error
  "Required stream 'observation.state' is missing"
  "Required stream 'action' is missing"
```

→ **在完全干净的数据上，physlint 返回 exit=1 和 2 条 error 级 findings。这是集成造成的假阳性，不是数据问题。**

**发现 C：6 条规则永远跑不到。**

| 规则 | 状态 | 原因 |
|---|---|---|
| `video.decode` / `video.frozen_frames` / `video.black_frames` | `not_run` | `adapter lacks capabilities: video`（因为导出丢了视频流） |
| `temporal.observation_action_delay` | `not_run` | `adapter lacks capabilities: stream_timestamps` |
| `numeric.configured_bounds` / `numeric.discontinuity` | `not_run` | 未配置阈值（预期行为，需用户提供） |

### 3.2 MCAP 路径（失败）

```
physlint check <file>.mcap
  → Source error: invalid MCAP recording: not a valid MCAP file,
    invalid magic: (123, 13, 10, 32, 32, 34, 102, 111)
```
`(123,13,10,...)` 就是 `{` `CR` `LF` —— 该文件首行是：
```json
{
  "format": "embodied_sync.mcap.contract.v0",
```
**`embsync --adapter mcap` 产出的是 JSON 契约文件（94,296 行），不是二进制 MCAP。** 另外 physlint **按扩展名识别** MCAP（无后缀文件直接 `no adapter recognizes`），这一点也需要注意。

→ **结论：MCAP 通道目前不通，本轮无法用它做跨工具对照。**

## 4. 故障检出矩阵（核心交付物）

注入配置见 [`benchmarks/integration/profiles/kitchen_sink.yaml`](../benchmarks/integration/profiles/kitchen_sink.yaml)，8 种故障全覆盖。

| # | 注入故障 | 目标流 | 参数 | embodied-sync 对齐报告 | physlint（LeRobot 通道） |
|---|---|---|---|---|---|
| 1 | `fixed_latency` | cam_wrist | +45 ms | ❌ 未体现（`median_skew_ns = 0`） | ❌ 流已被丢弃 |
| 2 | `jitter` | cam_front | σ=8 ms, clip=30 ms | ❌ 未体现 | ❌ 流已被丢弃 |
| 3 | `dropped_frames` | cam_front | p=2% | ⚠️ **漏报**：报 `missing_count=1`，真值 `ground_truth_missing_count=7` | ❌ 流已被丢弃 |
| 4 | `clock_drift` | robot_state | 100 ppm | ❌ 未体现 | ❌ 未触发（导出后时间戳被重写为等间隔） |
| 5 | `burst_stall` | cam_front | ×3, 80 ms | ❌ 未体现 | ❌ 流已被丢弃 |
| 6 | `duplicate_samples` | cam_front | p=1% | ❌ 未体现 | ❌ 流已被丢弃 |
| 7 | `non_monotonic` | tactile | ×2 | ❌ 未体现 | ❌ 未触发 |
| 8 | `missing_interval` | robot_state | 40 ms | ❌ 未体现 | ❌ 未触发 |

**关于 physlint 的公平说明（重要）：** physlint 自己的发布门禁声称在自有夹具上做到 3/3 受控缺陷检出（非有限值、时间戳乱序、删除源行）。本表不是否定 physlint 的能力，而是说明：**当故障发生在"上游多流运行"里，并经过一次跨工具交接之后，physlint 看不到它们了。** 它自己的故障注入用的是它自己的夹具（`validation/harness.py`），不覆盖跨工具链路。

## 5. 结论：EmbodiedSense 的定位依据

三个**实测出来的**空白（不是推测的）：

1. **交接保真无人测量。** "导出丢了 7 条流里的 2 条（含全部相机）""命名不匹配导致干净数据被判失败"——这两件事没有任何工具会告诉你。
2. **统一口径不存在。** 两个工具的输出格式、严重度语义、退出码、契约默认值互不兼容，无法直接比较（physlint 明确拒绝输出"统一质量分"，这是对的，但也就没人做跨工具一致视图）。
3. **因果未验证。** 上游 1/8、下游 0/8 的检出率，意味着"清洗到底有没有让下游模型变好"这个问题根本还没有可靠的测量前提。

**因此明确不要做（否则就是第 8 个检查器）：**

- ❌ 自己实现检测规则（physlint 32 条、embodied-sync 8 类故障注入，重复劳动）
- ❌ 自己做 MCP 壳（`bagx mcp` 已经有了；M4 只做"统一报告"的封装，不做通用框架）
- ❌ 自己写故障注入引擎（`embsync corrupt` 的 schema 已经很完备）

**要做的是：跨工具交接保真 + 统一 finding schema + 预注册的下游评测。**

## 6. 复现步骤

```powershell
# 一次性准备独立工具链环境（不污染项目 .venv；.venv*/ 已被 .gitignore 覆盖）
$d="D:\EmbodiedSense"
python -m venv "$d\.venv-toolchain"
& "$d\.venv-toolchain\Scripts\python.exe" -m pip install "physlint[video]==0.3.2" embodied-sync bagx
$s="$d\.venv-toolchain\Scripts"; $w="$d\outputs\integration"
# 1) 干净数据
& "$s\embsync.exe" synth --out "$w\runs\clean" --seed 0 --duration-s 10
# 2) 注入 8 种故障
& "$s\embsync.exe" corrupt "$w\runs\clean" --profile "$d\benchmarks\integration\profiles\kitchen_sink.yaml" --out "$w\runs\bad"
# 3) 对齐到 10Hz 策略帧（带真值校验）
& "$s\embsync.exe" align "$w\runs\bad" --out "$w\episodes\bad_10hz" --target-rate-hz 10 --check-ground-truth
& "$s\embsync.exe" align "$w\runs\clean" --out "$w\episodes\clean_10hz" --target-rate-hz 10
# 4) embodied-sync 自己的报告
& "$s\embsync.exe" report "$w\episodes\bad_10hz" --out "$w\reports\bad.html" --json-summary "$w\reports\bad.json"
# 5) 导出 LeRobot 交给 physlint
& "$s\embsync.exe" export-lerobot "$w\episodes\bad_10hz" --out "$w\lerobot\bad" --task "integration-check"
& "$s\embsync.exe" export-lerobot "$w\episodes\clean_10hz" --out "$w\lerobot\clean" --task "integration-check"
& "$s\physlint.exe" check "$w\lerobot\bad"   --output json --json-output "$w\reports\physlint_bad.json"
& "$s\physlint.exe" check "$w\lerobot\clean" --output json --json-output "$w\reports\physlint_clean.json"
```

> 注：`outputs/` 与 `data/` 已被 `.gitignore` 排除，生成物不进版本库；只有本文件与故障配置进库。


