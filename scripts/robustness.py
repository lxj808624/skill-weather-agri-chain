"""稳健性检查：多重检验校正、滚动样本外、事件年、双口径对照。

为什么必须做这四项：
- 扫了 15 个 (K, lag) 组合，挑最好的那个必然过拟合 → 必须做 FDR 校正
- 单一切分（2024 训练 / 2025 测试）可能只是运气 → 必须滚动
- 天气研究常被「一次拉尼娜就有效」骗过 → 必须逐年看
- 归档预报与再分析结论若一致，说明不是数据口径造成的假阴性 → 双口径对照
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "outputs"
IS_END = "2022-12-31"


def _p_from_t(t: float, n: int) -> float:
    """双侧 p 值，用正态近似；n>100 时与 t 分布差异可忽略。"""
    if not np.isfinite(t):
        return np.nan
    z = abs(t)
    return 2 * (1 - 0.5 * (1 + math.erf(z / math.sqrt(2))))


def bh_fdr(pvals: list[float], alpha: float = 0.05) -> list[bool]:
    """Benjamini-Hochberg。返回每个假设是否被拒绝。"""
    p = np.array(pvals, dtype=float)
    order = np.argsort(p)
    m = len(p)
    thresh = alpha * (np.arange(1, m + 1) / m)
    passed = p[order] <= thresh
    if passed.any():
        k = np.max(np.where(passed)[0])
        reject_sorted = np.zeros(m, dtype=bool)
        reject_sorted[: k + 1] = True
    else:
        reject_sorted = np.zeros(m, dtype=bool)
    out = np.zeros(m, dtype=bool)
    out[order] = reject_sorted
    return out.tolist()


def fdr_table() -> pd.DataFrame:
    d = pd.read_csv(OUT / "lag_scan_h1.csv")
    d["p"] = [_p_from_t(t, int(n)) for t, n in zip(d["t"], d["n"])]
    d["fdr_pass"] = bh_fdr(d["p"].tolist())
    d = d.sort_values("p")
    d[["K", "lag", "n", "ic", "t", "p", "fdr_pass"]].to_csv(OUT / "fdr_lag_scan.csv", index=False)
    return d


def build_matrix(horizon: int = 1) -> pd.DataFrame:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from evaluate import _load, _z, _features  # 复用同一套构造，避免口径分叉

    m = _load(horizon)
    wx = [f"precip{k}_fc" for k in (7, 14, 30)] + [f"temp{k}_fc" for k in (7, 14, 30)]
    wx = [c for c in wx if c in m.columns]
    m = _z(m, wx + ["mom20", "mom60", "vol20", "ret1", "inv", "basis"], m["date"] <= IS_END)
    return m, wx


def walk_forward(m: pd.DataFrame, wx: list[str]) -> pd.DataFrame:
    """扩张窗口滚动：每年用当年之前的数据拟合，预测当年。"""
    from evaluate import _features, _ols

    rows = []
    for test_year in range(2019, 2026):
        fit = m[(m["date"].dt.year < test_year)]
        test = m[m["date"].dt.year == test_year]
        for level, name in [(3, "B3_供需"), (4, "W_天气")]:
            cols = _features(m, level, wx)
            base = m[cols].notna().all(axis=1) & m["ret_next"].notna()
            f = fit[(fit[cols].notna().all(axis=1)) & fit["ret_next"].notna()]
            t = test[(test[cols].notna().all(axis=1)) & test["ret_next"].notna()]
            if len(f) < 100 or len(t) < 40:
                continue
            beta, _, _ = _ols(f["ret_next"].values, f[cols].values)
            y = t["ret_next"].values
            pred = np.column_stack([np.ones(len(t)), t[cols].values]) @ beta
            sse = float(((y - pred) ** 2).sum())
            sst = float(((y - y.mean()) ** 2).sum())
            rows.append({"test_year": test_year, "model": name, "n_test": len(t),
                         "r2_pred": round(1 - sse / sst, 4) if sst else np.nan,
                         "hit": round(float((np.sign(pred) == np.sign(y)).mean()), 3)})
    out = pd.DataFrame(rows)
    out.to_csv(OUT / "walk_forward.csv", index=False)
    return out


def yearly_ic(m: pd.DataFrame, wx: list[str]) -> pd.DataFrame:
    rows = []
    for y in sorted(m["date"].dt.year.unique()):
        d = m[m["date"].dt.year == y]
        for c in wx:
            ok = d[c].notna() & d["ret_next"].notna()
            if ok.sum() < 50:
                continue
            rows.append({"year": int(y), "var": c, "n": int(ok.sum()),
                         "ic": round(float(np.corrcoef(d.loc[ok, c], d.loc[ok, "ret_next"])[0, 1]), 4)})
    out = pd.DataFrame(rows)
    out.to_csv(OUT / "yearly_ic.csv", index=False)
    return out


def main() -> None:
    d = fdr_table()
    print("=== FDR 校正（15 个 K×lag 组合，样本内）===")
    print(d.head(6)[["K", "lag", "ic", "t", "p", "fdr_pass"]].to_string(index=False))
    print("通过 FDR 的组合数：", int(d["fdr_pass"].sum()))

    m, wx = build_matrix(1)
    wf = walk_forward(m, wx)
    print("\n=== 滚动样本外（扩张窗口）===")
    print(wf.to_string(index=False))

    yi = yearly_ic(m, wx)
    piv = yi.pivot_table(index="var", columns="year", values="ic")
    print("\n=== 逐年 IC（看是否只有单一年份有效）===")
    print(piv.round(3).to_string())


if __name__ == "__main__":
    main()
