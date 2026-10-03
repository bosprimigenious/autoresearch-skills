# QA skill routing evals

`qa-skill-routing.jsonl` 是一个 15 条的小型路由数据集，覆盖：

- 直接点名 skill 的请求；
- 不点名但语义上应路由到 QA 或 Baseline 审查的请求；
- 不应触发这两个 skill 的负例。

先运行结构门禁：

```sh
python3 scripts/validate_route_evals.py
```

这个命令只验证 JSONL schema、条数、分类平衡和期望标签，不执行模型，也不产生“路由准确率”。要声称某个 Agent/模型的路由效果，必须在固定模型版本、干净会话和相同数据集上实际运行全部 case，保存逐条原始输出、判定器版本及汇总结果。仓库当前不发布未经实测的准确率数字。
