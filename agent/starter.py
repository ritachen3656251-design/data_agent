# starter.py
# 初始化数据库连接，注入到 db.engine；加载 .env 并配置 DashScope

from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.engine import URL

from tools import db


def build_engine():
    url = URL.create(
        drivername="postgresql+psycopg2",
        username="postgres",
        password="win123",
        host="127.0.0.1",
        port=5432,
        database="tianchi_ub",
    )
    return create_engine(url)


def init():
    """
    初始化：加载 .env、配置 DashScope、创建 engine 并注入到 db.engine。
    调用后可使用 tools.run_tool()、mapper（LLM）等。
    """
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if api_key:
        try:
            import dashscope
            dashscope.api_key = api_key
        except ImportError:
            pass

    engine = build_engine()
    db.engine = engine
    return engine
