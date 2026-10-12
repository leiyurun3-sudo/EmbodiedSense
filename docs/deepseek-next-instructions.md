# 给 DeepSeek 的下一轮执行指令

你接手现有 `D:\EmbodiedSense`，在 Codex 的 2026-10-12 收尾基础上继续开发。
本轮主题：**FR-12 处理后数据服务的最小可消费闭环**。
无需重新做竞品分析、创新性论证或把总纲所有阶段一起实现。

交接时 Codex 修复保存在本地审查分支 `codex/review-handoff-20261012`，
尚未发布 GitHub main。先检查实际分支与工作区，在该修复基础上继续；
不要强行切回旧 main、reset 或覆盖修复。若用户后来已批准合并，以实际 Git 状态为准。
向默认 main 发布需要用户对本次发布的明确确认，不自动推送。

## 1. 用户目标与执行优先级

EmbodiedSense 为机器人/智能机器的上层程序提供可用的感知数据。
主产出必须是图像、深度/点云、状态、动作、时间与来源信息；质量报告是并行证据。
不能只交付 inspect JSON、findings 或新的商业计划。
本轮先让一个独立 Python 消费者通过统一入口取得真实数据。

项目位于 D 盘，工具为 `D:\DevTools\uv\bin\uv.exe` / `./dev.ps1`，Python 3.12。
核心 CPU-first、模型无关，不要求 GPU、实体机器人或联网。
用命令/API，不操纵电脑屏幕，不自动打开 Rerun。

## 2. 开始前核验，不根据交接文字猜状态

依次读取：`AGENTS.md`、README、`docs/codex-review-20261012.md`、本文件、
总纲 v1.2 和适配器/现有 pipeline 源码。检查 `git status`、分支和最近提交。
保留用户未提交改动和旧项目目录，不重新 init，不删除 `EmbodiedSense_Startup`。
基线测试数量会随代码改变，用实际输出记录；不得复制“37/63 passed”冒充本轮运行。

```powershell
cd D:\EmbodiedSense
git status --short --branch
git log -5 --oneline
./dev.ps1 sync --locked --extra viz --extra lerobot
./dev.ps1 run pytest -q
./dev.ps1 run ruff check .
./dev.ps1 run ruff format --check .
./dev.ps1 run embodiedsense doctor
```

若基线失败，先定位。现有数据不下载第二份；如必须获取新样本，先说明体量和必要性。
网络代理只可按实际可用性使用单次命令覆盖，不改全局配置，不透露凭据。

## 3. 已收尾内容，不要回退或重复做

- `PRESENT` 是文件存在；`PARTIAL` 是预览/部分读取；完整指定 episode 解码才可 DELIVERED。
- 保留 `inspection_basis`、present/delivered episode 计数及 read/expected frames。
- 缺帧号不造 `arange` 冒充源序号；索引/行数/时间不满足契约时显式异常。
- optional numeric 流 null/NaN/shape 错误为 UNAVAILABLE，健康流可以继续交付。
- 多文件数值清单有批量扫描，不用第一份 schema 推断全部文件。
- 视频采用已验证 CFR 顺序定位；不恢复未经核验的 `capture.set`。
- 未知单位为 None；禁止猜测 PushT state/action 单位为米。
- 旧目录在 Git/Ruff 排除项里；保留物理目录，不批量格式化旧代码。

## 4. 本轮实现切片

先列出最小修改文件，再逐项实现；不要生成大量空壳包。
建议新增 `src/embodiedsense/api/service.py` 和必要的数据契约，
复用 `RGBDPipeline.iter_observations()`、`LeRobotV3Dataset`，避免另一套解码/几何引擎。
如果现有等价模块已经存在，在原位置扩展。

### 4.1 一个一致的只读入口

提供一个服务对象或工厂，显式选择来源类型、目录和必要配置。
至少支持 TUM RGB-D 与 LeRobot v3；提供 inspect 与 `iter_observations` 风格的调用。
保持现有 API 可用。不要为统一外观把两种数据硬塞成完全相同的物理语义。

每次输出一条结构化观测，至少含：

- source/episode/frame identity 和源文件引用。
- 原始时间戳与单位、clock domain；视频片段文件时间与 episode 相对时间分开。
- 原始流名与可选语义映射；改名时保留 map，不静默推断 action/state。
- 图像、深度、点云、状态、动作等载荷或按需引用；缺失字段显式 None/缺失状态。
- 每路单位及来源；未知保持 None，不做未经允许的换算。
- 标定 ID、frame_id、已有几何变换来源；没有时保持未知。
- 交付状态、质量标记、检查覆盖范围；不得把未检查称为通过。
- 派生处理版本和配置，以及它关联的原始观测。

TUM 输出已有相机坐标下的米制彩色点云；LeRobot/PushT 输出图像、state/action，
没有深度或相机标定时 points=None 且有明确原因，不运行单目模型补造米制 3D。

### 4.2 逐帧消费与资源边界

不得调用 `list(read_all_episodes())` 缓存全数据集；图像逐帧/有界 batch 交付。
现有 read_episode 可用于小样本，但不能给 `max_frames=3` 的预览再搭配 161 行
数值，谎称已提供逐帧对齐后的完整观测。
如果只支持当前已验证的一视频帧对应一数值行，就写清限制；多速率没有映射时明确拒绝。

可在适配器新增只读迭代能力，或提供最小有界 chunk 消费器。限制必须真的作用在解码量，
不能先完整解码再 slice。不要用整个文件的无上限缓存解决性能问题。
优先维护现有 CFR 精确定位；PTS/VFR 与快速随机访问作为后续项。

### 4.3 数据处理而不只是文件搬运

TUM 复用已有同步、有效像素选择、深度换算与投影结果。
LeRobot 保留真实视频—state/action 配对、episode 边界、时间与字段约束，
向消费者交付配对观测，不能只返回数据集摘要。
状态/动作默认保持源数据精确关联，不插值、不重新采样、不自动删坏帧。
不认识单位、标定或时钟映射时，不作假设。

原始数据绝不原地修改。若增加保存处理后结果，只写新目录，记录源指纹和处理参数，
默认不覆盖已有 outputs；本轮不必实现完整 LeRobot 写入器。

### 4.4 质量是并行产出

数据消费不需要先生成全数据集报告，也不需要调用外部网络/Agent。
质量查询共用适配器/核心的实际证据，不重复实现 physlint 的检测规则。
对于读取契约失败，返回明确错误或结构化无效观测，记录原始位置；禁止默默填零或跳过不记账。

读取层 PRESENT/PARTIAL/DELIVERED 与检查层 NOT_DELIVERED/NOT_RUN/NOT_DETECTED/DETECTED
是不同维度。不得凭文件存在就说规则执行，也不得把规则未适用当作漏检。
本轮不扩建跨工具编排器；后续统一 Findings 再做独立语义映射。

## 5. 本轮交付与验收

新增 `examples/use_data_service.py`（或等价位置）作为独立消费者，
从命令行选择来源/episode/最大观测数，调用服务而非内部报告命令。
默认不显示 GUI；打印 shape、源时间、单位/坐标与覆盖状态，并做真实的消费者操作，
例如对 TUM 有效点云求包围盒、读取 PushT 的相应图像和 action 对。
没有语义检测时，不把几何包围盒叫“识别到了物体”。

至少覆盖下列自动测试：

1. 两种来源都可通过同一服务入口迭代；不破坏现有 RGBD/LeRobot API。
2. 合成真值验证逐帧图像—action/state 配对、episode/chunk 切换，容差小于相邻帧差异。
3. TUM 保留米制几何数值正确性；PushT 的点云与未知单位保持未知。
4. 缺模态、坏 optional 流、无索引、截断视频和关键格式失败都有明确行为，不静默补造。
5. clock domain 不混用；视频文件 offset 与 episode timestamp 不直接当同一时间。
6. 有界解码的调用/产出数量可验证；不以只检查输出 shape 证明内存有界。
7. 原始目录内容 SHA256 不变，派生输出不覆盖已有路径。
8. CPU/no-viewer/no-network 的服务示例可运行，核心不 import Rerun、torch 或 ROS。
9. 真实本地 PushT 至少两个 episode 与 TUM 小片段验证；无数据时如实跳过，不能伪造实测。

验收主线：**第三方程序拿到正确配对、可追溯的数据**。
不要把 finding 行数、预设检出 ≥3/8、模型提升百分比当本轮验收。

## 6. 输出证据和提交

每个切片运行相关 tests，再跑完整 pytest、ruff check、ruff format --check。
重跑已有 TUM 最小流程时使用新目录，并确认回放与 RRD 结构检查。
用独立文件记录实际命令、数据范围、样本数、来源是合成/真实、成功/失败/跳过与原因。
需要性能证据时记录硬件和测试方法，不能复制旧的 8.26 组/s 当作新接口性能。

完成后更新 README、FR-12 状态与验证记录；第三方许可明确记载，
不覆盖历史 review-handoff.md / verification.md。只提交源码、小型配置、测试和文档。
提交前查看暂存文件清单，不提交 data/outputs/dist/环境、凭据或旧项目目录。

## 7. 本轮终点与后续顺序

完成统一数据服务、独立消费者、边界测试和真实验证后交付本轮结果。
不要自动开始模型训练、多源时钟学习、ROS/SLAM、GUI、MCP 或所有 B–G 阶段。
若本轮因现有格式限制无法完成关键步骤，提交已验证的最小改动和具体阻塞证据，
保留其他正常能力，禁止伪装完整完成。

后续再按依赖推进：统一 Findings → 含视频的规范导出及独立读取器回读 →
小规模预注册下游比较（clean/corrupted-unprocessed/corrupted-processed）→ 可选 MCP。
第三方工具输出必须保留原始证据；先证明数据交接完整，再谈检测率和模型收益。
