# 模拟数据源

本目录保存 **赵文雅** 生成并维护的 200 家虚构供应商模拟数据，首版导入日期为 2026-09-07。

数据仅用于竞赛原型、模型训练和系统测试，不包含真实银行业务数据。七张 CSV 属于同一批次：

- `suppliers.csv`
- `contracts.csv`
- `projects.csv`
- `bank_systems.csv`
- `risk_events.csv`
- `rectify_records.csv`
- `weekly_snapshot.csv`

经独立复算，10,400 条周度记录的事件数量、风险分数与整改状态均与上游生成规则一致。
（该 CSV 中保留了一列旧实验期的"未来三周升级标签"字段，属历史遗留，**现行系统不做预测**，该字段不参与任何分析。）

## 字段变更（2026-09-11）

- `suppliers.csv` 新增 `importance_level` 列，取值 `重要 / 一般` 两级（200 行，`重要` 128 / `一般` 72）。
  该列由银行名单定义，作为供应商重要性的唯一权威来源，系统只读不算。
  取值按各供应商 `contracts.csv` 的 `contract_importance` 映射：`重要外包→重要`、`一般外包→一般`。
- `contracts.csv` 删除 `name` 列（展示文本，代码不读取）；`contract_importance` 保留为展示字段。
- `risk_events.csv` 追加 50 条事件，补齐 `司法`（13）、`失信`（17）、`知识产权`（20）三个维度，总数 539 → 589。
- `weekly_snapshot.csv` 的 `business_exposure` 列**保留**：业务逻辑未使用，
  但 `app/data/loader.py` 硬读该列，删除会导致装载失败。
- `projects.csv`、`bank_systems.csv`、`rectify_records.csv` 未改动。

> 说明：本目录原有的 `leading_indicators.csv`（领先指标对照实验产物，`LEADING-SIM-V0.1`）
> 连同其生成脚本 `scripts/generate_leading_indicators.py` **已随预测链路一并移除**，
> 现行系统只做当前风险分级，不做预测。
