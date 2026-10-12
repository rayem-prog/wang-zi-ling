# 工作包 A 交付索引

范围：建立故障基线与后续修复设计；本次未修改业务代码，也未进入工作包B。

- [设计方案](design.md)：现有调用链、7类故障、状态和权限规则、接口、原子发布、模块职责、16项验收方法。
- [实测基线](baseline.md)：机器和数据规模、只读真实审计、实际结果查询、隔离故障与测试记录，明确区分实测/源码推导/未测。
- [B/C任务清单](handoff.md)：依赖顺序、修改文件、验证场景和命令。
- [8组完整接口示例](contract-examples.json)：目标接口响应。
- [接口字段Schema](contract.schema.json)：字段类型、枚举与空值约束；跨字段规则另由验证脚本检查。
- [原始诊断数据](baseline-evidence.json)、[复现脚本](baseline_probe.py)：可控时间与临时样本。
- [交付校验脚本](verify_artifacts.py)、[校验记录](verification.txt)：接口示例及文档完整性检查。

方案与隔离基线已交付。生产场景基线仍有未测项：真实生成/训练耗时、实际服务重启、浏览器渲染及真实HTTP并发P95；不得据此宣布所有生产验收通过。第一步得到的是可指导实施、可复验的设计，后续B/C按验收矩阵补齐实测。

从仓库根目录执行：

```sh
.venv/bin/python docs/work-package-a-20261012/verify_artifacts.py
.venv/bin/python docs/work-package-a-20261012/baseline_probe.py
```

第二个命令只做诊断测量，会重新覆盖证据JSON；需要对比时先备份证据文件。baseline.md记录的是本次交付测量，重新采样后须同步该表。
