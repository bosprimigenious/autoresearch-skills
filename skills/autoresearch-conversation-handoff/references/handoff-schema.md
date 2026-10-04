# 交接结构

1. **一句话状态**：PASS、FAIL、NOT READY 或部分完成。
2. **权威身份**：题目 ID、仓库、分支、提交、最终包、SHA256。
3. **实测证据**：测试、QA、原始分数、轨迹时长和报告路径。
4. **外部状态**：平台上传、审核、表格写入；页面回执与本地准备分开。
5. **失败与走偏**：失败命令、真实原因、是否影响被测对象。
6. **边界**：未运行的 Docker、Harbor、Hidden、稳定性或费用动作。
7. **存储**：必须保留、已远端备份、可重建、禁止进 Git 的大文件。
8. **下一步**：可执行命令、顺序、反序风险、完成标准。

远端付费任务另加一行生命周期矩阵：`controller_stopped`、`workers_drained`、`evidence_pulled`、`local_hash_verified`、`os_shutdown`、`provider_billing_stopped`。每项只填 `VERIFIED / UNVERIFIED / FAILED` 和脱敏证据引用；不得用一个状态推导另一个状态。

数字必须来自最终报告或现场回读；说明文件与 JSON 冲突时先修复再交接。
