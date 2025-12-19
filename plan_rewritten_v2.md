# PLAN.md — 地震系统多源数据处理与关联建模（按题目内容重写：Raw 可查 + 三大阶段）

> 核心要求（题目明确）：基于 FastAPI 开发接口，支持可视化模块查询：  
> 1) **原始数据（按时间/空间筛选）**；2) **预处理后的标准化数据**；3) **与地震标记关联的数据集及特征值**。  
> 因此必须把“全量读取的数据”落成**可查询的结构化形态**（Raw/Bronze 层），再完成三大阶段：数据清洗 → 时空对齐与关联匹配 → 特征与关联模型。

---

## 0. 总体设计与交付物

### 0.1 三层数据资产（必须有）
- **Raw（Bronze，结构化原始）**：为接口“原始数据按时空筛选”服务  
  - 仅做：解析、统一字段、统一 UTC 时间、补全 station 坐标、标记 sentinel/坏值（不去噪、不补缺、不剔除异常）
- **Standard（Silver，预处理后的标准化）**：题目“预处理后的标准化数据”核心交付  
  - 做：去噪、异常剔除、缺失补全、单位/字段标准化
- **Linked/Features（Gold）**：按地震事件时空关联后的数据集、特征、模型结果

### 0.2 目录规范（可验收、可复现）
```
outputs/
  manifests/                 # 输入文件清单、hash、版本
  raw_bronze/                # 原始数据结构化（可查询）
  standard_silver/           # 预处理后的标准化数据（可查询、可复现）
  linked_gold/               # 按 event_id 关联输出（对齐序列 + 元数据）
  features/                  # 特征表（按 event_id / station / source 分区）
  models/                    # 阈值/规则 + 结果
  reports/                   # 每阶段 DQ 报告（客观证据）
  plots/                     # 可视化产物（Plotly JSON/HTML，可选）
```

### 0.3 端到端入口（建议）
- 全量建库（Raw → Standard）：`python -m exportblock.cli build --config configs/demo.yaml`
- 事件关联（Standard → Linked/Features/Models）：`python -m exportblock.cli link --config configs/demo.yaml`
- API 服务：`python -m exportblock.cli api --config configs/demo.yaml`
- 测试：`pytest -q`

---

## 1. 数据源与输入（全量读取，不做时间筛选）

> 约束：**读取阶段不按时间筛选文件**。每个数据源扫描目录下**全部文件**并解析。  
> 为避免内存爆炸：采用“按文件流式处理 + 增量落盘 + 分区写入”。

### 1.1 数据源
1) **Geomag 地磁**：IAGA2002（sec/min 等文本）  
2) **AEF 大气电场**：IAGA2002 风格（min/hour 等文本，可能含 dummy 通道）  
3) **Seismic 地震波**：MiniSEED（.mseed）+ StationXML（台站元信息/响应）  
4) **VLF 电磁**：CDF（分钟级或更细）

### 1.2 必备辅助数据
- 台站表：`station_id, lat, lon, elev_m, network/sta/loc/chan（seismic）`
- 地震目录：`event_id, origin_time_utc, lat, lon, depth_km, mag`

---

## 2. 统一 Schema（Raw 与 Standard 共用，便于 API 与可视化）

### 2.1 最小字段（统一长表）
- `ts_ms` (int64)：UTC 毫秒时间戳  
- `source` (str)：`geomag | aef | seismic | vlf`
- `station_id` (str)：观测点/台站 ID（seismic 建议 `NET.STA.LOC.CHAN` 或拆列）
- `channel` (str)：X/Y/Z/F、BHZ、或 vlf_band 等
- `value` (float)：主数值（必要时扩展 `value2/value3`）
- `units` (str)：nT、V/m、counts、Hz、dB 等（必须明确）
- `lat, lon, elev_m` (float)：WGS84
- `quality_flags` (json)：见 2.2
- `proc_stage` (str)：`raw_bronze | standard_silver | linked_gold | features`
- `proc_version` (str) + `params_hash` (str)：可复现

### 2.2 quality_flags 规范（必须统一）
- `is_missing`、`missing_reason`（sentinel/gap/parse_error/unknown）
- `is_outlier`、`outlier_method`、`threshold`
- `is_interpolated`、`interp_method`、`max_gap`
- `is_filtered`、`filter_type`、`filter_params`
- `station_match`（exact/downgrade/unmatched，主要用于 seismic↔StationXML）
- `note`（可选）

---

## 3. 阶段 A：全量 Ingest → Raw（结构化原始，可查询）

### 3.1 目标
把所有原始文件解析成统一长表，并按分区写入 `outputs/raw_bronze/`，以支持：
- `/raw/query`：按时间/空间/站点/通道筛选并返回原始曲线（或下采样）

### 3.2 读取与落盘（可执行）
**Step A0：生成 manifest（必须）**
- 扫描目录下所有文件：路径、大小、mtime、sha256、推断 source
- 输出：`outputs/manifests/run_<timestamp>.json`

**Step A1：逐文件解析（不裁窗）**
- geomag/aef：解析 header + 数据行，识别 sentinel（如 88888/99999），置 NaN 并打标
- vlf：解析 CDF；如果原数据粒度较细，可在 Raw 层保留原粒度（不强制 1min）
- seismic：
  - Raw 层建议至少落“结构化摘要”（trace 清单：start/end/sr/npts）以便查询覆盖范围
  - 若需要 raw 波形可视化：可选把波形按块写入 HDF5/Zarr（无损压缩），或保留 mseed 文件并提供“回放解析”的 raw 查询

**Step A2：分区写入（必须）**
- 建议 Parquet + zstd
- 分区：`source=.../station_id=.../date=YYYY-MM-DD/part-*.parquet`
- 文件目标大小：64–256MB（避免小文件风暴）

### 3.3 客观验收（必须输出证据）
输出 `outputs/reports/dq_raw_bronze.json`：
- `file_count, rows, station_count, channel_count`
- `ts_min, ts_max`
- `missing_rate`（sentinel/gap/parse_error 分开统计）
- seismic：`trace_count, matched_ratio（如做 StationXML join）`

---

## 4. 阶段 B：数据清洗（去噪/异常/补缺）→ Standard（预处理后标准化）

> 这是题目“三大步骤之一：数据清洗预处理”的核心。  
> 输入必须来自 Raw（结构化原始），输出写入 `outputs/standard_silver/`。

### 4.1 清洗策略（按源可配置）
**B1 去噪（Denoise / Filter）**
- geomag：漂移/低频趋势 → detrend（Kalman / rolling median / 小波去噪）
- aef：可用小波去噪或鲁棒平滑；dummy 通道（如 X/Y/F）默认丢弃或全标缺失
- vlf：若为分钟级指标，可用 rolling median 抑制突刺；如保留频谱可做 50/60Hz notch
- seismic：波形在提特征前必须做基础预处理：demean/detrend/taper + bandpass（可选 remove_response）

**B2 异常值剔除（Outlier）**
- 每条序列（station_id+channel）用 MAD/IQR 或鲁棒 z-score
- 行为可配：
  - `set_nan`（推荐）→ 交给补缺
  - `clip`（需记录阈值）
  - `keep`（仅打标）

**B3 缺失补全（Impute）**
- 仅补“短缺口”（可配置 max_gap 秒/点数）
- 方法：time interpolation / linear / spline
- 长缺口：保留 NaN，但写 `gap_ms` 到 quality_flags

### 4.2 标准化（Standardize）
在完成 B1–B3 后再做统一：
- 时间：UTC `ts_ms`（毫秒）
- 坐标：WGS84
- 单位：写入 `units`（counts 未去响应必须明确标注）
- 字段：统一为 2.1 schema；写入 `proc_stage=standard_silver`

### 4.3 压缩存储（无损）
- Parquet + zstd（推荐）
- 同样分区：`source/station_id/date`
- 输出：`outputs/standard_silver/...`

### 4.4 客观验收（必须输出证据）
1) `outputs/reports/dq_standard_silver.json`
   - rows、ts_min/max、missing_rate、outlier_rate、imputed_rate（按源/站点/通道）
2) `outputs/reports/filter_effect.json`（至少对一个源给出“滤波有效”的定量证据）
   - 频带功率下降（dB 或 ratio）
3) `outputs/reports/compression.json`
   - raw_bronze 字节 vs standard_silver 字节、压缩比、分区文件大小分布

---

## 5. 阶段 C：时空对齐与关联匹配（与地震标记关联）

> 题目“三大步骤之二：时空对齐与关联匹配”。

### 5.1 时间对齐（Time Align）
- 目标：统一到可配置时间栅格 `align_interval`（默认 1min，可选 30s）
- 规则（避免造假信号）：
  - **高频原波形不直接插值到分钟网格**：seismic 先提特征再对齐
  - geomag sec：对齐到网格做聚合（mean/std/min/max/ptp/梯度等）
  - vlf/aef/geomag min：重采样到网格；若需要填充只允许 ffill 且必须打标

输出（可选两种）：
- 全局 aligned：`outputs/linked_gold/aligned_global/...`（数据大时不推荐）
- 事件内对齐：在事件关联时按窗口生成 aligned（推荐）

验收：`outputs/reports/dq_align.json`（grid_len、join_coverage、每源覆盖率）

### 5.2 空间匹配（Spatial Match）
- 台站点建立空间索引（GeoHash/四叉树/R-tree）
- 范围查询：给定震中点 + 半径 K km
- 距离计算：Haversine（km）

验收：`outputs/reports/dq_spatial.json`（索引命中与 brute-force 一致性测试）

### 5.3 与地震标记关联（Event Link）
对每个事件（catalog）：
- 时间窗：`[t0 - N_hours, t0 + M_hours]`
- 空间窗：震中半径 `K_km`
- 数据来源：从 `standard_silver` 里按 station+time 查询（必要时做事件内对齐）
- 产出：`outputs/linked_gold/event_id=<id>/`
  - `stations.json`（台站列表、距离、匹配信息）
  - `aligned.parquet`（事件窗多源对齐序列）
  - `summary.json`（覆盖率、缺失率、参数快照）

验收：`outputs/reports/dq_linked.json`（每事件文件齐全、时间窗覆盖、join_coverage）

---

## 6. 阶段 D：特征提取与关联模型构建

> 题目“三大步骤之三：特征提取与关联模型构建”。

### 6.1 特征提取（Features）
从 `linked_gold/event_id=<id>/aligned.parquet` 或事件内原序列提取：
- 通用统计：mean/std/min/max/ptp、缺失率
- geomag：梯度变化率、短窗方差、突变计数
- seismic（基于波形或窗口特征）：RMS/能量、谱峰频率、STA/LTA 触发（可选 P 到时）
- vlf/aef：频带功率、谱峰、漂移率等

输出：
- `outputs/features/event_id=<id>/features.parquet`

验收：`outputs/reports/dq_features.json`（列齐全、NaN 比例、每源特征数）

### 6.2 简单关联模型（可解释）
- 阈值触发：基于基线窗的分位数/鲁棒 z-score
- 相似度（可选）：余弦/相关系数/DTW（需限制长度）
- 输出：
  - `outputs/models/event_id=<id>/anomalies.parquet`
  - `outputs/models/rulebook.yaml`（阈值/窗口/权重）

验收：`outputs/reports/dq_models.json`（score 范围、异常点数、基线样本数分布）

---

## 7. 数据接口（FastAPI/Flask，满足题目三类查询）

### 7.1 必须支持的三类查询
1) **原始数据 raw（按时间/空间筛选）**  
   - 数据来源：`outputs/raw_bronze/`
2) **预处理后的标准化数据 standard**  
   - 数据来源：`outputs/standard_silver/`
3) **关联数据集与特征值 linked/features**  
   - 数据来源：`outputs/linked_gold/` + `outputs/features/` + `outputs/models/`

### 7.2 推荐接口（最小集合）
- `GET /health`
- `GET /raw/query?source&start&end&bbox|min_lat,max_lat,min_lon,max_lon&radius_km&station_id&channel&limit`
- `GET /standard/query?...`
- `GET /events`
- `GET /events/{event_id}`
- `GET /events/{event_id}/linked`
- `GET /events/{event_id}/features`
- `GET /events/{event_id}/anomalies`

**可视化性能（必须）**
- 返回点数限制：`limit` 默认 20000
- 支持下采样：`downsample=lttb|uniform` + `max_points`
- 返回中必须标记：`meta.downsampled=true/false`

### 7.3 API 验收（必须自动化）
- `pytest` + `httpx`：
  - `/raw/query`、`/standard/query` 返回 200 且字段包含 `ts_ms/source/station_id/channel/value/lat/lon/quality_flags`
  - `/events/{id}/features` 返回 200 且至少 1 条特征
- 结果可复现：返回包含 `params_hash` 或 `proc_version`

---

## 8. 配置（configs/*.yaml，必须可复现）

```yaml
inputs:
  geomag_dir: data/geomag/
  aef_dir: data/aef/
  seismic_dir: data/seismic/
  vlf_dir: data/vlf/
  station_meta_path: data/stations.csv
  stationxml_path: data/stations_inventory.xml
  eq_catalog_path: data/earthquakes.csv

storage:
  format: parquet
  compression: zstd
  partition_cols: [source, station_id, date]
  target_file_mb: 128

preprocess:
  outlier:
    method: mad
    z_thresh: 6.0
    action: set_nan
  impute:
    method: time_interpolate
    max_gap_seconds: 300
  denoise:
    geomag: {method: kalman, params: {Q: 1e-3, R: 1e-2}}
    aef:    {method: wavelet, params: {wavelet: db4, level: 3}}
    vlf:    {method: rolling_median, params: {window: 5}}
    seismic:{method: bandpass, params: {freqmin: 0.5, freqmax: 20.0}}

link:
  N_hours: 72
  M_hours: 24
  K_km: 100
  align_interval: 1min

api:
  default_limit: 20000
  plot_max_points: 5000
  downsample_method: lttb
```

---

## 9. MVP（最小可交付版本，能答辩演示）

1) 跑通 Raw/Bronze：全量解析落盘 + `dq_raw_bronze.json`
2) 跑通 Standard/Silver：去噪/异常/补缺 + `dq_standard_silver.json` + `filter_effect.json`
3) 跑通事件关联：输出 1 个 `linked_gold/event_id=.../`（aligned + summary）
4) 跑通特征与模型：features + anomalies + `dq_features.json`/`dq_models.json`
5) 跑通 API：raw/standard/linked/features 四类接口 + 冒烟测试

---

## 10. “能验证的东西”清单（每次运行必须产出）

- `outputs/manifests/run_<ts>.json`（文件清单 + hash）
- `outputs/reports/dq_raw_bronze.json`
- `outputs/reports/dq_standard_silver.json`
- `outputs/reports/filter_effect.json`
- `outputs/reports/compression.json`
- `outputs/reports/dq_align.json`
- `outputs/reports/dq_spatial.json`
- `outputs/reports/dq_linked.json`
- `outputs/reports/dq_features.json`
- `outputs/reports/dq_models.json`
- `outputs/reports/config_snapshot.yaml`（配置快照）
