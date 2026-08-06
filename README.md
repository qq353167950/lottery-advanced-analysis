# 🎱 彩票高级综合分析引擎 (Lottery Advanced Analysis)

> 大乐透(DLT)与双色球(SSQ)号码高级统计分析引擎。

## 功能

- **14维基础指标**：频率冷热、遗漏、AC值、和值、跨度、奇偶、大 小、三区、重号、连号、同尾、副区冷热
- **11项高级数学模型**：马尔可夫链、贝叶斯、遗漏回补、蒙特卡洛、逆向形态、爆冷结构、副区专项等
- **数据驱动硬过滤**：按历史开奖分位数自动校准
- **单式 / 复式 / 胆拖**：支持 5 注单式、复式推荐、胆拖推荐
- **账本闭环**：自动结算历史推荐并记录新推荐
- **滚动回测**：评分模型 vs 随机基线
- **诊断模块**：时间序列、多元统计、误差分析（P0-P3）

## 快速开始

```bash
python scripts/fetch_data.py --game DLT --periods 200 --out data/dlt_history.csv
python scripts/analyze.py --game DLT --data data/dlt_history.csv --out result.json
python scripts/backtest.py --game DLT --data data/dlt_history.csv --window 100
python scripts/selftest.py
```

## 重要说明

- 彩票开奖是独立随机事件
- 本工具不保证中奖，也不声称提高中奖率
- 它的价值在于形态筛选、结构管理和诚实回测
