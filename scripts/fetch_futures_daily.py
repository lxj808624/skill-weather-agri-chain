"""取豆粕 M 的日频行情（主力连续，逐合约拼接）。

为什么用日线而不是分钟线：
  本研究的信号是天气（日频、周频尺度），持仓周期按周计，日线足够；
  且平台日线一次调用可取多年，比分钟线的 110 行硬上限省两个数量级的请求。

防前视：主力映射用 get_future_dominant 的当日值，当日之后才换月；
        收益一律用「前一日收盘 → 当日收盘」，不碰盘中。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp_client import McpClient  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data"
CODES = ["M"]                 # 预留：可加 "C"(玉米)、"A"(豆一) 做跨品种验证
START, END = "20180101", "20251031"


def dominant_map(client: McpClient, code: str) -> pd.DataFrame:
    sc = client.call("get_future_dominant",
                     {"underlying_symbol": code, "start_date": START, "end_date": END})
    if not sc.get("ok"):
        raise RuntimeError(f"{code} 主力映射失败：{sc.get('error')}")
    res = sc["result"]
    rows = res if isinstance(res, list) else res.get("rows", [])
    return pd.DataFrame(rows)


def daily(client: McpClient, contract: str, start: str, end: str) -> pd.DataFrame:
    """取一个合约段的日线；若被截断则对半劈开重取。

    实测：preview_rows 超过上限时接口直接返回结构化错误（KeyError: structuredContent），
    因此统一用 2000，并靠递归劈区间来绕开行数上限。
    """
    sc = client.call("get_future_daily",
                     {"symbol": contract, "start_date": start, "end_date": end},
                     preview_rows=2000)
    if not sc.get("ok"):
        raise RuntimeError(f"{contract} 取数失败：{sc.get('error')} {sc.get('detail')}")
    res = sc["result"]
    df = pd.DataFrame(res["rows"], columns=res["columns"]) if res.get("rows") else pd.DataFrame()
    if res.get("truncated") and start != end:
        mid = _mid_date(start, end)
        left = daily(client, contract, start, mid)
        right = daily(client, contract, _next_date(mid), end)
        return pd.concat([left, right], ignore_index=True)
    return df


def _mid_date(a: str, b: str) -> str:
    da, db = pd.to_datetime(a), pd.to_datetime(b)
    return (da + (db - da) / 2).strftime("%Y%m%d")


def _next_date(d: str) -> str:
    return (pd.to_datetime(d) + pd.Timedelta(days=1)).strftime("%Y%m%d")


def main() -> None:
    OUT.mkdir(exist_ok=True)
    client = McpClient()
    client.init()

    for code in CODES:
        print(f"[{code}] 拉主力映射", flush=True)
        dm = dominant_map(client, code)
        dm["date"] = dm["date"].astype(str)
        segs = (dm.groupby("symbol")["date"].agg(["min", "max"])
                  .reset_index().sort_values("min"))
        print(f"[{code}] {len(segs)} 个合约段", flush=True)

        frames = []
        for _, r in segs.iterrows():
            df = daily(client, r["symbol"], r["min"], r["max"])
            if df.empty:
                continue
            frames.append(df)
            print(f"  {r['symbol']}: {r['min']}~{r['max']} → {len(df)} 行", flush=True)
            time.sleep(1.0)

        if not frames:
            raise RuntimeError(f"{code} 未取到任何日线")

        raw = pd.concat(frames, ignore_index=True)
        raw["date"] = raw["date"].astype(str)
        # 只保留主力映射当日对应的那一条，避免同一天多个合约并存
        raw = raw.merge(dm[["date", "symbol"]].drop_duplicates(),
                        on=["date", "symbol"], how="inner")
        raw = raw.drop_duplicates(subset=["date"]).sort_values("date")

        path = OUT / f"futures_daily_{code}.parquet"
        raw.to_parquet(path, index=False)
        print(f"→ {path}  {len(raw)} 行 / {raw['date'].min()}~{raw['date'].max()}", flush=True)


if __name__ == "__main__":
    main()
