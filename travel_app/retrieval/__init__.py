"""资料获取公开入口；契约见 docs/modules/retrieval.md。"""
from .service import Capabilities, Query, QueryResult, Retrieval, ToolResult
from .connections import connected_retrieval
from .amap_http import AmapHTTPClient as MapHTTPClient
from .source_http import SourceHTTPClient

__all__ = ["Capabilities", "Query", "QueryResult", "Retrieval", "ToolResult", "connected_retrieval", "MapHTTPClient", "SourceHTTPClient"]
