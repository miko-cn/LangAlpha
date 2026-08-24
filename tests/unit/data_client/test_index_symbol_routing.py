"""Index symbol caret handling and region routing."""

from src.data_client.fmp.data_source import FMPDataSource
from src.data_client.market_data_provider import symbol_market


class TestCaretLogic:
    def test_us_index_gets_caret(self):
        assert FMPDataSource._api_symbol("GSPC", is_index=True) == "^GSPC"

    def test_already_caret_unchanged(self):
        assert FMPDataSource._api_symbol("^HSI", is_index=True) == "^HSI"

    def test_suffixed_cn_index_not_careted(self):
        # ^000300.SS is not a valid ticker anywhere; suffixed symbols pass through
        assert FMPDataSource._api_symbol("000300.SS", is_index=True) == "000300.SS"

    def test_non_index_unchanged(self):
        assert FMPDataSource._api_symbol("AAPL", is_index=False) == "AAPL"


class TestIndexRegionRouting:
    def test_known_foreign_indices_skip_us(self):
        assert symbol_market("^HSI") == "hk"
        assert symbol_market("^N225") == "jp"
        assert symbol_market("^FTSE") == "uk"
        assert symbol_market("^GDAXI") == "eu"

    def test_case_insensitive_caret_indices(self):
        assert symbol_market("^hsi") == "hk"
        assert symbol_market("^n225") == "jp"

    def test_us_indices_stay_us(self):
        assert symbol_market("^GSPC") == "us"
        assert symbol_market("^VIX") == "us"

    def test_plain_us_equity_unchanged(self):
        assert symbol_market("AAPL") == "us"

    def test_cn_suffix_unchanged(self):
        assert symbol_market("000300.SS") == "cn"


class TestCnIndexHeuristic:
    def test_sse_csi_indexes(self):
        from src.data_client.cn.symbols import is_cn_index

        assert is_cn_index("000001.SS") is True
        assert is_cn_index("000300.SS") is True
        assert is_cn_index("000688.SS") is True

    def test_szse_indexes(self):
        from src.data_client.cn.symbols import is_cn_index

        assert is_cn_index("399001.SZ") is True
        assert is_cn_index("399006.SZ") is True

    def test_same_digits_stock_is_not_index(self):
        from src.data_client.cn.symbols import is_cn_index

        assert is_cn_index("000001.SZ") is False  # 平安银行
        assert is_cn_index("600519.SS") is False
        assert is_cn_index("300750.SZ") is False

    def test_iso_date_normalizes_compact(self):
        from src.data_client.cn.bars import to_iso_date

        assert to_iso_date("20000101") == "2000-01-01"
        assert to_iso_date("2026-08-23") == "2026-08-23"
        assert to_iso_date(None) is None
        assert to_iso_date("") is None
