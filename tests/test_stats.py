import numpy as np
import pandas as pd
import pytest

from cr_strat.stats import (block_bootstrap, deflated_sharpe, expected_max_sharpe, newey_west_tstat, ols,
                            probabilistic_sharpe, sharpe_moments)


@pytest.fixture
def data():
    rng = np.random.default_rng(0)
    n = 400
    df = pd.DataFrame({"x1": rng.normal(size=n), "x2": rng.normal(size=n) > 0, "g": rng.integers(0, 40, n)})
    df["y"] = 1.0 + 2.0 * df["x1"] - 0.5 * df["x2"] + rng.normal(size=n) * (1 + df["x1"].abs())
    return df


def test_ols_coefficients_and_hc1(data):
    res = ols(data, "y", ["x1", "x2"]).set_index("term")
    X = np.column_stack([np.ones(len(data)), data["x1"], data["x2"].astype(float)])
    beta = np.linalg.lstsq(X, data["y"].to_numpy(), rcond=None)[0]
    assert res["coef"].to_numpy() == pytest.approx(beta)
    e = data["y"].to_numpy() - X @ beta
    bread = np.linalg.inv(X.T @ X)
    hc1 = bread @ (X.T * e**2) @ X @ bread * len(X) / (len(X) - 3)
    assert res["se"].to_numpy() == pytest.approx(np.sqrt(np.diag(hc1)))
    assert res.attrs["n"] == 400 and 0 < res.attrs["r2"] < 1
    clustered = ols(data, "y", ["x1", "x2"], cluster="g")
    assert clustered.attrs["n_clusters"] == 40 and clustered["coef"].to_numpy() == pytest.approx(beta)


def test_ols_drops_missing_and_rejects_underdetermined(data):
    d = data.copy()
    d.loc[:9, "x1"] = np.nan
    assert ols(d, "y", ["x1"]).attrs["n"] == 390
    with pytest.raises(ValueError):
        ols(d.head(2), "y", ["x1", "x2"])


def test_newey_west_without_lags_is_the_plain_t_stat():
    x = np.random.default_rng(1).normal(0.1, 1.0, 500)
    mean, t, lags = newey_west_tstat(x, lags=0)
    assert lags == 0 and t == pytest.approx(x.mean() / (x.std(ddof=0) / np.sqrt(500)))
    # Positive autocorrelation widens the errors.
    ar = np.convolve(np.random.default_rng(2).normal(0.05, 1, 600), np.ones(10) / 10, mode="valid")
    assert abs(newey_west_tstat(ar, lags=10)[1]) < abs(newey_west_tstat(ar, lags=0)[1])


def test_sharpe_inference():
    assert probabilistic_sharpe(0.0, 250, 0.0, 3.0) == pytest.approx(0.5)
    assert probabilistic_sharpe(0.1, 1000, 0.0, 3.0) > probabilistic_sharpe(0.1, 100, 0.0, 3.0)
    assert expected_max_sharpe(1, 0.01) == 0.0 and expected_max_sharpe(100, 0.01) > expected_max_sharpe(10, 0.01)
    r = np.random.default_rng(3).normal(0.0005, 0.01, 1000)
    sr, n, skew, kurt = sharpe_moments(r)
    trials = [sr, sr * 0.5, 0.0, -0.02, 0.03]
    assert deflated_sharpe(r, trials) < probabilistic_sharpe(sr, n, skew, kurt)
    est, lo, hi = block_bootstrap(r, np.mean, block=10, n_boot=200)
    assert lo < est < hi
