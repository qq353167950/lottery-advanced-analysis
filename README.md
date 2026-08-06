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

仓库内置按中国大陆开奖日自动运行的推荐流程：

- 工作流：`.github/workflows/daily-recommend.yml`
- 默认时间：每天北京时间 `08:30`
- `games=auto`：只分析当天开奖的彩种；无开奖日则跳过推送
- 默认模式：当天彩种生成 **5 注正常单式**，合计 10 元
- `mode=multi`：预算模式，严格检查总金额不超过预算
- `mode=mixed`：复式 + 单式混合，严格检查总金额不超过预算

### 开奖日规则

- 大乐透：周一、周三、周六
- 双色球：周二、周四、周日

如遇官方节假日调整，可在 Actions 页面手动触发，并把 `games` 改成 `DLT` 或 `SSQ`。

### PushPlus Secrets

在 GitHub 仓库的 `Settings -> Secrets and variables -> Actions` 中添加：

- `PUSHPLUS_TOKEN`：必填，PushPlus 用户 token 或消息 token
- `PUSHPLUS_TOPIC`：可选，群组 topic
- `PUSHPLUS_CHANNEL`：可选，指定渠道

token 只通过 GitHub Secret 注入，不写入代码、不提交到仓库。

### 预算模式说明

- 大乐透 `multi`：使用一组 `6+2` 和一组 `5+3` 混合复式，共 18 元。
- 双色球 `multi`：由于最小 `7+1` 复式就是 14 元，两组真复式无法控制在 20 元内，因此自动回退为 `7+1` 复式 + 3 注单式，共 20 元。
- 所有模式在发送前都会再次检查总金额，超过预算会直接失败，不发送通知。
