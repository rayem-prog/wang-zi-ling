# 工作包 B/C 实施任务清单

本清单是A阶段设计交付，未执行实现。执行前阅读 [设计](design.md)、[实测基线](baseline.md) 和 [完整接口示例](contract-examples.json)。技术栈沿用 Python/FastAPI/SQLite/pandas、Vue，优先日线；不增加远程数据库。每项先编写列出的回归用例，确认当前缺陷，再实现并运行针对性检查。

## 工作包 B：持久化、统一接口与更新期间稳定显示

### B1 结果存储和原子发布（优先级P0）

- [ ] 新建 `services/result_store.py`、`services/result_contract.py`，按design第6节建立结果库表和短事务；不得更改行情库主数据。
- [ ] 定义 `read_view()`、`stage(payload,job_id)`、`publish(snapshot_id,expected_versions,owner)`、`revoke(snapshot_id,reason)` 的类型与异常；read_view须一次读到payload、认证、job、风险投影、head和revision。
- [ ] 新建 `tests/test_result_store.py`，覆盖A02/A04/A06：同版行统计、stage失败、事务故障前后、撤销缓存；使用两个连接验证一致性，不能仅mock数据库。
- [ ] 运行 `.venv/bin/pytest tests/test_result_store.py -q`，保存原始输出；只有通过才能供B2使用。

输出：可存取不可变SnapshotView且可事务发布/撤销。依赖：无。核心规则：revoked不能重新发布，读损坏不能当正常为空。

### B2 将生成接入发布事务（P0）

- [ ] 修改 `scripts/daily_update.py`、`strategy/decision.py` 与必要的结果适配器，冻结数据/策略/参数版本，记录候选与最终计划的区别以及真实筛选漏斗。
- [ ] 删除“先删旧meta”的职责，计算期间仅写新staged；校验后切换head，兼容CSV只能由已发布快照导出。
- [ ] 为 `api/jobs.py` 加入发布所需的持久化job基础、阶段回调与输入身份；发布成功与job成功同事务完成。完整lease/恢复检查由C2补齐。
- [ ] 新建 `tests/test_result_publish.py`，覆盖A03/A04/A09/A15：features异常、报告或通知失败、空结果成功发布、输入变化拒绝、旧文件导入不伪可信。
- [ ] 运行 `.venv/bin/pytest tests/test_result_publish.py tests/test_daily_update.py tests/test_verified_update.py -q`。

输出：新版本失败不影响旧head，合法空结果可发布。依赖：B1。通知失败为发布后副作用错误，不将已发布结果误标撤销。

### B3 单响应查询及兼容适配（P0）

- [ ] 新建 `services/action_permissions.py`；修改 `services/recommendations.py`、`services/signals.py`、`api/routers/signals.py`，提供 `GET /api/signals/snapshot`。
- [ ] 按契约返回全量、筛选后数量/分档/时间/版本/权限；适配旧UI行字段和旧端点。GET不调用health_cache签名扫描，不触发后台工作。
- [ ] 新建 `tests/test_signals_snapshot.py`，覆盖A01/A07/A08/A09/A10，使用受控Event和spy证明查询无训练/审计/抓取调用；将contract-examples作为目标契约用例。
- [ ] 运行 `.venv/bin/pytest tests/test_signals_snapshot.py tests/test_signal_services.py tests/test_signal_readiness.py -q`；旧端点回归预期在迁移后明确更新，不能删除安全门禁测试以掩盖问题。

输出：完整SnapshotView JSON，503与正常空结果分离。依赖：B1/B2。不能在页面请求中hash行情历史。

### B4 页面保持、状态栏和单请求（P0）

- [ ] 修改 `frontend/src/api.js`、`frontend/src/features/signals/useSignalsPage.js`、`frontend/src/views/SignalsView.vue`，同一次响应更新列表和统计。
- [ ] 日期、历史、任务阶段及错误在有结果时仍可见；操作按钮按权限；保留查询失败时的历史回看；接撤销响应立即禁用并清可操作行。
- [ ] 拆分请求加载与后台job；请求序号防逆序返回；卸载取消轮询；连接失败退避并暂停操作。
- [ ] 新建 `frontend/tests/signals-snapshot.test.mjs` 和浏览器验收场景A01/A03/A11/A14；检查日期、行数、常驻错误、禁止按钮、请求数量及旧响应防回退。
- [ ] 运行前端新增测试与现有 `node --test tests/*.test.mjs`（在frontend目录）；运行 `npm run build`；浏览器验证构建产物。

输出：180秒核验期间历史结果仍可见；所有空态都有明确文案。依赖：B3。可在最后集成浏览器测量，不用“测试传输快”替代渲染验证。

## 工作包 C：完整性、权限、任务恢复与性能验收

### C1 核验证书与操作门禁（P0）

- [ ] 修改 `api/health_cache.py`、`data/health.py` 和后台核验调度入口；把签名/完整审计移出页面路径，持久化认证版本与有效期。
- [ ] 改造 `api/routers/status.py`、`holdings.py`、`orders.py`、`paper.py`、`intraday.py` 中的推荐消费者；读历史与执行操作各自判断，操作接收snapshot_id并提交时重验。
- [ ] 新建 `tests/test_snapshot_permissions.py`；覆盖A05/A06/A10/A13，禁止新买入但允许持仓查询；撤销/风险变化到提交时必须拒绝且账本无买入副作用。
- [ ] 运行该文件及 `tests/test_unified_risk.py tests/test_health_cache_content.py tests/test_paper_api.py tests/test_intraday.py`；分别完成日线与盘中时效验证。

输出：所有执行入口防绕过，完整审计仍检测内容修改。依赖：B1/B3；B2阶段必须已冻结输入身份。

### C2 任务阶段、重启及并发恢复（P0）

- [ ] 修改 `api/jobs.py` 和启动恢复入口，完善持久化时间、阶段、error码、lease/owner、重试关联、意图去重。
- [ ] 验证活跃owner后才把遗留queued/running标为interrupted；实际重启子进程，结果可历史显示。
- [ ] 新建 `tests/test_result_job_recovery.py`，覆盖A04/A12：多进程互斥、各发布崩溃点、已提交成功不误判中断、不同data_only意图不静默合并。
- [ ] 运行该文件与 `tests/test_jobs.py tests/test_daemon_reload.py tests/test_execution_ledger.py`；保留崩溃点日志。

输出：明确任务终态和可恢复发布结果。依赖：B2。实际恢复测试不能只重建内存对象。

### C3 生产规模基线补测和验收（P1）

- [ ] 用固定行情快照、策略/参数摘要在隔离目录跑真实审计与生成，记录各阶段/总耗时，禁通知，不能改用户持仓。
- [ ] 按A16做真实HTTP每场景1000次、并发5、5000候选、后台写入，记录硬件、规模、提交、P50/P95/max；冷启动另外统计。
- [ ] 实际浏览器完成全部A01–A16用例，记录请求与DOM证据；P95暂定<500ms，失败则定位是库锁、序列化、查询还是渲染。
- [ ] 运行后端完整测试、前端测试及build，仅在新问题修复后重复相关检查；更新验收矩阵为通过/失败/未测。

输出：真实端到端验收报告；补齐A阶段未测的真实训练、重启、浏览器及并发数据。依赖：B4/C1/C2。

## 顺序与完成定义

B1→B2→B3→B4；C1与C2在对应B接口稳定后实施；C3最后集成验证。每项产出包含修改清单、相关测试结果、已知限制。现阶段交付文件不改变业务代码；进入B后才采用隔离工作目录实施。

若按原2–3工作日安排：第1天以故障/调用链及补充生产基线为主；第2天审查状态、接口及发布边界；第3天审查异常、权限消费者和任务拆分。本文是该安排的技术交付，不声称耗时已过去或已完成生产演练。
