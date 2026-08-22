"""CN/HK search facade.

Implementation is query-time suggest (Eastmoney / Tencent / Sina) in
:mod:`src.data_client.cn.search`. This module keeps the import path the
search route already uses.
"""

from src.data_client.cn.search import has_cjk, search

__all__ = ["has_cjk", "search"]
