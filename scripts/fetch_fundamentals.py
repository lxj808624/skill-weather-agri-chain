"""取豆粕的已知供需数据：库存与基差。

这两个是「市场早就知道的信息」，是 B3 基线的核心。
如果天气模型的增量打不过它们，说明天气信息已被定价，研究应判为无增量。

接口参数差异是实测踩出来的：
- get_future_inventory 认 symbol='M'（品种级），合约代码返回空
- get_future_basis     认 underlying_symbol='M'，合约代码直接报错
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp_client import McpClient  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data"
CODE, START, END = "M", "20180101", "20251031"


def _rows(r: dict) -> pd.DataFrame:
    if not r.get("ok"):
        raise RuntimeError(f"{r.get('method')} 失败：{r.get('error')}")
    res = r["result"]
    if isinstance(res, list):
        return pd.DataFrame(res)
    return pd.DataFrame(res.get("rows", []), columns=res.get("columns"))


def main() -> None:
    c = McpClient()
    c.init()

    inv = _rows(c.call("get_future_inventory",
                       {"symbol": CODE, "start_date": START, "end_date": END},
                       preview_rows=2000))
    inv["date"] = pd.to_datetime(inv["date"])
    inv = inv.sort_values("date").drop_duplicates("date")
    inv.to_parquet(OUT / f"inventory_{CODE}.parquet", index=False)
    print(f"库存 → {len(inv)} 行 / {inv['date'].min().date()}~{inv['date'].max().date()}")

    bas = _rows(c.call("get_future_basis",
                       {"underlying_symbol": CODE, "start_date": START, "end_date": END},
                       preview_rows=2000))
    bas["date"] = pd.to_datetime(bas["date"])
    bas = bas.sort_values("date").drop_duplicates("date")
    bas.to_parquet(OUT / f"basis_{CODE}.parquet", index=False)
    print(f"基差 → {len(bas)} 行 / {bas['date'].min().date()}~{bas['date'].max().date()}")
    print(f"   列：{list(bas.columns)}")


if __name__ == "__main__":
    main()
