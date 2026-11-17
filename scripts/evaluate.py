"""天气→豆粕：滞后扫描、三级基线、样本外对照。

设计原则（对应 docs/研究设计.md 第 3~5 节）：
- 信号只用到 t 日及之前的气象数据；建仓在 t+1 日开盘，收益 = t+1 开 → t+2 开
- 所有标准化参数只在 IS 上估计，OOS 直接套用（不前视）
- 三级基线逐级加码：季节性 → 价格 → 已知供需
- 多重检验：K∈{7,14,30} × 滞后 5 档，输出全部组合，不挑最好的报

产出：
  outputs/lag_scan.csv        每个 (K, lag) 组合的 IC / t / 分年度表现
  outputs/model_compare.csv   天气模型 vs B1/B2/B3 的 IS/OOS 对照
  outputs/season_split.csv    生长季 vs 非生长季的失效区间检验
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "outputs"

IS_END = "2022-12-31"
LAGS = [0, 5, 10, 20, 40]
KS = [7, 14, 30]


def _load(horizon: int = 1) -> pd.DataFrame:
    w = pd.read_parquet(DATA / "weather_panel.parquet")
    w["date"] = pd.to_datetime(w["date"])

    f = pd.read_parquet(DATA / "futures_daily_M.parquet")
    f["date"] = pd.to_datetime(f["date"])
    f = f.sort_values("date").reset_index(drop=True)
    for c in ("open", "close", "settlement", "volume", "open_interest"):
        if c in f.columns:
            f[c] = pd.to_numeric(f[c], errors="coerce")

    # 收益一律「下一交易日开盘 → 再下一交易日开盘」，避免把隔夜传导算进收益
    # horizon=1 即 t+1 开盘建仓、t+2 开盘平仓；h>1 用于检验「天气影响单产」这类慢变量
    f["ret_next"] = f["open"].shift(-(1 + horizon)) / f["open"].shift(-1) - 1

    # 已知供需：库存（周度 → 前向填充）与基差率（日度）
    inv = pd.read_parquet(DATA / "inventory_M.parquet")
    inv["date"] = pd.to_datetime(inv["date"])
    inv = inv.sort_values("date").rename(columns={"inventory_qty": "inv"})[["date", "inv"]]
    bas = pd.read_parquet(DATA / "basis_M.parquet")
    bas["date"] = pd.to_datetime(bas["date"])
    bas = bas.sort_values("date")[["date", "basis_ratio"]].rename(columns={"basis_ratio": "basis"})

    m = f.merge(w, on="date", how="left")
    m = pd.merge_asof(m.sort_values("date"), inv, on="date", direction="backward")
    m = m.merge(bas, on="date", how="left")

    # 价格类基线特征
    m["ret1"] = m["close"] / m["close"].shift(1) - 1
    m["mom20"] = m["close"] / m["close"].shift(20) - 1
    m["mom60"] = m["close"] / m["close"].shift(60) - 1
    m["vol20"] = m["ret1"].rolling(20).std()
    m["month"] = m["date"].dt.month
    # 季节性要做哑变量：连续月份编码会把 12 月与 1 月当成相距 11 个月，是错的
    for k in range(2, 13):
        m[f"m{k}"] = (m["month"] == k).astype(float)
    return m


def _z(df: pd.DataFrame, cols: list[str], is_mask: pd.Series) -> pd.DataFrame:
    """只用 IS 段估计均值方差。"""
    d = df.copy()
    for c in cols:
        mu = d.loc[is_mask, c].mean()
        sd = d.loc[is_mask, c].std()
        d[c + "_z"] = (d[c] - mu) / sd if sd and not np.isnan(sd) else 0.0
    return d


def _ols(y: np.ndarray, X: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """带截距的最小二乘，返回系数、t 值、R²（不用 statsmodels，保持依赖极轻）。"""
    X = np.column_stack([np.ones(len(X)), X])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    dof = max(len(y) - X.shape[1], 1)
    sigma2 = (resid @ resid) / dof
    try:
        cov = sigma2 * np.linalg.inv(X.T @ X)
    except np.linalg.LinAlgError:
        return beta, np.full(len(beta), np.nan), np.nan
    se = np.sqrt(np.abs(np.diag(cov)))
    t = beta / np.where(se == 0, np.nan, se)
    ss_tot = ((y - y.mean()) ** 2).sum()
    r2 = 1 - (resid @ resid) / ss_tot if ss_tot else np.nan
    return beta, t, r2


def lag_scan(m: pd.DataFrame, suffix: str) -> pd.DataFrame:
    """扫 (K, lag)。**只用样本内**，否则等于用样本外挑参数再报样本外结果。"""
    rows = []
    is_mask = m["date"] <= IS_END
    for K in KS:
        for lag in LAGS:
            s_ = m[f"precip{K}_{suffix}"].shift(lag)
            y = m["ret_next"]
            ok = s_.notna() & y.notna() & is_mask
            if ok.sum() < 100:
                continue
            ic = float(np.corrcoef(s_[ok], y[ok])[0, 1])
            beta, t, r2 = _ols(y[ok].values, s_[ok].values.reshape(-1, 1))
            rows.append({"K": K, "lag": lag, "n": int(ok.sum()),
                         "ic": round(ic, 4), "t": round(float(t[1]), 2),
                         "r2": round(float(r2), 4), "seg": "IS"})
    return pd.DataFrame(rows)


def _features(m: pd.DataFrame, level: str, weather_cols: list[str]) -> list[str]:
    month_cols = [f"m{k}" for k in range(2, 13)]
    base = ["mom20_z", "mom60_z", "vol20_z", "ret1_z"] + month_cols if level >= 2 else month_cols
    if level >= 3:
        base += ["inv_z", "basis_z"]
    if level >= 4:
        base += weather_cols
    return [c for c in base if c in m.columns]


def model_compare(m: pd.DataFrame, weather_cols: list[str]) -> pd.DataFrame:
    """**IS 拟合、OOS 预测**。OOS 段内重新拟合再算 R² 不是样本外，已修正。"""
    rows = []
    is_mask = m["date"] <= IS_END
    for level, name in [(1, "B1_季节"), (2, "B2_价格"), (3, "B3_供需"), (4, "W_天气")]:
        cols = _features(m, level, weather_cols)
        if not cols:
            continue
        base = m[cols].notna().all(axis=1) & m["ret_next"].notna()
        fit = m[base & is_mask]
        test = m[base & ~is_mask]
        if len(fit) < 60 or len(test) < 60:
            rows.append({"model": name, "n_is": len(fit), "n_oos": len(test)})
            continue
        beta, t_is, r2_is = _ols(fit["ret_next"].values, fit[cols].values)

        def _score(d: pd.DataFrame) -> tuple[float, float]:
            y = d["ret_next"].values
            X1 = np.column_stack([np.ones(len(d)), d[cols].values])
            pred = X1 @ beta
            sse = float(((y - pred) ** 2).sum())
            sst = float(((y - y.mean()) ** 2).sum())
            r2 = 1 - sse / sst if sst else np.nan
            hit = float((np.sign(pred) == np.sign(y)).mean())
            return r2, hit

        r2_oos, hit_oos = _score(test)
        rows.append({"model": name, "n_is": len(fit), "n_oos": len(test),
                     "r2_is": round(float(r2_is), 4),
                     "r2_oos": round(float(r2_oos), 4),
                     "hit_is": round(_score(fit)[1], 3),
                     "hit_oos": round(hit_oos, 3),
                     "w_t_is": round(float(t_is[-len(weather_cols)]), 2) if level == 4 else np.nan,
                     "cols": len(cols)})
    return pd.DataFrame(rows)


def season_split(m: pd.DataFrame, weather_cols: list[str]) -> pd.DataFrame:
    rows = []
    for label, mask in [("生长季(5-9)", m["is_growing"]), ("鼓粒期(7-8)", m["is_critical"]),
                        ("非生长季", ~m["is_growing"].fillna(False))]:
        sub = m[mask & m["ret_next"].notna()]
        if sub.empty:
            continue
        for c in weather_cols:
            ok = sub[c].notna() & sub["ret_next"].notna()
            if ok.sum() < 30:
                continue
            ic = np.corrcoef(sub.loc[ok, c], sub.loc[ok, "ret_next"])[0, 1]
            rows.append({"seg": label, "var": c, "n": int(ok.sum()),
                         "ic": round(float(ic), 4)})
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="fc", choices=["fc", "obs"], help="fc=归档预报, obs=再分析")
    ap.add_argument("--lag", type=int, default=None, help="固定滞后；不给则扫描最优")
    ap.add_argument("--horizon", type=int, default=1, help="持仓天数（1=次日，5=一周，20=一月）")
    a = ap.parse_args()

    OUT.mkdir(exist_ok=True)
    m = _load(a.horizon)
    is_mask = m["date"] <= IS_END

    weather_cols = [f"precip{k}_{a.source}" for k in KS] + [f"temp{k}_{a.source}" for k in KS]
    weather_cols = [c for c in weather_cols if c in m.columns]
    m = _z(m, weather_cols + ["mom20", "mom60", "vol20", "ret1", "inv", "basis"], is_mask)

    scan = lag_scan(m, a.source)
    tag = f"h{a.horizon}"
    scan.to_csv(OUT / f"lag_scan_{tag}.csv", index=False)

    chosen = weather_cols
    if a.lag is None and not scan.empty:
        best = scan.loc[scan["ic"].abs().idxmax()]
        print(f"最强组合：K={best['K']} lag={best['lag']} IC={best['ic']} t={best['t']}")

    mc = model_compare(m, chosen)
    mc.to_csv(OUT / f"model_compare_{tag}.csv", index=False)

    ss = season_split(m, chosen)
    ss.to_csv(OUT / f"season_split_{tag}.csv", index=False)

    meta = {"source": a.source, "n_rows": int(len(m)),
            "range": [str(m["date"].min().date()), str(m["date"].max().date())],
            "is_end": IS_END, "weather_cols": chosen}
    (OUT / "run_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))

    print("\n=== 滞后扫描（|IC| 前 5）===")
    print(scan.reindex(scan["ic"].abs().sort_values(ascending=False).index).head(5).to_string(index=False))
    print("\n=== 模型对照 ===")
    print(mc.to_string(index=False))


if __name__ == "__main__":
    main()
