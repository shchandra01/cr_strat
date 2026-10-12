import pytest

from cr_strat.isin import is_valid_isin


def make_isin(payload: str) -> str:
    """Append the check digit to an 11-character ISIN payload."""
    for d in "0123456789":
        if is_valid_isin(payload + d):
            return payload + d
    raise ValueError(payload)


@pytest.fixture
def isin():
    return make_isin
