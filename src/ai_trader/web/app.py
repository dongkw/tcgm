"""FastAPI application factory and CLI entrypoint."""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .routes import router
from .settings import DashboardSettings, build_settings


def package_root() -> Path:
    return Path(__file__).resolve().parent


def create_app(settings: DashboardSettings | None = None) -> FastAPI:
    resolved_settings = settings or build_settings()
    app = FastAPI(title="天才交易员本地工作台")
    app.state.settings = resolved_settings
    # FastAPI 0.139 on the current Python runtime wraps APIRouter as a nested
    # route. Directly appending routes keeps url_for names and page routing
    # predictable for this local app.
    app.router.routes.extend(router.routes)
    app.mount(
        "/static",
        StaticFiles(directory=str(package_root() / "static")),
        name="static",
    )
    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local AI trader web dashboard.")
    parser.add_argument("--output-dir", default="data")
    parser.add_argument("--db-path")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = build_settings(args.output_dir, args.db_path, args.host, args.port)
    app = create_app(settings)
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
