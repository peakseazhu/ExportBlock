# 地震系统多源数据处理与关联建模 — 统一技术方案（重写版 plan.md）

> 本方案严格按照题目要求：先对**全部原始数据**完成预处理（去噪/异常剔除/缺失补齐），再进行格式标准化与压缩存储；之后开展时空对齐与地震标记关联；最后做特征提取与关联模型，并提供 FastAPI 数据接口。fileciteturn2file0L1-L52  
> **重要约束（按你的要求）**：各数据源读取“目录下所有文件”，读取阶段**不按时间筛选**。时间窗口（N/M）只用于第二阶段“事件关联抽取”。

---

## 1. 项目目标与交付物

### 1.1 目标
1) 多源数据预处理：噪声抑制、异常值剔除、缺失值补全  
2) 格式标准化：统一时间戳精度（毫秒）、统一空间坐标系（WGS84）、统一字段与单位  
3) 数据压缩存储：对高频数据采用无损压缩，保存“预处理后的标准化数据”  
4) 时空对齐与关联匹配：对齐时间轴、按空间范围检索、按地震事件规则生成关联数据集  
5) 特征提取与关联模型：产出可查询的特征表与异常/关联信号  
6) 数据接口：FastAPI 支持查询原始数据、预处理标准化数据、关联数据与特征值

### 1.2 主要交付目录（建议）
```
outputs/
  bronze/                 # 原始数据“结构化副本”（可选但强烈推荐，用于审计/回放）
  silver_standard/        # 预处理后的标准化数据（题目核心交付）
  gold_aligned/           # 多源时间对齐后的数据（按需要）
  gold_linked/            # 与地震事件关联后的数据集（按 event_id）
  features/               # 特征表（按 source/event）
  models/                 # 简单关联模型结果/阈值配置
  dq_reports/             # 数据质量报告（每次 pipeline 运行生成）
  manifests/              # 文件清单、处理版本、哈希等
```

---

## 2. 数据源与输入格式

### 2.1 三类原始数据
- **地磁（Geomag）**：文本日志（IAGA2002 类 `.min/.sec` 等），多通道（X/Y/Z/F 等）
- **地震波（Seismic）**：二进制波形（MiniSEED `.mseed`）+ 台站响应（StationXML）
- **大气电磁（VLF/AEF）**：VLF（CDF 等），AEF（IAGA2002 `.min`，如 Z 通道）

### 2.2 必备辅助数据
- **台站信息**：station_id、经纬度（WGS84）、海拔、通道信息  
- **地震标记（Catalog）**：event_id、origin_time、lat、lon、depth、mag 等

---

## 3. 总体架构：三大阶段流水线

> 阶段顺序不可颠倒：  
> **全量读取 → 清洗预处理 → 标准化与压缩存储（阶段1） → 时空对齐&关联匹配（阶段2） → 特征&模型（阶段3）**fileciteturn2file0L9-L52

### 阶段1：全量读取 + 清洗预处理 + 标准化 + 压缩存储
- **读取**：扫描每个输入目录下所有文件（不按时间筛选）
- **清洗预处理（核心）**：
  - 去噪：漂移/环境干扰/工频干扰，滤波（卡尔曼、小波去噪、带通/陷波等）
  - 异常值剔除：MAD/鲁棒 Z、物理边界、突刺检测
  - 缺失值补全：短缺口插值（时间序列插值/样条），长缺口保留缺失并标记
- **标准化**：统一字段、单位、时间戳毫秒、空间坐标 WGS84
- **压缩存储**：无损压缩写入 `silver_standard/`

### 阶段2：时空对齐与关联匹配
- 时间对齐：统一时间轴；对非等时采样序列插值/重采样；确保同一时刻可关联分析fileciteturn2file0L33-L39
- 空间匹配：建立空间索引（四叉树/GeoHash），支持范围查询（如震中 100km 内）fileciteturn2file0L39-L42
- 与地震标记关联：
  - 时间窗：`[t0 - N小时, t0 + M小时]`（N/M 可配置，如 72/24）fileciteturn2file0L45-L48
  - 空间窗：震中 `K km` 半径内（K 可配置，如 50–200km）fileciteturn2file0L48-L50
  - 生成事件级关联数据集（多源组合）fileciteturn2file0L50-L52

### 阶段3：特征提取与关联模型构建
- 基础特征：均值/峰值/方差等统计特征；以及领域信号特征（频谱峰值、梯度变化率、P/S 到时等）fileciteturn2file0L54-L60
- 关联规律：相似度匹配、阈值触发等，标记可能与地震相关的异常信号fileciteturn2file0L60-L63
- 产出：
  - `features/`：特征表
  - `models/`：关联规则与结果（含阈值、触发点、置信度）

---

## 4. 阶段1详细设计：全量读取、预处理、标准化、压缩

### 4.1 全量读取策略（不按时间筛选）
**原则**：读取阶段“只负责完整读取与解析”，不做事件窗口裁剪。  
为避免内存爆炸，采用“按文件流式处理 + 增量落盘”：
1) 扫描目录得到 `manifest`（文件路径、大小、mtime、hash）
2) 文件逐个/批次解析为 DataFrame（或 xarray/obspy Stream）
3) 进入预处理与标准化流程
4) 按分区写入存储（避免把全量数据一次性攒在内存）

### 4.2 预处理总流程（对每个 source 统一执行）
对每条序列（按 station_id + channel 分组）执行：

1) **基础清洗**
   - 解析异常行/坏块标记；将 sentinel/dummy 转 NaN
   - 去重、排序、时间戳纠正（如重复采样点）

2) **去噪（Denoise）**
   - Geomag/AEF：漂移/低频趋势 → detrend + 平滑（Kalman/小波）
   - VLF：分钟级指标 → 滚动中值/鲁棒平滑；如保留谱信息可做陷波
   - Seismic：波形 → demean/detrend/taper + bandpass（必要时陷波），可选 remove_response

3) **异常值剔除（Outlier）**
   - 鲁棒统计：MAD 或 IQR（按台站/通道）
   - 突刺：一阶差分超过阈值的点/段
   - 结果：
     - 标记在 `quality_flags.is_outlier=true`
     - 可配置：将异常值置 NaN（推荐），交给补缺

4) **缺失值补全（Impute）**
   - 仅对“短缺口”插值（可配置上限，如 ≤ 5min/≤ 30s）
   - 长缺口：保留 NaN，并记录 `quality_flags.gap_ms`
   - 插值方法：线性/时间插值/样条（按源配置）

5) **预处理审计**
   - 记录每条序列的：
     - missing_rate、outlier_rate、插值点数、滤波参数
   - 写入 `dq_reports/`

### 4.3 标准化数据模型（统一 Schema）
所有源写入统一结构（“长表”推荐），并保留源特有字段：

**通用字段**
- `ts`：UTC 时间戳（毫秒精度）
- `source`：`geomag | aef | seismic | vlf`
- `station_id`：台站/观测点 ID
- `channel`：通道（如 X/Y/Z/F 或 BHZ 等）
- `value`：主数值（或 `value1,value2,...`）
- `units`：单位（nT、m/s、V/m、dB 等）
- `lat, lon, elev_m`：WGS84 坐标与海拔
- `quality_flags`：JSON（缺失/异常/插值/饱和/坏段等）
- `proc_version`：处理版本号
- `proc_params`：关键参数摘要（便于复现）

**源特有建议**
- Seismic：可额外写 `sampling_rate_hz`、`network`、`location_code`
- VLF：如有频点/带宽，可写 `freq_hz`、`band_id`

> 备注：为支持 API 查询“原始数据”，建议同时在 `bronze/` 保存“结构化但未去噪/未补缺”的版本。

### 4.4 压缩存储与分区
- 推荐格式：**Parquet（列式）**  
- 压缩：`zstd`（无损且高压缩比）  
- 分区建议：
  - `source=.../station_id=.../date=YYYY-MM-DD/part-*.parquet`
- 对高频波形（若要存原始波形）：可用 HDF5/Zarr + Blosc（无损压缩），或保留 MiniSEED（其内部也有压缩编码）

---

## 5. 阶段2详细设计：时空对齐与关联匹配

### 5.1 时间对齐策略
**核心原则**：为关联分析提供统一时间轴，但避免把超高频数据强行插值到分钟级造成信息丢失。

- 统一时间轴：
  - 设定目标网格 `Δt`（如 1s 或 1min，按任务/可视化需求）
  - 对非等时采样数据：在 `Δt` 上重采样（均值/中位数/最大值等）
  - 对高频波形：优先提取短窗特征后再对齐（RMS、能量、谱峰等）

- 对齐输出：
  - `gold_aligned/`：按 `ts` 统一后的多源表（可选）
  - 或直接在事件关联时按窗口对齐（更省存储）

### 5.2 空间匹配与索引
- 坐标统一：WGS84（lat/lon）
- 空间索引：
  - GeoHash（工程简单）或四叉树（概念清晰）
- 范围查询：
  - 输入：震中点（lat0, lon0）+ 半径 K km
  - 输出：满足距离阈值的 station 列表
- 距离计算：Haversine（球面距离）或投影坐标（视实现选择）

### 5.3 地震事件关联规则（生成 gold_linked）
对每个地震事件：
1) 计算时间窗：`[t0 - N小时, t0 + M小时]`
2) 计算空间窗：`distance(station, epicenter) <= K km`
3) 从 `silver_standard/` 查询符合 station + time 的数据（多源）
4) 输出事件级数据集：
   - `gold_linked/event_id=<id>/geomag.parquet`
   - `gold_linked/event_id=<id>/seismic_features.parquet`（若不输出原波形）
   - `gold_linked/event_id=<id>/vlf.parquet`
   - `gold_linked/event_id=<id>/meta.json`（窗口、站点、K/N/M 等）

---

## 6. 阶段3详细设计：特征提取与关联模型

### 6.1 特征提取（features/）
- 统计特征（各源通用）：mean/max/min/std/var/skew/kurtosis、分位数等
- Geomag：
  - 梯度变化率、短窗方差、突变点计数、频带能量比等
- Seismic：
  - 分钟/秒窗：RMS、能量、谱峰频率、谱熵
  - 可选：简化 P/S 到时（基于 STA/LTA 或能量突变），作为“工程可用”的近似
- VLF/AEF：
  - 频谱峰值、峰值漂移、能量变化率、日周期偏离等

### 6.2 简单关联模型（models/）
- 规则类（可解释、易展示）：
  - 阈值触发：例如 geomag 波动率超过某阈值持续 T 分钟
  - 相似度匹配：与历史“震前模板”在窗口内做 DTW/相关系数
- 输出：
  - `anomalies.parquet`：`event_id, ts, source, station_id, score, rule_id`
  - `rulebook.yaml`：阈值、窗口长度、触发条件

---

## 7. FastAPI 数据接口（支持可视化查询）

### 7.1 查询范围（题目要求）fileciteturn2file0L65-L71
- 原始数据（按时间/空间筛选） → `bronze/`
- 预处理后的标准化数据 → `silver_standard/`
- 与地震标记关联的数据集及特征值 → `gold_linked/` + `features/`

### 7.2 典型接口设计
- `GET /stations`：站点列表与坐标
- `GET /raw`：`source, station_id, start, end, bbox/radius`（分页）
- `GET /standard`：同上，返回预处理标准化数据
- `GET /events`：地震事件列表（支持时间/震级筛选）
- `GET /events/{event_id}/linked`：返回该事件关联数据概览（源覆盖率、站点数）
- `GET /events/{event_id}/features`：返回特征表
- `GET /events/{event_id}/anomalies`：返回异常/关联模型结果

### 7.3 工程性要求
- 统一输出 JSON（列表分页）或 Parquet 下载（大数据）
- 必须支持：
  - 时间过滤、空间过滤、站点过滤、source 过滤
  - `limit/offset` 或 cursor 分页
  - 返回 `dq_summary`（可选：缺失率/异常率）

---

## 8. 配置（config.yaml 关键字段示例）

```yaml
inputs:
  geomag_dir: data/geomag/
  aef_dir: data/aef/
  seismic_dir: data/seismic/
  vlf_dir: data/vlf/
  station_meta_path: data/stations.csv
  stationxml_path: data/station.xml
  eq_catalog_path: data/eq_catalog.csv

processing:
  timezone: UTC
  standard_time_precision: ms

  denoise:
    geomag:
      method: kalman
      params: {Q: 1e-3, R: 1e-2}
    aef:
      method: wavelet
      params: {wavelet: db4, level: 3}
    seismic:
      method: bandpass
      params: {freqmin: 0.5, freqmax: 20.0}
    vlf:
      method: rolling_median
      params: {window: 5}

  outlier:
    method: mad
    z_thresh: 6.0
    action: set_nan   # or "clip" / "keep"

  impute:
    max_gap_seconds: 300
    method: time_interpolate

storage:
  format: parquet
  compression: zstd
  base_dir: outputs/
  partition_cols: [source, station_id, date]

linking:
  N_hours: 72
  M_hours: 24
  K_km: 100
  align_grid: 1min
```

---

## 9. 数据质量（DQ）与可复现性

每次 pipeline 运行必须生成：
- `manifests/run_<timestamp>.json`：输入文件清单、hash、版本
- `dq_reports/dq_<timestamp>.json`：每源/每台站缺失率、异常率、插值比例、滤波参数
- `proc_version`：写入每条记录，保证复现

---

## 10. 实现路线（代码结构建议）

```
src/
  ingest/
    scan.py                 # 扫描目录、生成 manifest（不做时间筛选）
    parse_geomag.py
    parse_aef.py
    parse_vlf.py
    parse_seismic.py
  preprocess/
    denoise.py
    outliers.py
    impute.py
    standardize.py          # schema/units/time/geo
  storage/
    writer.py               # 分区写入 parquet/hdf5
    reader.py               # 为 API 提供统一读取
  align_link/
    time_align.py
    spatial_index.py
    link_events.py
  features/
    extract.py
    seismic_picking.py       # 可选
  models/
    rules.py
    scoring.py
  api/
    app.py
    routers/
```

---

## 11. 重要说明（工程现实与本方案的关系）

- 本方案按要求“全量读取不筛时间”，但实现应采用**流式/增量落盘**，否则大数据量会导致内存与运行时间不可控。  
- 第二阶段事件关联再做 N/M 时间窗抽取，是合理的“计算侧优化”，不违反“读取阶段不筛选”的约束。

---
