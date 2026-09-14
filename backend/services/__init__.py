"""服务编排层：contracts / domains / application / infrastructure / adapters。

不在此包级 re-export 子包符号——调用方按能力域导入，例如 ``from services.domains.memory import create_memory``。
工具与供应商注册由 bootstrap 显式触发。
"""
