# 共同状态与证据合同

## 三表依赖

```text
当期领题表：phase + authoritative_question_id + paper identity
  -> 完成提交/验收表：同一题号 + artifact identity + QA evidence
  -> 组长初检表：同一题号 + reviewed artifact + review evidence
```

后表不能反向创造前表身份。题号缺失、期次冲突或论文身份冲突时，停止后续写入并标记 `CONFLICT`；不要以提交包目录名、压缩包名或展示标题补题号。

## 操作状态

私有操作账本至少区分：

- `READ_VERIFIED`：已从当前资源读取并唯一定位记录。
- `WRITE_VERIFIED`：写请求成功，且回读与预期一致。
- `INCOMPLETE_PERMISSION`：资源、表、字段或附件无所需权限。
- `INCOMPLETE_SCHEMA`：无法取得字段 schema、枚举或关联关系。
- `INCOMPLETE_EVIDENCE`：材料已定位，但硬门槛或证据不完整。
- `CONFLICT`：重复记录、期次/题号/论文/产物身份矛盾，或写后回读不一致。
- `NOT_APPLICABLE`：当前角色不负责该字段或该动作不适用。

不要把 `READ_VERIFIED` 写成“提交成功”，也不要把接口返回成功但未经回读的状态写成 `WRITE_VERIFIED`。

## 私有变更账本

账本应留在授权工作区，不提交到公开 skill 仓库。建议字段：

```yaml
operation_id: <LOCAL_OPERATION_ID>
occurred_at: <ISO_8601_WITH_TIMEZONE>
actor_role: <CLAIMANT_OR_SUBMITTER_OR_REVIEWER>
resource_alias: <CURRENT_PHASE_TABLE_ALIAS>
record_alias: <LOCAL_RECORD_ALIAS>
authoritative_question_id: <CURRENT_PHASE_QUESTION_ID>
read_schema_digest: <SHA256>
before_digest: <SHA256>
requested_changes:
  <SEMANTIC_FIELD>: <REDACTED_SUMMARY>
write_receipt_ref: <PRIVATE_EVIDENCE_REF_OR_NULL>
readback_digest: <SHA256_OR_NULL>
status: <OPERATION_STATUS>
blocker: <REDACTED_ERROR_CLASS_OR_NULL>
next_owner_role: <ROLE_OR_NULL>
```

摘要和哈希用于公开报告时仍需严格隐私扫描；哈希不能替代私有账本中的原始证据。

## 错误分级与停止条件

| 现场事实 | 状态 | 可以做 | 禁止做 |
|---|---|---|---|
| 当前身份不能打开资源 | `INCOMPLETE_PERMISSION` | 保存脱敏错误类别，联系现任所有者或迁移负责人 | 重放旧凭据、声称空表或猜测字段 |
| 能看视图，不能列字段或稳定记录主键 | `INCOMPLETE_SCHEMA` | 生成语义字段草稿 | 按列序号或屏幕坐标盲写 |
| 能读不能写 | `INCOMPLETE_PERMISSION` | 输出逐字段待填草稿和证据索引，交给有权限角色 | 把草稿描述为已提交 |
| 写请求成功，回读旧值 | `CONFLICT` | 保留回执并让所有者检查自动化、公式或权限 | 重复批量写入或报成功 |
| 同名记录多条 | `CONFLICT` | 用当期题号、论文 canonical ID 和 artifact digest 消歧 | 选“最像的一条” |
| 审核字段只对负责人开放 | `NOT_APPLICABLE` 或 `INCOMPLETE_PERMISSION` | 填提交人可写页，交接审核草稿 | 代替负责人填通过/返修 |

## 去隐私化

公开文档和测试只能使用 `<CURRENT_PHASE_QUESTION_ID>`、`<TABLE_ALIAS>`、`<PRIVATE_EVIDENCE_REF>` 等不可执行占位符。禁止放入真实 tenant 域名、文档/base/table/view/record token、人员标识、本机绝对路径、二维码、授权码、附件下载签名或真实截图。权限错误只保留错误类别、所需能力和发生阶段，不复制带 token 的完整请求或响应。
