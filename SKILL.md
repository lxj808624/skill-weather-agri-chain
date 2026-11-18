---
name: weather-agri-chain
description: 用美豆带（IL/IA/NE/MN）降水与温度异常预测 DCE 豆粕，严格对齐气象信息的发布时间与传导滞后，并与季节性、价格动量、已知供需（库存/基差）三级基线对照，检验样本外增量与失效区间。使用 PandaData 日线与库存/基差、Open-Meteo 再分析与归档预报。
project_type: skill
category: futures-agri
validation_level: real-data-partial
platform: QuantStudio (PandaData MCP + 外部气象 API)
---

# weather-agri-chain

## 这个 skill 回答什么问题

天气能不能给农产品期货带来**超出季节性和已知供需**的增量信息？

不是问「天气和价格有没有相关性」——那个谁都能画出图。而是问：
控制掉季节性、价格动量、库存与基差之后，天气变量还剩多少解释力，且在样本外是否还成立。

## 结论先行（已跑完，负结果）

**在次日与一周窗口未发现可利用的样本外增量；一月窗口样本功效不足，不下结论。**

| 口径 | 样本内 R² | 样本外 R² | 天气系数 t（IS） |
| --- | --- | --- | --- |
| 次日持仓 | 0.0168 | **-0.0228** | 1.85 |
| 一周持仓 | 0.0711 | **-0.0844** | 0.73 |
| 一月持仓 | 0.1747 | **-0.0312** | 0.10 |

（OOS 列为 IS 拟合系数套用到样本外的预测结果；基线 B3 的次日 OOS R² 为 -0.0112，天气模型更差。）

- 15 个 (K, lag) 组合经 FDR 校正后 **0 个通过**（最小 p=0.078，未达 0.05）
- 逐年 IC 符号来回翻转，非「某一年有效」而是「连符号都不稳」
- **但失效区间成立**：7 日降水异常 IC 在生长季 0.081、鼓粒期 0.051、非生长季 0.000 → 天气只在该窗口有作用

完整论证见 `docs/研究报告.md`；结论强度的边界见 §5.5（次日窗口最小可测 ΔR²≈1.1 个百分点，更小的真实增量测不出来）。

## 输入

| 数据 | 来源 | 接口 / 路径 | 覆盖 |
|---|---|---|---|
| 历史气象观测 | Open-Meteo Archive（ERA5 再分析） | `archive-api.open-meteo.com/v1/archive` | 2010-01 ~ 2025-10 |
| **归档预报（时点快照）** | Open-Meteo Historical Forecast | `historical-forecast-api.open-meteo.com/v1/forecast` | **2018-01 ~ 2025-10** |
| 期货日线 | PandaData | `get_future_daily`（按合约） | 2018-01 ~ 2025-10 |
| 库存 | PandaData | `get_future_inventory`（`symbol='M'`） | 周度 |
| 基差 | PandaData | `get_future_basis`（`underlying_symbol='M'`） | 日频 |

**接口参数坑（复跑必踩，已固化）**
- `get_future_inventory` 只认品种代码 `M`；传 `M2409.DCE` 回空、传 `underlying_symbol` 直接报错
- `get_future_basis` 只认 `underlying_symbol`；传合约代码报错
- `get_future_daily` 一次调用可取多年，不受 110 行限制（分钟线才受）

## 输出

| 文件 | 内容 |
|---|---|
| `data/weather_panel.parquet` | 12 网格点 → 州 → 产量加权的美豆带天气序列（双口径） |
| `data/futures_daily_M.parquet` | 豆粕主力连续日线 |
| `outputs/lag_scan_h{1,5,20}.csv` | K∈{7,14,30} × 滞后 0/5/10/20/40 的 IC / t / R²；`lag_scan.csv` 是 h1 的同内容副本（历史命名） |
| `outputs/model_compare_h{1,5,20}.csv` | 次日/一周/一月三个窗口上 B1 季节 → B2 价格 → B3 供需 → W 天气 的 IS/OOS 对照（报告与结论均以此三份为准） |
| `outputs/season_split.csv` | 生长季 / 鼓粒期 / 非生长季的失效区间检验 |

## 复跑

```bash
# 0. 依赖：复用主线一的虚拟环境（已含 pandas/pyarrow/yaml）
PY=../skill-futures-session-split/.venv/bin/python

# 1. 气象（两源分开抓，约 1 分钟）
$PY scripts/fetch_weather.py archive forecast

# 2. 聚合面板
$PY scripts/panel_weather.py

# 3. 豆粕日线（需要平台令牌，且不得与其他取数进程并发）
$PY scripts/fetch_futures_daily.py

# 4. 评估
$PY scripts/evaluate.py --source fc     # 主口径：归档预报
$PY scripts/evaluate.py --source obs    # 对照：再分析（强制滞后）
```

## 三条不能破的规矩

1. **不进再分析当信号**：ERA5 是事后修订的。主口径必须是归档预报，再分析只能作对照且必须加滞后。
2. **建仓在 t+1 开盘**：CBOT 隔夜信息已在 DCE 开盘价里；用收盘建仓会把传导段收益算进来，虚高。
3. **标准化参数只从 IS 估计**：均值方差、缩尾边界都只用训练期，OOS 直接套用。

## 失败边界

- 只验证 DCE 豆粕 M，不外推到油脂（棕榈油受产地降雨，机制不同）、玉米（饲料需求侧不同）
- 归档预报 2016-2017 不可用（温度为 0 占位），样本从 2018 起
- 只覆盖美豆带（约占全球大豆供给 1/3），巴西/阿根廷产区未纳入
- 日线口径，日内传导与夜盘跳空不刻画
- 若只在极端年份（如 2021 拉尼娜）有效，须标注为事件驱动而非稳定 alpha
