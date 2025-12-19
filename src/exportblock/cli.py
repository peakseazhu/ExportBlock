from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn

from exportblock.config import load_config
from exportblock.pipeline.run import build_pipeline, link_pipeline


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="exportblock")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, help_text in [
        ("build", "全量 ingest + 预处理（Raw → Standard）"),
        ("link", "按配置事件执行关联、特征、可视化"),
    ]:
        p = sub.add_parser(name, help=help_text)
        p.add_argument(
            "--config",
            default="configs/demo.yaml",
            help="配置文件路径（YAML），默认: configs/demo.yaml",
        )

    run = sub.add_parser("run", help="兼容入口：先 build 再 link")
    run.add_argument(
        "--config",
        default="configs/demo.yaml",
        help="配置文件路径（YAML），默认: configs/demo.yaml",
    )

    api = sub.add_parser("api", help="启动 FastAPI 服务（依赖 outputs/ 已生成）")
    api.add_argument(
        "--config",
        default="configs/demo.yaml",
        help="配置文件路径（YAML），默认: configs/demo.yaml",
    )
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=8000)
    api.add_argument("--reload", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    config_path = Path(args.config).resolve()
    config = load_config(config_path)

    if args.command == "build":
        build_pipeline(config, config_path=config_path)
        return 0

    if args.command == "link":
        link_pipeline(config, config_path=config_path)
        return 0

    if args.command == "run":
        build_pipeline(config, config_path=config_path)
        link_pipeline(config, config_path=config_path)
        return 0

    if args.command == "api":
        os.environ["EXPORTBLOCK_CONFIG"] = str(config_path)
        uvicorn.run(
            "exportblock.api.app:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
        )
        return 0

    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
