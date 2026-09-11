# 基于 tRPC-Agent 设计多租户节点化 Agent 部署平台

## 2026-09-08 原 README 要求对应的实现入口

本轮实现范围直接取自本 README 的“具体要求、题目难点、验收标准”。

- 常驻 Worker 使用已发布租户配置；需要确认的 `write_artifact` 通过持久提案、IM 确认、独立 `action-worker` 保存当前会话文件的新版本。批准前不写文件，旧版本保留。
- Admin API 支持操作员和租户身份。`TRPC_ADMIN_PRINCIPALS` 是身份配置数组，每项包含 `actor`、`role`（`tenant_admin` / `viewer`）、`tenant_ids`、`token_ref`（`env://...`），以及可用于配置的 `profile_ids`、`secret_refs`。令牌至少 32 字符且不能复用；越租户、只读身份写入、未分配的后端或密钥引用返回 403。操作员负责初次分配租户与资源。
- 租户 Model Filter 按 `audit_policy` 处理已登记密钥、凭据模式、电子邮箱和中国大陆手机号；输出采用最终文本，避免流式分片拆开敏感值。该规则不是任意类别个人信息识别器；SDK/通道日志仍独立禁止原始正文和密钥输出。
- 已收到供应商请求编号的费用未知调用可通过 `POST /admin/tenants/{tenant_id}/model-attempts/{attempt_id}/reconcile` 核对。接口只使用服务保存的供应商回执查询实际费用，不接受客户端声明金额；缺少回执继续保留未知费用。
- `start.sh` / `stop.sh` / `build.sh` 使用 `docker-compose.live.yml`。先在部署环境提供该文件要求的凭据、运行 `migrate` 和 `init-resources`，再通过 Admin API 登记后端、发布租户配置和授权 IM 成员。真实通道投递由 `channel` 负责，部署不启动模拟 Dispatcher。

真实验收需要本机 Python 环境安装 `.[dev,im]`，以及真实 PostgreSQL、Redis、Qdrant、MinIO 和现有 IM 凭据。可启动 `docker-compose.protected-test.yml` 并加载 `deploy/protected-test.env.example`；密钥从 `.secrets/im.json` / `.secrets/feishu.json` 读取，不要提交这些文件。

```sh
python -m trpc_service._cli live-acceptance --test-timeout 600
# 等价入口：sh e2e.sh
# 真实存储接管 + 真实 IM 联合验收：sh ops.sh
```

每次创建独立 PostgreSQL 测试数据库并选择空闲 Redis 逻辑库，不清空旧证据。按终端输出分别向企微和飞书发送保存口令，在 IM 内执行机器人返回的确认指令；两个 Agent Worker 重启后，再发送读取/检索口令。最终报告检查实际工具、文件内容、Memory、Summary、模型费用、投递回执与贯穿各进程的 Trace。需要用户实际看到回复的确认独立记录。群聊、断网重连和容量压测未由这四条消息自动证明。

`tests/integration/dual_worker_acceptance.py`、`runtime_smoke.py` 已改用该真实入口。模拟模型/协议仍用于故障分支的组件单测，不能替代真实 IM 验收；旧 SDK 原生写契约保留为诊断，正式存储门禁检查服务自有 protected 适配器。历史开发记录和历史报告不等同于本轮通过结果。

## 当前进展

- [2026-09-11 飞书群媒体与撤回真实验收](docs/飞书群媒体与撤回真实验收-2026-09-11.md)：在 PostgreSQL、Redis、MinIO、双 Worker 常驻环境中完成独立图片、独立文件和已完成入站消息撤回。图片/文件均写入租户 Artifact 并执行一次；撤回回执为 `recalled`，已完成输入保留 `succeeded` 并标记 `message_recalled`。富文本 `post`、真实 429、长文本真实分段和执行中撤回竞态不计作通过。

- [2026-09-07 服务自有原子 Session 与租户 IM 全链路](docs/服务自有原子Session与租户IM全链路-2026-09-07.md)：按用户选择的第二种方案，通过官方公开接口实现 PostgreSQL/Redis 原子存储适配器，接入 protected 运行模式、迁移与双租户配置。附链路图、独立双 Worker 真实 IM 验收入口；本地测试已通过，真实新格式联合验收尚待 Docker 后端就绪。

- [原始需求对照与范围校准](docs/第三阶段范围校准-原始需求对照-2026-09-06.md)（按用户要求严格围绕下方原题开发，保留已明确要求的 UI/真实联调，不追加商业系统、全媒体或固定登录/页面方案）

- [第三阶段持久审批实现](docs/第三阶段持久审批实现-2026-09-06.md)（确认事务、独立执行器、通知恢复、后台接口及 schema 5；实际业务工具与 UI 仍待接入）

- [第三阶段真实文本链路](docs/第三阶段真实文本链路实现-2026-09-06.md)（隔离 IM → 官方 Runner → 原生 SQL Session → OpenRouter 实际费用 → 投递；附两通道联调入口与验收边界）
- [第三阶段预算治理实现](docs/第三阶段预算治理实现-2026-09-06.md)（日/月共享预算、精确结算、未知费用、Model Filter 与后台接口，schema 4）

- [2026-09-06 第三阶段开发方案：IM、治理运维与管理 UI](docs/第三阶段开发方案-IM接入与治理运维-2026-09-06.md)（企业微信长连接 + Telegram；开发与验收依据）
- [第三阶段实施进度](docs/第三阶段开发进度-2026-09-06.md)（通道、隔离、持久收发、授权预算及文本联调已实现；完整审批、媒体、管理 UI 与生产联合验收尚未完成）
- [首次配置指南](docs/真实IM首次配置指南-2026-09-06.md)及[参考仓库源码核对记录](docs/第三阶段参考仓库源码核对-2026-09-06.md)
- [2026-08-27 项目方案书（提交版）](docs/项目方案书-2026-08-27.md)
- [2026-08-27 详细技术设计](docs/方案设计-2026-08-27.md)
- [2026-08-31 第一阶段补充开发方案](docs/第一阶段补充开发方案.md)
- [2026-09-03 第二阶段开发方案](docs/第二阶段开发方案.md)（开发与验收依据）
- [2026-09-05 第二阶段补全开发方案](docs/第二阶段补全开发方案-2026-09-05.md)（补全顺序与完整验收标准）
- [2026-09-05 双 Worker 服务运行与验收](docs/双Worker服务运行与验收-2026-09-05.md)（持续消费、独立投递、原生结果恢复与派生任务；当前为显式模拟模式，SDK 原子写保护仍阻断生产发布）
- [2026-09-04 第二阶段验收报告](docs/第二阶段验收报告.md)（实现范围、真实后端与可复现命令）
- [2026-09-05 迁移与后端切换 E2E](docs/第二阶段迁移E2E测试说明.md)及[已知缺陷修复验收](docs/已知缺陷修复与验收-2026-09-05.md)（原五项迁移失败与模型状态失败已修复；第二阶段完整生产验收仍有未覆盖能力）
- [运维架构与容量评估](docs/运维架构与容量评估.md)（故障降级、灰度回滚、架构/时序与部署）及[实测容量报告](docs/容量基准报告-2026-09-05.md)（两种后端、8 个并发档位；模型与通道为模拟）
- 第一阶段运行内核已经完成：租户后端 namespace 强隔离、Channel Binding 验签顺序、租户/Binding/应用状态路由、单聊/群聊/线程 HMAC 身份、精确 Runner Registry、AgentContext 元数据注入、工具白名单和安全通道事件投影。
- 双租户 Fake Adapter/Fake Runner 验收链路已经覆盖相同外部用户的租户隔离、精确 Runner 选择、上下文隔离、安全输出和验签失败零执行。
- 真实内核 E2E 使用 tRPC-Agent `Runner`、`LlmAgent`、`FunctionTool` 和共享 InMemory Session，覆盖双租户两轮会话、真实工具调用、模型错误隔离以及 v3→v4→v3 灰度回滚。
- 第二阶段共享状态与可靠性层已经实现：版本化 Storage Resolver、tRPC Session/Memory/Summary 公共接口包装、SQL Inbox/Outbox、lease/fencing/revision CAS、工具账本、Durable Post-turn、MinIO Artifact、Local/Qdrant Knowledge、Audit、Redis→SQL Session 复制与版本化向量迁移。
- Docker Compose 可启动 Gateway、两个 Worker、PostgreSQL/pgvector、Redis AOF、Qdrant、MinIO 和 OpenTelemetry Collector；真实探针覆盖三种 Session 后端的一致契约、跨实例 Memory、Redis→SQL 复制、Local→Qdrant 迁移和 S3 Artifact。
- 2026-09-05 修复后完整本地回归：117 passed、4 skipped（MinIO 与网络故障测试需 real 模式），无 xfail。新构建镜像上的完整真实后端严格回归：121 passed，无 skip/xfail，含运维故障 6 项全部通过。迁移要求目标停写，尚不能证明在线迁移和完整双 Worker 故障恢复。详见已知缺陷修复验收报告。

## 第二阶段运行与验收

SDK 基线为官方 `trpc-group/trpc-agent-python` 的固定提交 `f05797d9f9dff2461922b5985aeccc1b636b7c8d`，
不使用本地二开或 fork。下方历史报告中的 fork 测试只作为历史记录，最终结果以本次官方 SDK 验收为准。

新增双 Worker 模拟闭环可通过 `docker compose up -d --build gateway worker-1 worker-2 dispatcher post-turn`
启动，通过 `python tests/integration/dual_worker_acceptance.py` 验证跨进程上下文、最终原生写入后的进程退出、
100 条实际投递及派生任务重启追平。生产模式不会静默使用模拟模型；当前仍未达到第二阶段全部完成标准。

```bash
# 启动最小多节点环境
sh start.sh

# 独立执行真实四后端验收，结束后自动停止环境
sh integration.sh

# 真实迁移与后端切换严格验收
sh e2e.sh

# 运维故障验收和容量测量，输出到 reports
sh ops.sh

# 本地质量检查
python -m pytest -q
flake8 trpc_service tests
git diff --check
```

`integration.sh` 使用独立的 `trpc` 平台数据库和 `trpc_runtime` tRPC Session 数据库，避免框架表与平台表同名冲突。真实 IM 凭据、在线模型吞吐和生产 Kubernetes 清单不包含在本阶段代码中；接口语义、指标和生产拓扑约束记录在开发方案内。

## 背景和价值
企业在落地 Agent 应用时，通常不会只部署一个单体机器人，而是希望面向多个部门、多个业务线、多个 IM 入口和多个数据后端
，构建一套可统一管理的 Agent 平台。例如：客服团队希望把 Agent 接入企业微信，研发团队希望接入内部群机器人，运营团队>希望接入微信公众号或微信客服，不同租户又需要隔离会话、记忆、知识库、工具权限和审计日志。
tRPC-Agent-Python 已经具备 Agent 编排、Tool / MCP、Session、Memory、Knowledge、Filter、Telemetry、FastAPI 服务化、OpenClaw / IM 通道、A2A / AG-UI 等能力。该题要求基于这些能力设计一个“多租户、可节点化部署、支持多后端数据同步、可接>入微信 / 企业微信等 IM 软件”的生产级方案。
这个题目解决的业务痛点是：企业希望把 Agent 能力从单点 demo 扩展成平台化服务，同时满足租户隔离、弹性部署、数据一致性
、IM 触达、审计合规和后端可替换等要求。它的价值在于把框架能力真正映射到企业级 Agent 平台架构，而不是只停留在单个 Agent 脚本。 
根据需要可以选择Python或者Go语言框架进行实现
任务描述
请设计一个基于 tRPC-Agent-Python 的多租户节点化 Agent 部署平台。平台需要支持多个租户创建和部署自己的 Agent，每个租>户可以绑定不同 IM 通道、选择不同数据后端、配置不同工具权限和知识库，并允许多个 Agent 节点水平扩展。系统需要考虑跨节
点会话路由、数据同步、后端适配、IM 消息接入、监控审计和故障恢复。
本题以架构设计为主，可以包含少量关键伪代码、接口定义或数据模型示例。不要求实现完整系统，但方案必须足够具体，能指导>后续工程落地。

## 具体要求
### 多租户与节点部署
- 设计租户模型，至少包含 tenant_id、应用配置、模型配置、工具权限、IM 通道配置、数据后端配置、审计策略。
- 设计节点部署拓扑，说明 Agent Gateway、Agent Worker、Channel Adapter、Storage Adapter、Admin API、Telemetry Collector 等组件如何协作。
- 支持多节点水平扩展，说明用户消息如何路由到正确租户和正确 session。
- 说明是否需要 sticky session；如果不需要，说明如何依赖共享 Session / Memory 后端实现无状态 Worker。
- 设计租户隔离机制，包括配置隔离、数据隔离、工具权限隔离、日志脱敏和密钥管理。

### 数据同步与多后端支持
- 支持不同租户选择不同数据后端，例如 InMemory、Redis、SQL、向量库、对象存储或外部 Memory 服务。
- 设计统一的数据访问抽象，说明 Session、Memory、Summary、Artifact、Knowledge、Audit Log 分别如何存储。
- 设计数据同步策略，至少覆盖：  
- 多节点并发写入同一 session 的一致性。
- Session event、state、summary 的更新顺序。
- Memory 写入后的跨节点可见性。
- 后端从 Redis 迁移到 SQL 或从本地向量库迁移到远端向量库时的数据迁移方案。
- IM 消息重复投递时的幂等处理。
- 说明不同后端的一致性取舍，例如强一致、最终一致、读写延迟、成本和运维复杂度。
- 给出一个最小数据模型或表结构示例，至少包含 tenant、agent app、session、message/event、memory、summary、channel binding、audit log。

### IM 软件接入
- 设计 IM Channel Adapter，支持企业微信、微信客服、微信公众号、Telegram 或其他 IM 通道中的至少两类。
- 说明外部 IM 消息如何转换为 tRPC-Agent-Python 的用户输入，Agent Event 如何转换为 IM 回复、流式消息或卡片消息。
- 设计 IM 账号和租户绑定方式，包括 webhook URL、token、secret、回调验签、消息去重、用户身份映射。
- 说明群聊和单聊的 session_id 生成规则，以及用户跨群、跨租户时的隔离策略。
- 考虑 IM 平台限制，例如消息长度、频率限制、异步回复、图片 / 文件消息、撤回或失败重试。

### 治理、监控和安全
- 使用 Filter 设计租户级治理策略，例如工具白名单、敏感信息脱敏、预算限制、危险工具二次确认、IM 用户权限校验。
- 设计监控指标，例如请求量、模型调用耗时、工具调用耗时、IM 投递成功率、错误率、token 消耗、每租户成本、Session 后端延迟。
- 说明如何接入 OpenTelemetry 或等价 tracing，要求 trace 能串起 IM callback、Runner 执行、Tool 调用、Session / Memory 读写和 IM 回复。
- 设计审计日志字段，至少包含 tenant_id、channel、user_id、session_id、agent_name、tool_name、decision、latency、error_type、cost、trace_id。
- 说明密钥管理和脱敏策略，IM token、模型 API key、数据库密码不能明文出现在日志、trace 或错误报告中。

### 故障恢复与运维
- 设计节点故障、IM 重试、数据库短暂不可用、模型超时、工具执行失败时的降级策略。
- 说明如何做灰度发布和租户级配置回滚。
- 说明如何做容量评估，例如每节点并发 session 数、平均 token 消耗、Redis / SQL QPS、IM 回调峰值。
- 设计最小可运行部署方案和生产推荐部署方案，可以使用 Docker Compose、Kubernetes 或等价部署方式描述。
交付物
- 一份架构设计文档，建议 2000 – 4000 字。
- 一张系统架构图，展示 Gateway、Worker、Channel Adapter、Storage Adapter、Filter、Telemetry、数据库和 IM 平台之间的
关系。
- 一张核心时序图，展示“企业微信用户发消息 → Agent 执行 → Tool 调用 → Session / Memory 写入 → IM 回复”的完整链路。
- 一份数据模型设计，包含核心表结构或 JSON schema。
- 一份数据同步和幂等策略说明。
- 一份多后端适配方案，说明 Redis / SQL / 向量库 / 对象存储分别适合存什么。
- 一份风险清单，列出至少 8 个生产风险及对应缓解措施。 
- 一份基于该设计的github实现的代码 

## 题目难点
- 多租户隔离不是只加一个 tenant_id 字段，还涉及配置、权限、密钥、数据、日志、工具和成本隔离。
- 节点化部署要求 Agent Worker 尽量无状态，但 Agent 又天然依赖 Session、Memory、Summary 和工具上下文，需要设计可靠的
共享状态层。
- IM 通道存在消息乱序、重复投递、响应超时、长度限制和身份映射问题，不能简单等同于 HTTP chat API。
- 不同后端的数据一致性能力不同，Redis、SQL、向量库、对象存储无法用同一种同步策略处理。
- Agent 执行链路包含模型、工具、MCP、知识库、沙箱和外部系统，监控和审计必须跨组件串联。 
- 企业级平台必须考虑灰度、回滚、租户级限流、成本控制和合规审计。 

## 验收标准
1.架构方案必须覆盖多租户、节点化部署、数据同步、多后端支持、IM 接入、治理监控和故障恢复。
2.数据模型必须能表达 tenant、agent、channel binding、session、event、memory、summary、audit log 的关系。
3.必须说明至少两种 IM 通道的接入差异，其中至少包含微信或企业微信。
4.必须说明至少三类后端的数据存储和同步策略，例如 Redis、SQL、向量库或对象存储。
5.必须给出一条完整消息链路的时序说明，包含 trace_id 或 request_id 如何贯穿链路。 
6.必须列出至少 8 个生产风险和缓解措施。 
7.方案需要明确哪些能力可直接复用 tRPC-Agent-Python，哪些需要新增平台层模块。

## 代码目录

```txt
|-- README.md  # 说明文档,包含设计, 安装,使用
|-- build.sh   # 开发的时候,用于构建项目
|-- clean.sh   # 清理当前项目的中间产物 
|-- coverage.sh # 运行单测覆盖率 
|-- data     # 存储服务需要的数据文件夹
|-- docs    # 各模块的说明文档目录 
|-- format.sh # 格式化项目代码风格
|-- lint_flake8.sh # 格式化项目代码风格
|-- start.sh  # 运行脚本可以启动服务 
|-- stop.sh  # 运行脚本可以停止服务 
`-- trpc_service # 源码项目
    |-- _cli.py # cli 可以直接命令行运行
    |-- agent   # agent 的源码
    |-- channels # 对接im 的channel
    |-- config   # 需要的配置 
    |-- log   # 日志代码,可以设置日志文件级别的操作
    |-- metrics # 监控
    |-- skill # 可以运行的skill文件
    |-- tenant # 多租户的代码 
    |-- tool # 需要使用的tool
    |-- version.py # 版本
    |-- web # 提供网页版本页面可以访问服务
    `-- workspace # 工作目录,包含本地,容器等沙箱环境
```

第二阶段新增后端配置管理 API：支持后端登记、租户配置保存、原子发布与配置回滚、管理审计，以及 Worker 按消息固定版本加载。启用数据库管理模式见 [运行说明](docs/后端配置管理运行说明-2026-09-05.md)。

工具执行、六类资源装配、持久化迁移、会话灰度和观测的后续实现及生产边界，见 [第二阶段运行时补全记录](docs/第二阶段运行时补全与边界-2026-09-06.md)。官方最新 SDK 仍缺原生写入 fencing/CAS，生产模式门禁保留。

实际双通道验收采用企业微信智能机器人与飞书企业自建应用机器人，开发、配置和测试边界见 [飞书适配与企微双通道测试](docs/飞书适配与企微双通道测试-2026-09-07.md)。Telegram 协议实现保留；双通道测试不要求 Telegram 账号。
