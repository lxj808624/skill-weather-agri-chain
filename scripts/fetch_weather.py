"""抓取美豆主产区气象数据。

两个口径，刻意分开存，因为它们的「信息时点」性质不同：

1. archive  —— Open-Meteo Archive（ERA5 再分析）。**事后修订**，不是当时可知的信息。
   只能用于描述真实天气，做信号时必须额外加滞后（本 skill 默认 3 天）。
2. forecast —— Open-Meteo Historical Forecast API 的归档预报。**当时发布的快照**，
   可作为「市场在 t 日能看到什么」的无偏代理。这是满足题目「对齐信息发布时间」的关键。
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONF = ROOT / "config" / "regions.yaml"
OUT = ROOT / "data"


def _get(url: str, retries: int = 4) -> dict:
    last = None
    for i in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"取数失败 {url} :: {last}")


def fetch_point(lat: float, lon: float, start: str, end: str, source: str, decl: str) -> pd.DataFrame:
    base = {
        "archive": "https://archive-api.open-meteo.com/v1/archive",
        "forecast": "https://historical-forecast-api.open-meteo.com/v1/forecast",
    }[source]
    d = decl.split(",")
    q = (
        f"{base}?latitude={lat}&longitude={lon}&start_date={start}&end_date={end}"
        f"&daily={decl}&timezone=UTC"
    )
    js = _get(q)
    df = pd.DataFrame(js["daily"])
    df["date"] = pd.to_datetime(df["time"])
    df = df.drop(columns=["time"])
    keep = [c for c in d if c in df.columns]
    df = df[["date"] + keep]
    for c in keep:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def main() -> None:
    cfg = yaml.safe_load(CONF.read_text())
    decl = ",".join(cfg["variables"])
    end = cfg["end_date"]
    sources = sys.argv[1:] or ["archive", "forecast"]
    OUT.mkdir(exist_ok=True)

    for source in sources:
        start = cfg["start_date"] if source == "archive" else cfg["forecast_start_date"]
        frames = []
        for reg in cfg["regions"]:
            for i, (lat, lon) in enumerate(reg["points"]):
                print(f"[{source}] {reg['code']}-p{i} ({lat},{lon})", flush=True)
                df = fetch_point(lat, lon, start, end, source, decl)
                df["region"] = reg["code"]
                df["point"] = i
                frames.append(df)
                time.sleep(0.4)
        raw = pd.concat(frames, ignore_index=True)
        path = OUT / f"weather_{source}.parquet"
        raw.to_parquet(path, index=False)
        print(f"→ {path}  {len(raw)} 行", flush=True)


if __name__ == "__main__":
    sys.exit(main())
