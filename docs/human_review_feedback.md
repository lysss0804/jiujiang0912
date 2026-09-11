# 人工复核反馈草案 C-DRAFT-V0.2

## 当前用途

本仓库（Algorithm / Multi-Agent 侧）已建立人工复核输入和留痕结构，用于本地流程测试。
它暂时写入本地 JSONL（`artifacts/reviews/review_feedback.jsonl`），
**不代替后端侧（传统后端团队）的 PostgreSQL 表和事务接口**。

一次复核至少记录：

- `run_id`：对应哪一次风险研判；
- `supplier_id`：对应哪家供应商；
- `reviewer_id`：复核人员编号；
- `review_result`：通过、驳回或补充证据；
- `final_action`：观察、预警或整改；
- `review_comment`：复核说明；
- `actual_outcome`：后续是否真的发生风险；
- `outcome_week`：实际结果发生周；
- `reviewed_at`：复核时间。

`APPROVED` 必须同时选择最终动作。同一 `run_id` 在本地原型中只允许提交一次，防止重复审核。

## 后续与后端对接

后端需要把本地 JSONL 保存替换为数据库写入，并补充登录身份、角色权限、并发控制和修改历史。
字段草案见 `docs/contracts/human_review_request.C-DRAFT-V0.2.schema.json` 和
`human_review_record.C-DRAFT-V0.2.schema.json`。接口细节见 `docs/后端交接文档.md`。

## 待业务方确认

需业务侧确认：谁能审核、能否修改结论、日志保存期限、审核备注是否脱敏，
以及「补充证据」后是否允许再次提交。
