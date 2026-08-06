# 🎱 彩票高级综合分析引擎 (Lottery Advanced Analysis)

> 大乐透(DLT)与双色球(SSQ)号码高级统计分析引擎。

## 功能

- **14维基础指标**：频率冷热、遗漏、AC值、和值、跨度、奇偶、大 小、三区、重号、连号、同尾、副区冷热
- **11项高级数学模型**：马尔可夫链、贝叶斯、遗漏回补、蒙特卡洛、逆向形态、爆冷结构、副区专项等
- **数据驱动硬过滤**：按历史开奖分位数自动校准
- **单式 / 复式 / 胆拖**：支持 5 注单式、复式推荐、胆拖推荐
- **账本闭环**：自动结算历史推荐并记录新推荐
- **滚动回测**：评分模型 vs 随机基线
- **不确定性与验证**：蒙特卡洛 95% 威尔逊置信区间、滚动回测与随机基线对照

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

---

## 🤖 GitHub Actions 自动推送

仓库内置每日自动推荐流程：

- 工作流：`.github/workflows/daily-recommend.yml`
- 默认时间：每天 `00:30 UTC`
- 默认内容：大乐透和双色球各生成 1 注单式推荐
- 支持：GitHub Actions 页面手动触发

### 配置 PushPlus

在 GitHub 仓库的 `Settings -> Secrets and variables -> Actions` 中添加：

- `PUSHPLUS_TOKEN`：必填，PushPlus 用户 token 或消息 token
- `PUSHPLUS_TOPIC`：可选，群组 topic
- `PUSHPLUS_CHANNEL`：可选，指定 PushPlus 渠道

未配置 `PUSHPLUS_TOKEN` 时，Action 会因为无法发送通知而失败；token 只通过 GitHub Secret 注入，不写入代码。

### 手动调整

手动触发 workflow 时可以选择：

- `games=DLT` 或 `games=SSQ`
- `mode=single`、`mode=multi` 或 `mode=dantuo`
- `bets`：单式注数或复式组数
- `mc`：蒙特卡洛模拟次数

要更改每天的执行时间，修改 workflow 中的 cron 表达式。GitHub Actions 的定时表达式默认按 UTC 执行。
