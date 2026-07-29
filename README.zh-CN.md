# NoDeskClaw RPA Engine

简体中文 | [English](README.md)

AutoTask 产品分支 `v0.1` 包含 RPA Engine 组件版本 `0.6.0`。两者是不同的
版本维度：`v0.1` 是产品分支，`0.6.0` 是当前 Engine 包和服务版本。

Engine 当前提供配置管理、结构化日志、存活与就绪检查、PostgreSQL 与
S3 兼容存储基础设施、Flow Registry 与版本化包管理、内部 Worker Pool、
精确版本 Flow 包加载、MANAGED Playwright 浏览器会话、Artifact 记录以及
标准化错误映射。

Engine 负责 Flow Registry 元数据和 Flow Package；`nodeskclaw-task` 负责
WorkflowBinding、业务任务、Run、事件、Artifact 元数据和 HumanAction。
Engine 没有对外提供直接运行或调试 Flow 的 HTTP API，业务运行由 Task 创建
任务和 Run 后，通过 Worker lease 驱动。

从 `0.6.0` 开始，Flow 成功时可以从 `flow.py:run(ctx)` 返回 JSON object。
Engine 会执行严格 JSON、敏感字段和大小校验，只在 `SUCCESS` finish 中通过
Callback Outbox 传递该输出；默认上限由 `RUNTIME_OUTPUT_MAX_BYTES=1048576`
控制。返回 `None` 的历史 Flow 保持兼容。输出校验失败不会重跑已经完成业务操作
的 Flow。

2026-07-16 对测试服务器 OpenAPI 进行的只读核对已确认：Task 提供了 Engine
所需的 lease/renew 契约字段，以及 Worker Artifact 上传地址接口。该核对只
验证接口结构，不代表真实 Task 驱动链路已经联调成功。在专用测试数据、精确
发布的 Registry 版本、受限测试凭据与 Portal 作用域，以及完整回调链路准备并完成
端到端验证前，真实 lease 轮询仍应保持关闭。

Flow `1.0.0` 是确定性的本地 Mock SRM 基线，覆盖 `SUCCESS`、`FAILED` 和
`WAITING_HUMAN` 三种结果。Flow `1.1.0` 使用配置传入的供应商门户，执行登录、
订单查询、进入详情页和下载 XLSX。当前 `WAITING_HUMAN` 使用 Type-A 模式：
证据记录完成后关闭服务器浏览器，人工处理后不恢复原有 Playwright 会话。

## 环境要求

- Python `>=3.12,<3.13`
- 下列命令使用 Windows PowerShell
- Linux 常驻部署请使用 `docs/LINUX_DEPLOYMENT.md` 中的 Python 3.12、Chromium 和
  systemd 配置，不要直接套用 Windows 路径。
- 使用“禁用外部依赖”配置时，不要求本机运行 PostgreSQL 或 MinIO

## 本地安装与启动

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m nodeskclaw_rpa_engine
```

如果 Python 安装在其他位置，第一条命令可以改为：

```powershell
py -3.12 -m venv .venv
```

服务默认监听 `127.0.0.1:4610`：

```text
GET http://127.0.0.1:4610/health/live
GET http://127.0.0.1:4610/health/ready
GET http://127.0.0.1:4610/docs
```

其他环境的服务地址必须通过环境变量或部署配置提供，不得把真实部署地址提交到
本仓库。部署时 `RPA_ENGINE_PUBLIC_BASE_URL` 必须填写其他组件可以访问的
Engine 根地址，因为 Registry 返回的 Flow `packageUri` 会使用该地址。

## 文档导航

- Linux 非 root 用户、Runtime 目录、Playwright Chromium 和 systemd 部署：
  [`docs/LINUX_DEPLOYMENT.md`](docs/LINUX_DEPLOYMENT.md)
- Flow Registry 请求、Flow 包上传、版本发布和绑定校验示例：
  [`docs/PHASE2_API.md`](docs/PHASE2_API.md)
- Worker 配置、Task lease 契约和真实冒烟测试边界：
  [`docs/PHASE3_WORKER.md`](docs/PHASE3_WORKER.md)
- Runtime、浏览器会话、Artifact 和错误处理行为：
  [`docs/PHASE4_RUNTIME.md`](docs/PHASE4_RUNTIME.md)
- 版本化的本地 Mock Flow、真实供应商门户 Flow 和浏览器验证：
  [`docs/PHASE5_MOCK_SRM.md`](docs/PHASE5_MOCK_SRM.md)
- 测试机部署、Flow 发布、Task 数据准备和端到端验收顺序：
  [`docs/PHASE5_TEST_SERVER_HANDOFF.md`](docs/PHASE5_TEST_SERVER_HANDOFF.md)

安装 Chrome 后，可以在本机运行全部三个 Phase 5 场景：

```powershell
.\.venv\Scripts\python.exe scripts\run_phase5_demo.py `
  --start-mock-srm `
  --channel chrome
```

也可以安装并使用 Playwright Chromium：

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe scripts\run_phase5_demo.py `
  --start-mock-srm `
  --channel chromium
```

该本地演示使用内存替身，不访问 PostgreSQL、MinIO 或 Task API。

构建当前供应商门户 Flow（默认版本也是 `1.1.0`）：

```powershell
.\.venv\Scripts\python.exe scripts\build_phase5_package.py --version 1.1.0
```

真实门户地址和凭据只能放在本地环境变量或 Task 部署配置中：

```powershell
$env:SUPPLIER_PORTAL_URL = "<供应商门户地址>"
$env:SUPPLIER_PORTAL_USERNAME = "<用户名>"
$env:SUPPLIER_PORTAL_PASSWORD = "<密码>"
.\.venv\Scripts\python.exe scripts\run_supplier_portal_demo.py `
  --po-no POJS2606030010 `
  --channel chrome
```

当前门户详情页下载的是固定 XLSX，不是 PDF，也不是按订单生成的独立文件。
冒烟测试会记录请求订单号，但不会声称该固定文件与订单一一对应。

## 质量检查

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -m pip check
```

## 外部依赖与安全策略

- PostgreSQL 默认关闭。启用后，连接 URL 必须使用
  `postgresql+asyncpg`，每个 Engine 连接单独设置
  `rpa_engine,public` search path。
- 应用启动时不会调用 `create_all`，不会自动运行 Alembic、创建 Schema、
  执行迁移或写入种子数据。
- MinIO/S3 默认关闭。启用后，readiness 会检查配置的 bucket；应用启动时
  不会创建 bucket。
- Flow Registry 依赖 PostgreSQL 和对象存储；Worker 依赖 PostgreSQL；Runtime
  依赖对象存储。启用 Worker 或 Runtime 后，Task API 会成为 readiness 的必要
  依赖，不可用时 `/health/ready` 返回 503。
- 数据库密码、MinIO Key、服务账号秘密和门户凭据只能通过本地 `.env`、部署
  环境变量或受管秘密注入，不得提交到 Git，也不得写入日志。
- 当前 `TASK_AUTH_MODE=none` 仅用于测试环境兼容。生产环境仍需实现 Worker
  服务账号 Token exchange。
- Flow API 和 Worker 只读观察接口使用 `X-Actor-Id`；TENANT Flow 还必须携带
  `X-Tenant-Id`。这些 Header 只是可信测试环境中的调用者、租户和审计上下文，
  不是生产鉴权机制。
- Worker Pool 和 lease 轮询默认关闭。可以只开启注册和心跳；lease 轮询需要
  Runtime Handler，并且只能在专用联调数据获得明确批准后开启。
- 凭据解析默认关闭。`mock_env` 只允许用于 development/test，并且必须严格
  限制到一个 credential reference、一个 tenant 和一个 Portal account。它只用于
  受控的 Phase 5 演示，不能替代生产凭据服务适配器。
- 部署地址通过环境变量提供。本仓库不得提交内部地址、`.env`、JWT、真实凭据、
  Flow ZIP、浏览器 Trace、截图、下载文件或 Runtime Artifact。

## Flow Registry

Flow 包使用 ZIP 格式，根目录必须至少包含：

```text
manifest.json
flow.py
```

`selectors.json` 是可选文件。存在时必须是 UTF-8 JSON object；它会在 Runtime
加载阶段解析，因此上传静态校验通过并不代表 selector 文件或页面行为一定可用。

上传接口：

```http
POST /api/v1/flows/packages
Content-Type: multipart/form-data
X-Actor-Id: <actor>
```

主要约束：

- 压缩包最大 50 MiB
- 解压后最大 200 MiB
- 最多 500 个非目录文件
- 最大压缩比 100 倍
- `manifest.json` 最大 1 MiB
- `flow.py` 必须存在顶层 `async def run(ctx)`
- 当前 `engineType` 只支持 `PLAYWRIGHT_CDP`
- 当前 `entrypoint` 只支持 `flow.py:run`
- 同一 scope/tenant 下同一 Flow 的同一版本不可覆盖，内容变化必须升 SemVer
- 拒绝绝对路径、`..`、反斜杠路径、符号链接和加密 ZIP 条目
- 拒绝 `.env`、`credentials.json` 和 `secrets.json`
- 校验阶段使用 AST 检查，不 import 或执行 `flow.py`
- 包内不得包含凭据、数据库连接、部署地址或环境专用配置

常用 Flow Registry 接口包括：

```text
GET  /api/v1/flows
POST /api/v1/flows/packages
GET  /api/v1/flows/{rpaFlowId}
GET  /api/v1/flows/{rpaFlowId}/versions
POST /api/v1/flows/{rpaFlowId}/disable
POST /api/v1/flows/{rpaFlowId}/rollback

GET  /api/v1/flow-versions/{id}
POST /api/v1/flow-versions/{id}/validate
POST /api/v1/flow-versions/{id}/publish
POST /api/v1/flow-versions/{id}/deprecate
POST /api/v1/flow-versions/{id}/disable
POST /api/v1/flow-versions/validate-binding
GET  /api/v1/flow-versions/{id}/package
```

Engine 根据 `rpaFlowId + rpaFlowVersion + tenantId` 解析唯一、精确且已发布的
Registry 版本，禁止用“最新版本”替代绑定指定的版本。

Registry 的回滚操作只调整 Engine 中的 Flow 版本发布状态，不会修改
`nodeskclaw-task` 已存在的 WorkflowBinding。业务绑定需要由 Task 服务显式
切换到目标旧版本。

## Worker 与 Runtime

Worker 和 Runtime 的默认安全配置：

```env
WORKER_ENABLED=false
WORKER_LEASE_ENABLED=false
RUNTIME_ENABLED=false
CREDENTIAL_RESOLVER_MODE=disabled
```

三种 Worker 状态的含义：

- `WORKER_ENABLED=false`：不向 Task 注册，不发送心跳，也不调用 lease。
- `WORKER_ENABLED=true` 且 `WORKER_LEASE_ENABLED=false`：只注册和发送心跳，
  适用于联调前的安全冒烟测试。
- `WORKER_LEASE_ENABLED=true`：必须已经启用并注入 Runtime Handler，否则应用
  在启动前拒绝该配置。

只读 Worker 观察接口：

```text
GET /api/v1/workers
GET /api/v1/workers/{workerId}
```

演示 Worker 的默认能力标识为：

```json
[
  "PLAYWRIGHT_CDP",
  "BROWSER_SESSION_MANAGED",
  "SCREENSHOT",
  "DOWNLOAD"
]
```

Runtime 目前仅支持 `browserSession.mode=MANAGED`。支持的浏览器 channel：

```text
chromium
chrome
msedge
```

MANAGED 命令必须使用 `ALWAYS` 或 `CLOSE_ON_FINISH` 关闭策略，并且
`profileRef`、`cdpEndpointRef` 必须为 null。Flow 只接收 Engine 管理的
`ctx.page`，不得自行启动浏览器或连接 CDP。

Worker Artifact 链路为：

```text
Flow
  -> ArtifactRecorder
  -> 本地 run 文件
  -> Task POST /worker-api/artifacts/upload-url
  -> 对象存储签名 PUT
  -> Task run Artifact 元数据回调
```

上传地址请求包含：

```text
worker_id
task_id
run_id
name
mime_type
```

签名 URL 不会持久化。结构化日志会对常见签名参数和敏感字段进行脱敏。
当 `RUNTIME_CLEANUP_ON_FINISH=true` 时，浏览器清理完成后会删除本地 run 文件。

## 错误与运行结果

| 异常类型 | Runtime 结果 |
| --- | --- |
| `RpaRetryableError`、Playwright/Python timeout | 在上限内重试，耗尽后 `FAILED` |
| `RpaBusinessError` | `FAILED` |
| `RpaHumanRequiredError` | `WAITING_HUMAN` |
| `RpaFatalError` | `FAILED` |
| 未识别异常 | `FAILED / FLOW_UNHANDLED_ERROR` |

终态失败和 `WAITING_HUMAN` 会尽力记录失败截图。Flow 的日志和事件内容会经过
统一的敏感字段脱敏处理。当前重试粒度是整个 Flow，可能重复外部副作用，因此
Flow 必须自行保证业务操作幂等。

## Phase 5 版本化演示 Flow

Mock SRM 是独立的本地 FastAPI 服务。启动 Engine 不会自动启动或暴露 Mock
Portal：

```powershell
.\.venv\Scripts\python.exe -m nodeskclaw_rpa_engine.mock_srm
```

默认地址：

```text
GET http://127.0.0.1:4600/health/live
GET http://127.0.0.1:4600/
```

`1.0.0` 保留为本地确定性 Mock，三个固定场景为：

| `po_no` | Runtime 结果 | 错误码 |
| --- | --- | --- |
| `PO-20260708-001` | `SUCCESS` | 无 |
| `PO-NOT-FOUND` | `FAILED` | `BUSINESS_NOT_FOUND` |
| `PO-MANUAL-001` | `WAITING_HUMAN` | `HUMAN_VERIFICATION_REQUIRED` |

`1.1.0` 使用 `ctx.portal_url` 指向部署时配置的供应商门户，并使用 Runtime
解析的凭据完成登录、查询订单、进入详情页和确认下载。默认验收订单为
`POJS2606030010`；实际任务仍通过 `po_no` 动态传入。未知验证码返回
`WAITING_HUMAN / HUMAN_VERIFICATION_REQUIRED`。

构建发布用 Flow ZIP。构建器默认选择 `1.1.0`，也可以显式构建保留的
`1.0.0`：

```powershell
.\.venv\Scripts\python.exe scripts\build_phase5_package.py --version 1.1.0
.\.venv\Scripts\python.exe scripts\build_phase5_package.py --version 1.0.0
```

输出：

```text
dist/rpa_flow_mock_srm_fetch_po-1.1.0.zip
dist/rpa_flow_mock_srm_fetch_po-1.0.0.zip
```

每个 ZIP 只包含 `manifest.json`、`selectors.json` 和 `flow.py`，不包含凭据、
内网地址或环境配置。`dist/` 已被 Git 忽略，构建产物不作为源码提交。

`1.1.0` 当前验收的是门户返回的固定 `order-20260709122735.xlsx`，不是 PDF。
WorkflowBinding 必须绑定发布后返回的精确 `1.1.0` Flow Version UUID 和
checksum，禁止使用 `1.0.0` 快照或“最新版本”回退。

## 当前联调边界

- 2026-07-16 的只读 Task OpenAPI 核对确认：
  `WorkerLeaseResponse` 已包含 Engine 需要的不可变执行快照，renew 返回
  `leaseExpiresAt`，Worker Artifact 上传地址为
  `POST /worker-api/artifacts/upload-url`。这只是 Schema 验证，不是成功的
  register、heartbeat、lease、renew 或回调运行记录。
- Task 调度当前使用 HTTP lease/renew 兼容层。生产 Queue 的 ack、可见性超时、
  重试和死信行为仍待实现。
- 对已创建 execution attempt 的运行，EVENT 和 FINISH 会先持久化到现有
  `rpa_callback_outbox`，再由后台按至少一次语义重试投递，并携带稳定的
  `Idempotency-Key`。Artifact 上传和 metadata 登记仍采用直接调用；只有在
  attempt 创建前被前置校验拒绝时，才使用 direct best-effort 回调兜底。
- Task 必须持久化并按 `Idempotency-Key` 去重回调；后续还需结合 `leaseId`
  拒绝旧 attempt 发出的跨 attempt 陈旧 FINISH。
- `TASK_AUTH_MODE=none` 和测试 actor header 都不是生产鉴权，Worker
  服务账号鉴权仍是生产发布前置条件。
- Python Flow 当前运行在 Engine 进程中。静态策略只能降低误用风险，不能提供
  操作系统级隔离；生产环境仍需决定是否采用独立进程或容器隔离。
- 当前 Task lease 不能作为生产级能力调度机制使用。开启 lease 前必须确保只有
  专用且获批的测试 Run 处于可领取状态。
- `config.portalUrl` 目前只允许用于受控测试 Runtime。生产 Portal 地址解析
  仍需由受治理的 Task/Portal 配置适配器提供。
- Type-A `WAITING_HUMAN` 不支持人工处理后恢复原 Playwright 浏览器会话。
- 在专用 Binding 与 Run 获批、精确 Registry 版本及测试作用域准备完成，并且
  lease、renew、event、Artifact upload/metadata 和 finish 完成真实端到端验证
  前，必须保持 `WORKER_LEASE_ENABLED=false`。

## 数据库控制点

当前部署目标为 PostgreSQL 数据库 `nodeskclaw_task`、Schema `rpa_engine`。
ORM 基线定义九张 Engine 自有表，共 142 个字段、七个内部外键、四个触发器函数
和十二个触发器。

以下文件是由管理员控制执行的数据库基线产物：

- [`sql/0002_rpa_engine_initial_schema.sql`](sql/0002_rpa_engine_initial_schema.sql)
- Alembic revision：`20260713_0001`

对于已有 Schema，必须先检查结构漂移，再由管理员决定是否执行 stamp。经过批准
的空 Schema 可以使用基线 upgrade。应用启动和测试永远不会自动 stamp、迁移、
执行 DDL 或写入种子数据。
