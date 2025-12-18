# PLAN.md — 地震系统多源数据处理与关联建模（毕业设计，工程落地版）

> 核心目标：把“多源数据处理 + 事件关联 + 特征/异常 + API”做成**可长期运行、低遗漏、可验收**的工程流水线；每一步都能用脚本/测试给出客观证据；最终按 `project_task_list.csv`（对应映射：`docs/task_map.md`）逐条闭环推进与交付。

## 0.0 当前仓库可执行入口（已落地）

- 环境安装：`python -m pip install -r requirements.txt` + `python -m pip install -e .`
- 端到端流水线：`python -m exportblock.cli run --config configs/demo.yaml`
- API 服务：`python -m exportblock.cli api --config configs/demo.yaml`
- 自动化测试：`python -m pytest`

---

## 0. 项目信息与原则

### 0.1 项目名称
- 地震系统多源数据处理与关联建模

### 0.2 数据源（必须支持）
1) **地磁：IAGA2002（sec/min）**  
   - 站点：`kak/kny/mmb`，地磁数据：`sec/min`（秒级/分钟级）
   - 对应源：`geomag`（地磁）
2) **地震波：MiniSEED + stations_inventory.xml**  
   - 必须包含波形数据，台站信息通过 `stations_inventory.xml`（FDSN StationXML）
   - 可选：SAC（如果存在，提供数据处理）
3) **大气电磁：AEF（IAGA2002 风格）**
   - **分钟级数据**：按天分文件（约 1440 行/天，允许缺失）
   - **小时级数据**：按月分文件（约 24*days 行/月，允许缺失）
4) **VLF：CDF（每小时分钟级数据，≈60 点）**

### 0.3 工程实践原则（避免“理论正确但不可落地”）
- **不插值高频原波形到分钟/秒网格**：如地震波和秒级地磁数据，先提特征或聚合再对齐。
- **缺失/哑元（dummy/sentinel）必须显式处理**：如 AEF 的 `X/Y/F` 组件，若为 `88888.00` 或类似值，需标记为缺失，避免污染特征与相关性。
- **所有数据变换要可追溯**：每个数据处理环节都需要产生对应的 DQ（数据质量）报告，包含行数、时间范围、缺失率、异常率、join 覆盖率等。
- **两轮交付**：第一轮端到端跑通所有模块，第二轮回归测试与查漏补缺（论文答辩阶段可交付的稳定版本）。

---

## 1. 目标与客观验收标准（全局）

### 1.1 目标
1) **多源数据预处理**：去噪/异常/缺失处理，格式标准化，压缩存储  
2) **时间对齐**：统一到可配置时间栅格（默认 1min，可选 30s）  
3) **空间匹配**：根据给定震中点和半径 K(km)，查询台站数据  
4) **与地震标记关联**：通过时间窗（前 N 小时，后 M 小时）和空间窗（K km）  
5) **特征提取**：统计特征 + 信号特征（梯度、P/S 到时、频谱峰值等）  
6) **简单关联模型**：阈值/相似度匹配，输出异常分数与标记  
7) **FastAPI**：提供 raw/standard/linked/features 查询接口

### 1.2 全局验收口径（硬性）
- **统一 schema**：输出字段：`ts_ms, source, station_id, channel, value(s), lat, lon, elev, quality_flags`
- **时间**：统一 UTC，主字段 `ts_ms` 精确到毫秒
- **坐标**：WGS84（lat/lon，单位：度）
- **每阶段必须产出** `outputs/reports/*.json`，包括：
  - `rows`、`ts_min/ts_max`、`missing_rate`、`outlier_rate`、`station_count`、`join_coverage`
- **端到端 demo 一键可跑**：支持分阶段、全阶段运行，自动生成产物
- **API 冒烟测试**：使用 httpx，确保返回状态 200 且字段齐全

---

## 2. 统一数据规范（Standard Schema 与文件产物）

### 2.1 标准化记录（建议 Parquet；也可 HDF5）
**最小字段：**
- `ts_ms` (int64): UTC 毫秒时间戳
- `source` (str): `geomag | seismic | aef | vlf`
- `station_id` (str): 
  - 地磁/AEF：IAGA CODE（如 KAK）
  - 地震：`NET.STA.LOC.CHAN`（LOC 可为空，用空串表示）
- `channel` (str): 如 `X/Y/Z/F`、或 `BHZ`、或 `VLF_xxx`
- `value` (float): 主数值（必要时扩展 `value2/value3`）
- `lat/lon/elev` (float): WGS84；缺省允许为 NaN（但地震波在 join 后应尽量非空）
- `quality_flags` (json): 见 2.3

### 2.2 元数据（metadata.json）
- 原始文件清单与时间覆盖范围
- IAGA2002 header 关键字段（Reported、Units、Resolution、Interval Type）
- MiniSEED ↔ StationXML 匹配统计（matched_ratio、downgrade_count、unmatched_keys_topN）
- 参数快照（config hash）、pipeline_version、运行环境版本（python/依赖）

### 2.3 quality_flags 规范（必须统一）
建议字段：
- `is_missing` (bool)
- `missing_reason` (`sentinel|gap|parse_error|unknown`)
- `is_interpolated` (bool) + `interp_method`
- `is_outlier` (bool) + `outlier_method` + `threshold`
- `is_filtered` (bool) + `filter_type` + `filter_params`
- `station_match` (`exact|downgrade|unmatched`)
- `note`（可选）

---

## 3. 配置参数规范（无歧义，可复现）

统一 `configs/*.yaml`，并把最终使用的 config copy 到 `outputs/reports/config_snapshot.yaml`。

### 3.1 time
- `timezone: UTC`
- `align_interval: 1min`（默认）；可选 `30s`
- **对齐策略（强制规则）**：
  - `seismic_waveform`：不直接插值原波形；先提特征，再对齐
  - `geomag_sec`：聚合到 align_interval（mean/std/min/max/ptp/梯度等）
  - `geomag_min/aef_min/aef_hor`：若 align_interval < 原间隔：
    - 默认：不数值插值（避免造假信号）
    - 可选：forward-fill（必须标记 `is_interpolated=true`，且只用于 join，不用于频谱类特征）

### 3.2 preprocess
- `geomag_detrend: kalman`（或 rolling_median；二选一，默认 kalman）
- `seismic_bandpass_hz: [0.5, 20.0]`（默认，适配 20/40 Hz 常见采样；可配）
- `em_notch`：
  - `freq_hz: 50`（或 60）
  - `bandwidth_hz: 5`（新增）
- `missing_fill`：
  - `short_gap_max_points`: 默认 5（分钟序列：5 点；秒序列需单独配置）
  - `method: linear`（或 spline）

### 3.3 link
- `N_hours: 72`（默认，可配）
- `M_hours: 24`（默认，可配）
- `K_km: 100`（默认，可配 50–200）

### 3.4 storage
- `format: parquet`
- `compression: zstd`（或 snappy；以可安装为准）
- `chunking`: 可选（HDF5 用）

---

## 4. 模块设计、标准方案与客观验收（逐阶段闭环）

> 每个阶段必须输出对应 DQ 报告：`outputs/reports/dq_<stage>.json`

### A. Ingest（解析与入库）

#### A1 IAGA2002 解析器（地磁 + AEF 共用）
**标准方案：** 解析 header → 解析数据表 → 识别缺失 sentinel → 输出长表 records。

- 输入：IAGA2002 文本（sec/min/hour）
- 关键工程细节：
  - header 解析字段：IAGA CODE、lat/lon/elev、Reported、Units、Resolution、Interval Type
  - sentinel/dummy 处理：
    - 若遇到 `88888.00`/`99999.00`/`99999.90` 等（以真实数据为准，允许在 config 中扩展列表），视为缺失并打标
    - AEF 文件可能声明 “X/Y/F components are dummies”，则：
      - 默认只把 `*_Z` 作为有效通道（单位 V/m，downward+）
      - `X/Y/F` 要么丢弃，要么保留但全部标记 missing_reason=sentinel（在 metadata 中记录策略）
- 验收（客观）：
  1) 单测：fixtures 至少覆盖 1 个地磁 min、1 个地磁 sec、1 个 AEF min、1 个 AEF hour；解析后列/通道正确
  2) dq_ingest_iaga.json：rows>0；ts_min/max 正确；missing_rate/outlier_rate 统计可用
  3) AEF：dummy_rate（X/Y/F sentinel 占比）写入报告，且 Z 通道非全缺失

#### A2 MiniSEED ingest（ObsPy 标准实现）
**标准方案：** obspy.read → Stream/Trace → 提取 stats → 保存波形摘要（不必把全波形写入标准长表，除非做 raw 存档）。

- 输入：MiniSEED
- 产物：
  - raw 波形：保留原 mseed（或复制到 data/raw_manifest）
  - 解析摘要：trace 清单（net/sta/loc/chan/start/end/sampling_rate/npts）
- 验收：
  1) 单测：能读取到 Trace；`sampling_rate`、`npts` 与 ObsPy 打印一致
  2) dq_ingest_mseed.json：trace_count>0；每条 trace start/end 合法

#### A3 StationXML ingest + 与 MiniSEED 匹配（无歧义 join 规则）
**标准方案：** StationXML 展开到 Channel level；按 NET/STA/LOC/CHAN + 时间 epoch 精确匹配。

- Join Key：
  1) 精确匹配 `(net, sta, loc, chan)`
  2) 若 trace.loc 为空：
     - 先匹配 `locationCode=""`
     - 找不到再降级匹配 `"00"`（记录 downgrade_count）
  3) 若存在多个 epoch（startDate/endDate）：按 trace.starttime 落在 epoch 内选择
- 验收（客观）：
  - `matched_ratio = matched_traces / total_traces`
  - 默认要求 `matched_ratio >= 0.99`（若数据自身不满足，需在报告解释并调整阈值）
  - 产出 `outputs/reports/station_match.json`：
    - matched_ratio、downgrade_count、unmatched_keys_topN、epoch_conflict_count

#### A4 SAC（可选）
- 若存在：用 ObsPy 读取，提取内置 lat/lon；验证波形长度>0
- 验收：dq_ingest_sac.json 记录文件数、成功率

#### A5 VLF CDF ingest（标准库）
- 输入：CDF（单文件约 1 小时分钟级）
- 验收：
  - dq_ingest_vlf.json：rows≈60（允许缺失），时间跨度≈1h（误差容忍 1–2 分钟）

---

### B. Preprocess（去噪/异常/缺失）

**标准方案：**
- 缺失：sentinel → NaN；短缺口插值；长缺口保留 NaN
- 异常：MAD 或 3-sigma（同一 source 内统一）
- 滤波：
  - 地震：bandpass（默认 0.5–20Hz，适配 20/40Hz；参数可配）
  - 电磁：notch 50/60Hz（freq±bandwidth/2）
- 地磁漂移：kalman/rolling_median detrend（可解释且工程可实现）

**验收（客观）**
1) 单测：人为构造缺失/异常 → flags 命中率=100%（对 fixture）
2) `outputs/reports/filter_effect.json`（电磁 notch 必须给证据）：
   - 定义：滤波前后计算指定频带功率 `P_band`
   - 频带：`[freq - bw/2, freq + bw/2]`
   - 验收阈值（默认之一，二选一）：
     - `power_drop_db >= 10 dB` 或
     - `power_drop_ratio >= 0.70`
   - 报告输出：freq、bw、drop_db、drop_ratio、样本数量

---

### C. Align（时间对齐，避免造假信号）

**标准方案：**
- 统一生成 `ts_grid`（UTC，step=align_interval）
- 各 source 对齐规则：
  - geomag_sec：按 grid 聚合（mean/std/min/max/ptp/梯度）
  - geomag_min / aef_min / aef_hor：按 grid 重索引；细粒度 grid 默认不插值
  - seismic_waveform：先做分钟/30s 窗口特征（见 F），再对齐
  - vlf：原本分钟级，按 grid 对齐
- 输出：`aligned.parquet`（长表或宽表皆可，但必须能 join）

**验收（客观）**
- dq_align.json：
  - grid_len、ts_min/max、join_coverage（能在同一 ts 取到 ≥2 源的比例）
- 端到端 demo：同一事件窗内至少两源可 join

---

### D. Spatial（空间索引与范围查询）

**标准方案：**
- 台站点建立 R-tree（经纬度 bounding box）
- 精确距离用 haversine（球面距离，单位 km）做二次过滤（工程实践常用）

**验收（客观）**
- 单测：已知点对距离误差 < 1e-3 km
- R-tree 查询结果 == brute-force haversine 过滤结果（集合一致）
- `dq_spatial.json` 输出站点数、查询示例结果数

---

### E. Link（与地震事件关联）

**输入格式（必须定义）**
- earthquakes.csv：`id,time_utc,lat,lon,mag[,depth]`
  - time_utc：ISO8601 或毫秒时间戳（统一转换为 UTC）
  - lat/lon：度
  - mag：Mw 或等价（写入 metadata）

**关联规则**
- 时间窗：`[t0 - N_hours, t0 + M_hours]`
- 空间窗：事件点半径 `K_km`
- 输出：每事件一个目录 `outputs/linked/<event_id>/`：
  - `stations.json`（台站列表、距离、匹配信息）
  - `aligned.parquet`（事件窗对齐后的多源序列）
  - `features.parquet`（事件窗特征）
  - `anomaly.parquet`（异常分/标记）
  - `summary.json`（行数、覆盖率、缺失率、主要参数）

**验收（客观）**
- 生成目录与 4 个核心文件齐全
- aligned 覆盖时间窗（ts_min <= t0-N，ts_max >= t0+M）
- 至少包含一种有效源 + features 表非空

---

### F. Features + Simple Model（领域标准、可解释、可实现）

#### F1 特征提取（最小可交付集合）
- 通用统计：mean/std/min/max/ptp、缺失率
- 地磁（geomag）：梯度变化率（diff/Δt）、突变计数（超过阈值次数）
- AEF（aef）：Z 通道统计 + 指定频带功率（用于电磁干扰/异常）
- VLF（vlf）：谱峰频率/幅值、频带功率
- 地震（seismic）：
  - 窗口能量（RMS/绝对值积分）
  - 频谱峰值（主频）
  - P 到时（标准可实现）：classic STA/LTA + trigger_onset
  - S 到时（毕业设计可简化）：在 P 后窗口做二次触发/能量峰（若不稳定，可只输出 P 并在报告说明限制）

> 说明：若要更“标准”，可选做 instrument response removal（StationXML 含响应），但作为毕业设计可以设为可选项：若实现则输出单位（m/s），否则保持 counts 并在 metadata 标记。

**验收（客观）**
- features 表列齐全
- NaN 占比可统计且不爆炸（例如 NaN_ratio < 0.8；阈值可配，并在报告解释）
- `dq_features.json`：每源特征数量、缺失比例、时间范围

#### F2 简单关联模型（可解释）
- **阈值触发：**
  - 每站每特征基线：背景窗（同站非事件日/或事件前更长窗）分位数阈值
  - 输出：是否异常 + 分数（例如 z-score 或 0–1 归一化）
  - **基线窗口选择（必须可复现）**：
    - 默认基线：`[t0 - (N_hours + baseline_extra_hours), t0 - baseline_gap_hours]`
      - `baseline_extra_hours: 168`（默认 7 天，保证统计稳定；可配）
      - `baseline_gap_hours: 6`（默认 6 小时，避免把前兆/同震影响混入基线；可配）
    - 若基线缺失严重（如有效样本 < `baseline_min_samples`）：
      - 降级策略 1：同台站“同月同小时段”历史数据（若项目数据覆盖足够长）
      - 降级策略 2：同源同站全局分位数（必须在 `summary.json` 记录降级原因）
    - `baseline_min_samples`: 默认 500（按 1min 对齐约 8.3 小时数据；若 30s 则翻倍）
- **相似度（可选，二选一）：**
  - 余弦相似度（对齐后特征向量；适用于多特征融合）
  - DTW（对时间序列形状；成本高，慎用；若启用必须限制长度并启用窗口）
- **输出：** `anomaly_score`
  - 定义范围：默认 `0–1`
  - 计算方法（默认）：对每个特征计算 robust z-score，再经 sigmoid 映射到 0–1；多特征取 max 或加权平均（权重写入 config）
  - 必须落盘：`anomaly.parquet` 中包含 `event_id, station_id, source, feature_name, ts_ms, score, is_anomaly, baseline_method, params_hash`

**验收（客观）**
- `anomaly_score` 合法范围校验（0<=score<=1 或明确 z-score）
- 输出 top anomalies 列表（event_id, station, feature, score, time_range）
- `outputs/reports/feature_correlation.json`（新增，定义必须明确）：
  - 对每个特征：事件窗均值/方差、背景窗均值/方差、差值、Cohen’s d
  - Spearman：与“距震时间（分钟）”或“事件指示变量（0/1）”的相关
  - 背景窗选择策略、降级策略触发情况必须写入文件（可复现）
- `outputs/reports/dq_anomaly.json`（新增）：
  - 每事件：异常点数、异常台站数、按 source 分布、阈值/权重配置快照
  - 统计稳定性：基线样本数分布（min/median/p95）

---

### G. API（FastAPI，工程闭环）

> 目标：既支持程序化查询（给模型/脚本用），也支持可视化端渲染（给 Plotly 前端用）；避免把“画图逻辑”写死在前端，保证可复现与可验收。

**接口（最小集合）**
- GET `/health`
- GET `/raw/query?source&start&end&bbox|radius`
- GET `/standard/query?source&start&end&station_id&channel&limit`
- GET `/events`
- GET `/events/{id}`
- GET `/events/{id}/linked`
- GET `/events/{id}/features`
- GET `/events/{id}/anomaly`
- （新增，可视化专用）GET `/events/{id}/plots?kind=...`  → 返回 Plotly figure JSON（`plotly.io.to_json(fig)`）
- （新增，可视化专用）GET `/plots/{plot_id}` → 返回指定图的 figure JSON 或 HTML（用于离线报告展示）
- （新增，静态前端）GET `/ui` → Dashboard 首页（HTML 模板或静态文件）

**工程细节**
- 查询性能：优先读取 Parquet 分区 + predicate pushdown（按 `source/station_id/date` 过滤）
- 返回协议：
  - 数据接口：`application/json`（数据点）或 `application/x-parquet`（可选：大结果集）
  - 图接口：`application/json`（Plotly figure spec）
- 统一参数：
  - `start/end` 支持 ISO8601 或 `ts_ms`；内部统一为 UTC `ts_ms`
  - `limit` 默认 20000（防止一次拉爆前端）；超限必须返回分页/截断标记
- 数据下采样（可视化必需，避免“理论正确但无法落地”）：
  - `plot_max_points_per_trace: 5000`（默认）
  - `downsample_method: lttb|uniform`（默认 `lttb`，无依赖则降级 uniform）
  - 下采样必须打标：figure 的 `layout.meta` 写入 `downsampled=true/false` 与方法、点数

**验收（客观）**
- **pytest + httpx API 冒烟测试**：确保返回状态 200 且字段齐全
- **返回字段**：至少包含 `id/time_range/source/station/lat/lon/value` 或 `features`
- **可视化接口验收**：
  - `/events/{id}/plots` 返回 JSON 且可被 Plotly 前端直接渲染（前端用 `Plotly.react()`）
  - figure JSON 中必须包含：`layout.title`、`layout.xaxis.title`、`layout.yaxis.title`、`layout.meta.params_hash`
  - 图中时间轴必须为 UTC（x 轴显示带 `Z` 或明确标注 UTC）
- **性能基线**（可选）：指定时间窗查询 < X 秒（在报告记录机器配置）

---

## 4.5（新增）Visualization & Frontend（Plotly + FastAPI，工程可验收）

> 目的：把 pipeline 的“证据”从 JSON/Parquet 扩展到 **可交互图表**（时间序列、特征、空间分布、事件窗对比），并形成论文/答辩可直接展示的 Dashboard。  
> 原则：**图 = 可复现产物**，必须能在任意机器上从 `outputs/*` 重新生成（同 config、同数据 → 同图）。

### H. 可视化模块（Plotly）设计

#### H1 图表清单（必须实现的最小集合）
1) **多源时间序列对齐图（事件窗）**
   - 内容：同一 `event_id` 下，`geomag/aef/vlf/seismic_features` 在同一 x 轴（UTC）叠加
   - 关键点：
     - 不画 raw seismic 波形（太大且易误导），画其窗口特征（RMS、主频、STA/LTA 指标、P 到时 marker）
     - 缺失点必须显示为断线（不要插值成连续曲线）
   - 验收：
     - 图上必须有 `t0`（震时）竖线标记
     - 至少两源同图可视（join_coverage>0 的事件）
     - 图标题包含 `event_id, t0, K_km, N/M_hours`

2) **特征热力图（Feature Heatmap / Matrix）**
   - 内容：`station_id × feature_name` 的异常分数或 effect size（Cohen’s d）
   - 用途：快速定位“哪些台站/哪些特征最显著”
   - 验收：
     - heatmap 的色标范围明确（0–1 或 z-score）
     - hover 信息包含：station、feature、score、有效样本数（baseline/event）

3) **空间分布图（台站地图 + 异常强度）**
   - 内容：震中点 + 半径 `K_km` 圆（或近似）+ 台站散点（颜色/大小表示 anomaly_score）
   - Plotly 实现建议：
     - 优先 `scattergeo`（无需 map token，稳定可复现）
     - 如需底图：`scatter_mapbox(style="open-street-map")`（无需 token，但要联网；离线场景用 geo）
   - 验收：
     - 震中点、台站点可交互 hover（lat/lon、距离 km、score）
     - 与 `stations.json` 的站点数量一致（允许过滤缺失坐标，但必须写入 meta）

4) **滤波效果证据图（Notch / Bandpass 前后对比）**
   - 内容：同一段数据滤波前后功率谱对比（或目标频带功率对比）
   - 与 `filter_effect.json` 对齐：图中必须标出 notch 频带 `[freq-bw/2, freq+bw/2]`
   - 验收：
     - 图中标注 drop_db 或 drop_ratio，与报告数值一致（误差容忍 < 1e-6 级别取决于实现）

5) **数据质量仪表盘（DQ Dashboard）**
   - 内容：每阶段 `dq_*.json` 的汇总（缺失率、异常率、匹配率、join 覆盖率、文件数）
   - 验收：
     - 任何一个阶段不达标（如 matched_ratio < 阈值）要在图上突出显示（红色/警告标识由前端实现即可，但数据必须提供）

#### H2 图表生成与存储（可复现产物）
- 产物目录（新增）：
  - `outputs/plots/`：
    - `figures/<event_id>/plot_<kind>.json`（Plotly figure JSON）
    - `html/<event_id>/plot_<kind>.html`（离线可打开的 HTML，`plotly.io.write_html`）
  - `outputs/reports/dq_plots.json`（新增）
- 图表生成策略：
  - **优先生成 figure JSON**（轻量、可版本管理、可复现）
  - HTML 作为可选导出（论文/答辩展示友好）
- 图表版本与参数：
  - figure 的 `layout.meta` 必须包含：
    - `pipeline_version`
    - `params_hash`
    - `data_snapshot`（ts_min/ts_max、rows、sources）
    - `downsample_method/max_points`（若触发）

**验收（客观）**
- `dq_plots.json` 包含：
  - 每种图 `count`
  - 成功率 `render_success_rate`（必须=1.0 对 MVP 事件）
  - 每事件输出文件齐全（json/html 可选但至少 json）
  - 文件大小统计（p50/p95，防止异常膨胀）
- 自动化验证脚本：
  - 读取 `plot_<kind>.json` → `plotly.io.from_json` → 不报错
  - figure 中 trace 数量、点数符合 `plot_max_points_per_trace`

---

### I. 前端展示（Plotly + FastAPI/Flask）

> 推荐：统一用 **FastAPI**（既已有 API，又可同时提供静态页面/模板），避免 Flask/FastAPI 双栈增加工程复杂度。若你已有 Flask 代码基础，也可换 Flask，但验收标准不变。

#### I1 前端形态（两种都可，默认先做 A）
A) **轻量 Dashboard（HTML + Plotly.js + FastAPI Template）**
- FastAPI 提供 `/ui`，返回 HTML（Jinja2）
- 前端通过 fetch 调用：
  - `/events` 列表
  - `/events/{id}/plots?kind=...` 获取 figure JSON
- 优点：开发快、部署简单、毕业设计够用
- 缺点：复杂交互有限（但足够展示）

B) **前后端分离（React/Vue + FastAPI）**
- 前端项目独立构建，静态文件由 FastAPI 或 Nginx 托管
- 用途：如果你需要更强交互（筛选、联动、多页面）

#### I2 UI 页面最小闭环（必须实现）
1) **事件列表页**
   - 列：event_id、time_utc、mag、depth（若有）、lat/lon
   - 点击进入事件详情

2) **事件详情页**
   - 左侧：事件元信息 + 参数（N/M/K、baseline 策略、join coverage）
   - 右侧：四个核心图（时间序列、热力图、地图、滤波对比）
   - 下载按钮：
     - 下载 `aligned.parquet/features.parquet/anomaly.parquet`
     - 下载 `plot_<kind>.html`（若导出）

3) **数据质量页（DQ）**
   - 展示所有 `dq_*.json` 的关键指标 + 阈值是否达标

**验收（客观）**
- `/ui` 打开后可完整浏览：
  - 事件列表 → 事件详情 → 至少 2 张图可成功渲染（MVP 事件要求 4 张）
- 前端对错误处理：
  - 若某源缺失或图不可生成，页面必须显示“缺失原因”（来自 `dq_*` 或 summary 的 message 字段）
- 自动化 UI 验收（最小）：
  - 后端测试：对 `/events/{id}/plots` 返回 JSON 的 schema 校验（trace>0，layout 存在）
  - 可选：Playwright E2E（如果时间允许）

---

## 3.5（新增）存储工程化细节（Parquet 分区与读写策略）

> 目标：既能支撑大数据量（秒级/分钟级），又能让 API/可视化做到“秒开”。

### storage（增强）
- Parquet 分区建议（按数据规模选择）：
  - 标准表（长表）：`source=.../station_id=.../date=YYYY-MM-DD/part-*.parquet`
  - 特征表：`event_id=.../station_id=.../part-*.parquet`
- 必须落盘的统计（新增 `compression.json` 扩展）：
  - 原始字节数 vs Parquet 字节数（压缩比）
  - 分区文件数量、单文件大小分布（避免过多小文件）
- 读写约束：
  - 单个 Parquet 文件建议 64–256MB（可配），避免太碎导致查询慢
  - 写入必须是 append-safe（按分区追加），支持增量更新

**验收（客观）**
- `outputs/reports/compression.json`：
  - `raw_bytes, parquet_bytes, compression_ratio`
  - `file_count, size_p50, size_p95`
- 查询性能抽测（记录在 report）：
  - 读取某 `station_id + 1天` 的数据 < 1 秒（示例；以机器为准记录）

---

## 6. 证据与产物目录（必须落盘）

- `outputs/standard/`：标准化数据（Parquet/HDF5）+ metadata.json
- `outputs/linked/`：事件关联包（按 event_id 分目录）
- `outputs/features/`：特征与异常分
- `outputs/plots/`（新增）：可视化产物
  - `figures/<event_id>/plot_<kind>.json`
  - `html/<event_id>/plot_<kind>.html`（可选）
- `outputs/reports/`：客观证据报告：
  - `dq_ingest_*.json`（数据解析验证报告）
  - `dq_align.json`（时间对齐验证报告）
  - `dq_features.json`（特征提取验证报告）
  - `dq_anomaly.json`（新增：异常输出验证报告）
  - `dq_spatial.json`（空间查询验证报告）
  - `station_match.json`（台站匹配报告）
  - `filter_effect.json`（滤波效果验证报告）
  - `compression.json`（数据压缩验证报告）
  - `feature_correlation.json`（特征与地震事件的统计相关性报告）
  - `dq_plots.json`（新增：图表生成与可复现验证报告）
  - `config_snapshot.yaml`（配置参数快照）

---

## 7. 风险与应对（工程可落地）

- **StationXML 匹配不达标**：输出 `unmatched_keys_topN`，调整 loc 降级策略或时间 epoch 选择规则
  - 风险：匹配失败导致数据遗漏
  - 应对：加强对 XML epoch 匹配的验证，确保至少能匹配 `net.sta.loc.chan`
- **AEF dummy 值污染**：必须在 ingest 阶段消除（sentinel→NaN + 标记）
  - 风险：dummy 值污染导致特征计算错误
  - 应对：加强对 AEF 数据的监控和标记，避免误用无效数据
- **秒级对齐导致“造假”**：禁止插值高频波形；只允许聚合/提特征
  - 风险：误插值高频信号，导致结果不可靠
  - 应对：确保所有高频波形在对齐前先提取特征，避免对原始数据进行插值
- **滤波效果不可验证**：必须用 `filter_effect.json` 给定量证据（dB/ratio）
  - 风险：滤波效果不清晰，导致特征计算错误
  - 应对：提供对比滤波前后的频谱数据，并给出滤波效果的统计量
- **可视化性能不可用（前端卡死）**（新增）
  - 风险：直接把高频/大窗口数据喂给 Plotly 导致浏览器崩溃
  - 应对：服务端下采样 + 限制最大点数 + 必要时只返回特征序列（而非 raw）
  - 证据：`dq_plots.json` 记录每条 trace 点数与下采样触发率
- **地图底图依赖网络/Token**（新增）
  - 风险：答辩现场无网导致 mapbox 底图不可用
  - 应对：默认用 `scattergeo`（离线可用）；mapbox 仅作为可选增强
- **单位/坐标系混乱**（新增）
  - 风险：不同源单位不同（nT、counts、V/m、Hz），若不标注会误读
  - 应对：metadata + figure axis title 强制写单位；若 counts 未去响应必须明确标注
- **计算成本过高**：默认对齐 1min；30s 仅用于小样例或特定需求
  - 风险：过细的对齐粒度导致性能瓶颈
  - 应对：根据数据量和计算资源调整对齐粒度，对于大规模数据采用更粗粒度对齐

---

## 8. 最小可交付版本（MVP）

- **跑通 ingest：** IAGA2002（地磁+AEF） + MiniSEED + StationXML join
- **跑通 preprocess：** 缺失/异常/滤波 + filter_effect 证据
- **跑通 align：** 1min 网格 + join_coverage
- **跑通 link：** 输出 1 个事件关联包
- **跑通 features：** 最小特征集 + anomaly_score + dq_anomaly
- **跑通 plots：** 至少为 1 个事件输出 4 张核心图（时间序列/热力图/地图/滤波对比）+ dq_plots
- **API 提供 linked/features/plots 查询 + 冒烟测试通过**
- **UI：** `/ui` 可浏览事件并渲染图（轻量 Dashboard 版本）

---

## 9. issues.csv 任务拆解补充（新增：可视化闭环）

> 在原有 A→G 的基础上，新增 H/I 相关任务，并确保每个任务都能产出证据。

新增 Issue 类别（示例）
- `H1_plot_timeseries_event_window`
- `H2_plot_feature_heatmap`
- `H3_plot_spatial_distribution`
- `H4_plot_filter_effect`
- `H5_dq_plots_report`
- `I1_ui_event_list`
- `I2_ui_event_detail`
- `I3_ui_dq_dashboard`
- `Gx_api_plots_endpoint`
- `Gx_api_ui_endpoint`

每条新增 Issue 必须包含
- Requirements：图类型、输入输出、必要 meta 字段、下采样策略
- VerifySteps：
  - 生成 plot JSON → from_json 不报错
  - trace 点数 <= max_points
  - layout.meta 含 params_hash
- Evidence：
  - `outputs/plots/figures/...`
  - `outputs/reports/dq_plots.json`
  - pytest 输出摘要

---
