# ExportBlock-2：地震系统多源数据处理与关联建模

本项目实现一个可运行的工程流水线：对地磁（IAGA2002）、地震波（MiniSEED + StationXML）、大气电场（AEF，IAGA 风格）、VLF（CDF）进行解析、预处理、时间对齐、空间查询、事件关联、特征/异常、可视化，并提供 FastAPI 查询接口与自动化测试。

## 快速开始（Demo）

1) 安装依赖：

```powershell
python -m pip install -r requirements.txt
python -m pip install -e .
```

2) 运行端到端 Demo（会生成 `outputs/` 下的报告/产物）：

```powershell
python -m exportblock.cli run --config configs/demo.yaml
```

3) 运行 API（默认 `http://127.0.0.1:8000`）：

```powershell
python -m exportblock.cli api --config configs/demo.yaml
```

4) 运行测试：

```powershell
python -m pytest
```

## 目录结构（核心）

- `src/exportblock/`：核心代码（解析、pipeline、API）
- `configs/`：配置（数据路径、事件参数）
- `docs/task_map.md`：任务清单 → 代码/证据路径映射
- `outputs/`：运行产物（已在 `.gitignore` 中默认忽略）

## 注意

- 当前仓库包含大体量原始数据文件（`.sec/.min/.mseed/.cdf/...`），不适合直接推送到 GitHub（单文件 100MB 限制）。默认通过 `.gitignore` 排除；如需上 GitHub，建议使用 Git LFS 或外置数据盘/下载脚本。
