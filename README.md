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
- 默认时间：每天北京时间 `08:30`（GitHub cron 使用 UTC：`30 0 * * *`）
- `games=auto`：只分析当天开奖的彩种；无开奖日（如周五）自动跳过，不推送

### 默认推送内容

每天只推当天开奖彩种，包含两种情况：

1. **默认正常5注**：5注单式，合计10元；该金额不参与复式预算限制。
2. **2组复式**：两组复式合计不超过20元。

当前预算策略：

- 大乐透：`6+2` 复式（12元） + `5+3` 后区复式（6元），复式合计18元。
- 双色球：`7+1` 复式（14元） + `6+2` 蓝球复式（4元），复式合计18元。

发送前会再次检查复式合计金额，超过 `--multi-budget` 会直接失败，不发送通知。

### 开奖日规则

- 大乐透：周一、周三、周六
- 双色球：周二、周四、周日
- 周五：无大乐透/双色球开奖，自动跳过

如遇官方节假日调整，可在 Actions 页面手动触发，并把 `games` 改成 `DLT` 或 `SSQ`。

### PushPlus Token 配置

在 GitHub 仓库的 `Settings -> Secrets and variables -> Actions` 中添加：

- `PUSHPLUS_TOKEN`：必填，PushPlus 用户 token 或消息 token
- `PUSHPLUS_TOPIC`：可选，群组 topic
- `PUSHPLUS_CHANNEL`：可选，指定渠道

官方说明：用户 token 和消息 token 都可以填在发送接口的 `token` 参数上；消息 token 可创建多个，适合脚本、程序或第三方系统场景。token 只通过 GitHub Secret 注入，不写入代码、不提交到仓库。

### Action 保活

仓库另有 `.github/workflows/keepalive.yml`：

- 每月 1 日自动更新 `.github/keepalive.txt` 并提交一次。
- 目的：给公开仓库制造真实 repository activity，降低 GitHub 因长期无仓库活动而停用 schedule 的风险。
- 使用 `ACTIONS_KEEPALIVE_PAT` Secret 推送，避免主分支保护拦截保活提交。
