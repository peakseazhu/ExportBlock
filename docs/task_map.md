# 任务清单与交付映射（project_task_list.csv）

本文件用于“逐条确认开发内容”：将 `project_task_list.csv` 的每一条任务，映射到代码入口、验证方式与证据产物路径。

| Issue ID | Category | 交付内容（实现点） | 验证（自动化） | 证据产物（生成路径） |
|---:|---|---|---|---|
| 1 | Ingestion | IAGA2002 解析（sec/min）+ 缺失/哑元处理 + 元数据抽取 | `pytest -k iaga` | `outputs/reports/dq_ingest_iaga.json` |
| 2 | Ingestion | MiniSEED 解析 + StationXML 匹配统计 + 波形摘要 | `pytest -k mseed` | `outputs/reports/dq_ingest_mseed.json` |
| 3 | Preprocessing | 地磁 Kalman 滤波 + 前后对比指标 | `pytest -k kalman` | `outputs/reports/filter_effect.json` |
| 4 | Feature Extraction | 地震波分钟级能量/频谱特征 | `pytest -k seismic_features` | `outputs/reports/dq_features.json` |
| 5 | Alignment | 统一 1min 时间栅格对齐 + 完整率统计 | `pytest -k align` | `outputs/reports/dq_align.json` |
| 6 | Spatial Query | 空间索引（KDTree/ECEF）+ 半径查询 | `pytest -k spatial` | `outputs/reports/dq_spatial.json` |
| 7 | Event Matching | 时间窗 + 空间窗事件关联，输出事件包 | `pytest -k linkage` | `outputs/linked/0001/stations.json` |
| 8 | Anomaly Detection | z-score 阈值异常分数/标记 | `pytest -k anomaly` | `outputs/reports/dq_anomaly.json` |
| 9 | API | FastAPI：raw/linked/features/plots 查询 + 冒烟测试 | `pytest -k api` | `outputs/api_tests/logs.json` |
| 10 | Visualization | Plotly：事件时间序列 + 热力图 | `pytest -k plots` | `outputs/plots/figures/0001/plot_timeseries.json` |

