"""Uvicorn 入口（`main:app`）；应用装配与生命周期见 bootstrap/app.py 与 bootstrap/lifecycle.py。"""

from bootstrap.app import app

__all__ = ["app"]
