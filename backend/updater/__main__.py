"""服务更新器：容器内执行检查更新、构建与重启 backend。"""

from updater.config import UPDATER_SETTINGS
from updater.server import serve

if __name__ == "__main__":
    serve(UPDATER_SETTINGS)
