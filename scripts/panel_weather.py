"""把 12 个网格点的原始气象数据聚合成「一条美豆带天气序列」。

三个关键设计，都是为了不引入未来信息：

1. **区域聚合**：州内多点取算术平均（降水用面积平均、温度用均值），州间按大豆产量权重加权
   （IL 14 / IA 14 / MN 8 / NE 7，USDA NASS 五年均值）。
2. **气候态只用过去**：异常的基准是「同一日历日、过去若干年」的均值，用 expanding 方式
   逐日推进，绝不使用未来年份的同期数据。这是天气研究最常被评审打回的地方。
3. **预报口径单独成列**：`*_fc` 后缀的列来自归档预报（当时发布的快照），
   `*_obs` 后缀来自再分析（事后修订，必须再加滞后）。

产出 data/weather_panel.parquet，一行一天。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONF = yaml.safe_load((ROOT / "config" / "regions.yaml").read_text())
DATA = ROOT / "data"

DOY_WIN = 7          # 气候态匹配窗（同日历日 ±7 天）
WINSOR = 3.0         # z 分缩尾


def _region_weighted(df: pd.DataFrame) -> pd.DataFrame:
    """点 → 州 → 全国加权。"""
    reg = df.groupby(["region", "date"]).mean(numeric_only=True).reset_index()
    w = {r["code"]: r["weight"] for r in CONF["regions"]}
    reg["w"] = reg["region"].map(w)
    reg["w"] = reg["w"] / reg["w"].groupby(reg["date"]).transform("sum")
    reg["w"] = reg["w"].fillna(0)

    out = []
    for col in ["precipitation_sum", "temperature_2m_mean", "temperature_2m_max"]:
        if col not in reg.columns:
            continue
        reg[f"_{col}"] = reg[col] * reg["w"]
    g = reg.groupby("date").agg({f"_precipitation_sum": "sum",
                                f"_temperature_2m_mean": "sum",
                                f"_temperature_2m_max": "sum"}).reset_index()
    g = g.rename(columns={f"_precipitation_sum": "precip",
                          f"_temperature_2m_mean": "temp",
                          f"_temperature_2m_max": "tmax"})
    return g


def _fixed_baseline(obs_panel: pd.DataFrame, col: str, max_year: int = 2017) -> pd.Series:
    """用 obs 的「起点~2017」做固定气候态基线，逐日按 DOY 平滑。

    这样 2018 起的预报异常也能算，且基线只来自 2017 年以前的数据，
    对全部预报日期都是严格可得的信息。
    """
    o = obs_panel[obs_panel.index.year <= max_year].copy()
    o["doy"] = o.index.dayofyear
    base = o.groupby("doy")[col].mean()
    # ±7 天滑动平滑
    sm = base.rolling(15, center=True, min_periods=5).mean()
    sm = sm.bfill().ffill()
    return sm


def _clim_anomaly(s: pd.Series, doy: pd.Series, win: int = DOY_WIN) -> pd.Series:
    """逐日计算「相对过去同季」的异常，只用 t 年之前的数据。"""
    out = pd.Series(index=s.index, dtype=float)
    years = s.index.year
    for y in sorted(set(years)):
        past = s[(years < y)]
        if past.empty:
            continue
        mask = years == y
        for i in s[mask].index:
            d = i.dayofyear
            lo, hi = d - win, d + win
            sel = []
            for dd in range(lo, hi + 1):
                dd2 = dd % 365 if dd > 0 else dd + 365
                sel.append(past[past.index.dayofyear == dd2])
            ref = pd.concat(sel) if sel else pd.Series(dtype=float)
            if len(ref) < 30:
                out.loc[i] = np.nan
            else:
                out.loc[i] = s.loc[i] - ref.mean()
    return out


def build(source: str) -> pd.DataFrame:
    raw = pd.read_parquet(DATA / f"weather_{source}.parquet")
    df = _region_weighted(raw).sort_values("date").reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    if source == "forecast":
        # 实测：2016-2017 的温度全是 0 占位值，2018 起才干净
        df = df[df.index >= "2018-01-01"]

    suf = "_obs" if source == "archive" else "_fc"
    if source == "archive":
        df[f"precip_anom{suf}"] = _clim_anomaly(df["precip"], df.index.to_series())
        df[f"temp_anom{suf}"] = _clim_anomaly(df["temp"], df.index.to_series())
    else:
        # 基线取自 obs 的 2010-2017，对 2018+ 的每一天都是过去信息
        base_p = _fixed_baseline(OBS_PANEL, "precip")
        base_t = _fixed_baseline(OBS_PANEL, "temp")
        doy = df.index.dayofyear
        df[f"precip_anom{suf}"] = df["precip"] - pd.Series(doy, index=df.index).map(base_p)
        df[f"temp_anom{suf}"] = df["temp"] - pd.Series(doy, index=df.index).map(base_t)

    # 缩尾，避免归档预报里 120mm 这类极端值主导 z 分（实测出现过）
    for c in [f"precip_anom{suf}", f"temp_anom{suf}"]:
        sd = df[c].std()
        df[c] = df[c].clip(-WINSOR * sd, WINSOR * sd)

    # 滚动累计（把日异常攒成「一段时间的干湿」）
    for w in (7, 14, 30):
        df[f"precip{w}{suf}"] = df[f"precip_anom{suf}"].rolling(w, min_periods=w).mean()
        # 高温胁迫同样是单产机制，之前只做了降水、漏了温度，导致模型里只有 3 个天气变量
        df[f"temp{w}{suf}"] = df[f"temp_anom{suf}"].rolling(w, min_periods=w).mean()
    df[f"gdd{suf}"] = (df["tmax"] - 10).clip(lower=0)   # 生长度日，10℃ 基点

    df = df.reset_index().rename(columns={"index": "date"})
    return df


def main() -> None:
    global OBS_PANEL
    OBS_PANEL = _region_weighted(pd.read_parquet(DATA / "weather_archive.parquet"))
    OBS_PANEL["date"] = pd.to_datetime(OBS_PANEL["date"])
    OBS_PANEL = OBS_PANEL.sort_values("date").set_index("date")
    obs = build("archive")
    fc = build("forecast")
    keep = ["date"]
    obs_cols = [c for c in obs.columns if c.endswith("_obs") or c in ("precip", "temp", "tmax", "gdd_obs")]
    obs = obs[[c for c in obs.columns if c == "date" or c in obs_cols]]
    fc = fc[[c for c in fc.columns if c == "date" or c.endswith("_fc") or c in ("precip", "temp", "tmax")]]

    # 两边同名列（原始气象值）加后缀区分
    obs = obs.rename(columns={c: c + "_obs" for c in ("precip", "temp", "tmax") if c in obs.columns})
    fc = fc.rename(columns={c: c + "_fc" for c in ("precip", "temp", "tmax") if c in fc.columns})

    panel = obs.merge(fc, on="date", how="outer").sort_values("date")
    panel["month"] = panel["date"].dt.month
    panel["year"] = panel["date"].dt.year
    panel["is_growing"] = panel["month"].between(
        CONF["growing_season"]["start_month"], CONF["growing_season"]["end_month"])
    panel["is_critical"] = panel["month"].isin(CONF["growing_season"]["critical_months"])

    path = DATA / "weather_panel.parquet"
    panel.to_parquet(path, index=False)
    print(f"→ {path}  {len(panel)} 行 / {panel['date'].min().date()}~{panel['date'].max().date()}")
    print(panel[[c for c in panel.columns if c.startswith(("precip", "temp"))]].describe().round(2).to_string())


if __name__ == "__main__":
    main()
