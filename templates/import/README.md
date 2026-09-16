# 银行导入模板说明

- `supplier_template.csv`：重要程度填“重要/一般”或 `IMPORTANT/GENERAL`。供应商编号、名称、统一社会信用代码、重要程度必填。
- `risk_event_template.csv`：风险维度只能是“公司背景、司法、失信、经营风险、经营状况、知识产权”；严重程度为 0–5。发生日期和事件周次二选一：填写 `YYYY-MM-DD` 的发生日期时，系统自动计算 ISO 周次。
- 先以 `confirm=false` 上传预检，修正错误后再用 `confirm=true` 写入数据库。CSV 支持 UTF-8（含 BOM）和 GB18030 编码。
- 官方 API 数据同步与 CSV 导入使用同一套规范化风险事件字段；证据链接应尽量指向可审计的原始公告页。
