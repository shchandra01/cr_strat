import pytest

from cr_strat.isin import is_valid_isin


@pytest.mark.parametrize("value", ["US0378331005", "AU0000XVGZA3", "GB0002634946", "INE002A01018", " ine002a01018 "])
def test_valid(value):
    assert is_valid_isin(value)


@pytest.mark.parametrize("value", ["INE002A01019", "INE002A0101", "TREPS", "", None, 12345])
def test_invalid(value):
    assert not is_valid_isin(value)
