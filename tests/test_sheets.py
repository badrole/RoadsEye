# -*- coding: utf-8 -*-
import pytest
from unittest.mock import MagicMock, patch
from server.gsheets_exporter import _format_peak_hour_range, _col_letter, is_configured


def test_format_peak_hour_range():
    assert _format_peak_hour_range("2026-10-18T17:00:00") == "17:00-18:00"
    assert _format_peak_hour_range("2026-10-18 09:00:00") == "09:00-10:00"
    assert _format_peak_hour_range("23") == "23:00-00:00"
    assert _format_peak_hour_range(None) == "—"


def test_col_letter():
    assert _col_letter(1) == "A"
    assert _col_letter(2) == "B"
    assert _col_letter(26) == "Z"
    assert _col_letter(27) == "AA"


def test_is_configured():
    with patch("server.gsheets_exporter.get_spreadsheet_id", return_value="test_id"):
        with patch("server.gsheets_exporter.get_credentials_path") as mock_path:
            mock_path.return_value.exists.return_value = True
            assert is_configured() is True
