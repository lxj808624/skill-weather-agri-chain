# skill-weather-agri-chain

量枢院（QuantSkills）本期任务交付：**天气—农产品链是否带来超出季节性、价格与已知供需的独立交易信息**。

链路：美豆带（IL / IA / NE / MN 按产量权重）降水与温度异常 → CBOT 大豆 → DCE 豆粕 M。

## 一句话结论

**在次日与一周持仓窗口未发现可利用的样本外增量；一月窗口样本功效不足，不下结论。**

| 持仓 | 天气模型 IS R² | OOS R² | 基线 B3 OOS | 天气系数 t |
| --- | --- | --- | --- | --- |
| 次日 | 0.0168 | −0.0228 | −0.0112 | 1.85 |
| 一周 | 0.0711 | −0.0844 | −0.0452 | 0.73 |
| 一月 | 0.1747 | −0.0312 | +0.0080 | 0.10 |

- 15 个 (K, lag) 组合经 Benjamini-Hochberg 校正 **0/15 通过**（最小 p=0.078）
- 逐年 IC 符号来回翻转；2021 拉尼娜年天气模型反而比基线更差
- **但失效区间成立**：7 日降水异常 IC 生长季 **0.081**、鼓粒期 0.051、非生长季 **0.000**

完整论证见 [`docs/研究报告.md`](docs/研究报告.md)，设计冻结见 [`docs/研究设计.md`](docs/研究设计.md)。

## 结论强度边界（必读）

见报告 §5.5：次日窗口的最小可测 ΔR² ≈ 1.1 个百分点，比这更小的真实增量**测不出来**；一月窗口的有效独立观测只剩 30–50 个，**任何排序结论都不成立**。所以本仓库的结论是「未发现」，不是「证明无效」。

## 怎么复跑

```bash
PY=../skill-futures-session-split/.venv/bin/python   # 或自建：pip install pandas pyarrow pyyaml

$PY scripts/fetch_weather.py      # Open-Meteo 归档预报 + 再分析
$PY scripts/fetch_futures_daily.py  # 平台 PandaData MCP
$PY scripts/fetch_fundamentals.py   # 库存 / 基差
$PY scripts/panel_weather.py        # 组装面板
$PY scripts/evaluate.py --source fc --horizon 1     # 主结果（1/5/20）
$PY scripts/robustness.py           # FDR / 滚动 / 逐年 IC
```

**数据未随本仓库分发**（`data/` 已在 .gitignore/上传时排除），原因有两个：行情属平台授权数据不应二次分发；气象数据体积较大但可由脚本完整重建。取数需要平台 OAuth 令牌（`~/.dsh-quantstudio/quantskills/panda-mcp/oauth.json`）。

## 目录

| 路径 | 内容 |
| --- | --- |
| `SKILL.md` | skill 声明、输入输出、复跑、失败边界 |
| `docs/研究报告.md` | 11 节主报告（负结果 + 失效区间 + 平台记录 + 价值验收）|
| `docs/研究设计.md` | 冻结设计（选链理由、三处滞后、判据）|
| `docs/评分点自检.md` | 题目四条评分维度逐条对照 |
| `scripts/` | 取数 / 面板 / 评估 / 稳健性 |
| `config/regions.yaml` | 区域、权重、生长季、双起始日期 |
| `outputs/` | 16 个产物（model_compare / lag_scan / fdr / walk_forward / yearly_ic / 索引）|
| `review/` | 三连 review + 提交前终审留痕 |
| `platform/` | 提交前平台接口复测记录 |

## 已知限制

- 平台一键运行不可用（宿主目录抓取故障，issue #24 / PR #25），交付为等价可复跑流水线
- 期货模拟赛未连接账户，报告 §9 逐条声明等价回放范围
- 成本口径为常规假设（单边约 3.5bp），非模拟赛实测
- 提交前复测：日线/库存接口返回空，数据以 2025-11-17 快照为准，复跑前先探接口
