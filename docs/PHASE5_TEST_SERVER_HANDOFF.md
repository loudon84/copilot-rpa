# Phase 5 测试机部署与联调清单

本文用于将 Engine `v0.1` 部署到测试机，并完成 Task 驱动的 Phase 5
端到端联调。所有地址和标识都使用环境变量或 Postman 变量；不得把真实地址、
凭据、JWT 或 `.env` 提交到仓库。

## 0. 变量约定与安全边界

在部署环境或 Postman Environment 中准备以下变量：

| 变量 | 含义 |
| --- | --- |
| `ENGINE_BASE_URL` | Engine 根地址，不含 `/api/v1` |
| `TASK_BASE_URL` | Task AutoTask 根地址，包含 `/api/v1/autotask` |
| `MOCK_SRM_URL` | Engine 所在机器的浏览器能够访问的 Mock SRM 地址 |
| `ACCESS_TOKEN` | 调用 Task 管理接口的用户 JWT，仅存本地 Postman Environment |
| `TENANT_ID` | 本次专用测试租户 ID |
| `FLOW_VERSION_ID` | Engine 上传或查询得到的 Flow Version UUID |
| `FLOW_CHECKSUM` | Engine 返回的 `sha256:` checksum |
| `PORTAL_ACCOUNT_ID` | Task 创建 Portal 后返回的 ID |
| `WORKFLOW_TEMPLATE_ID` | Task 创建模板后返回的 ID |
| `WORKFLOW_BINDING_ID` | Task 创建绑定后返回的 ID |
| `TASK_ID` / `RUN_ID` | 每个演示场景对应的 Task/Run ID |

- 本清单只允许使用专用测试数据。不要直接修改 Task 或 Engine 表来推进状态。
- 当前 Engine Worker 只支持测试环境的 `TASK_AUTH_MODE=none`；生产服务账号
  Token exchange 尚未实现。
- `.env`、JWT、数据库 URL、MinIO Key、Mock 密码和 Artifact 签名 URL 不得进入
  Git、聊天、截图或日志。
- 测试机当前 Task 契约是混合命名：Portal 创建使用 camelCase；模板、绑定和
  任务创建使用 snake_case。旧 Postman Collection 中后面三类请求的 camelCase
  顶层字段已过期。绑定 `config` 内部仍使用 Runtime 契约规定的 `portalUrl` 和
  `browserSession` camelCase 字段。

## 1. 部署前检查

1. 从公开仓库拉取经确认的 `v0.1` 提交，并记录实际 SHA：

   ```powershell
   git fetch origin
   git switch v0.1
   git pull --ff-only origin v0.1
   git status --short
   git rev-parse HEAD
   ```

   工作树必须干净；不要从开发机复制 `.env`、`dist/`、`runtime-cache/` 或浏览器
   Artifact。

2. 测试机准备 Python 3.12、可运行的 Chromium/Chrome、到 PostgreSQL、MinIO、
   Task API 及 Artifact 签名上传地址的网络连通性。安装 Engine 和浏览器：

   ```powershell
   py -3.12 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
   .\.venv\Scripts\python.exe -m playwright install chromium
   .\.venv\Scripts\python.exe -m pip check
   .\.venv\Scripts\python.exe -m pytest
   Copy-Item .env.example .env
   git check-ignore .env
   ```

3. 数据库固定为 `nodeskclaw_task`、Schema 固定为 `rpa_engine`。Engine 启动不会
   建表、执行 Alembic、迁移或种子：

   - 只读核对九张 Engine 表与 revision `20260713_0001` 完全一致。
   - 表已存在但 `rpa_engine.alembic_version` 缺失时，先完成结构差异审查；只有
     DBA 明确授权后才可执行 `alembic stamp 20260713_0001`。
   - 表缺失或结构有漂移时停止部署并交 DBA 处理；本次不得自行运行
     `alembic upgrade`、DDL、迁移或种子。

4. 确认 MinIO bucket 已由管理员创建。Engine 启动不会创建 bucket。

## 2. 第一阶段启动：只注册和心跳

在测试机本地 `.env` 填入真实值，秘密字段只保存在该文件。第一阶段至少使用：

```env
APP_ENV=test
APP_HOST=0.0.0.0
APP_PORT=4610
RPA_ENGINE_PUBLIC_BASE_URL=<ENGINE_BASE_URL>

DATABASE_ENABLED=true
DATABASE_URL=<postgresql+asyncpg URL，目标库必须为 nodeskclaw_task>
DATABASE_SCHEMA=rpa_engine

MINIO_ENABLED=true
MINIO_ENDPOINT_URL=<S3 compatible endpoint>
MINIO_ACCESS_KEY=<secret>
MINIO_SECRET_KEY=<secret>
MINIO_BUCKET=rpa-flow-packages

TASK_API_BASE_URL=<TASK_BASE_URL>
TASK_AUTH_MODE=none

WORKER_ENABLED=true
WORKER_LEASE_ENABLED=false
WORKER_ID=server-worker-phase5-integration
WORKER_MAX_CONCURRENT_RUNS=1
WORKER_CAPABILITIES=["PLAYWRIGHT_CDP","BROWSER_SESSION_MANAGED","SCREENSHOT","DOWNLOAD"]

RUNTIME_ENABLED=false
CREDENTIAL_RESOLVER_MODE=disabled
```

启动 Engine：

```powershell
.\.venv\Scripts\python.exe -m nodeskclaw_rpa_engine
```

核对：

- `GET ${ENGINE_BASE_URL}/health/live` 返回 200。
- `GET ${ENGINE_BASE_URL}/health/ready` 返回 200，`database`、`objectStorage`、
  `taskApi` 均为可用状态，且不返回任何连接秘密。
- Task 能看到 `server-worker-phase5-integration` 注册并持续心跳。
- Engine `GET /api/v1/workers/server-worker-phase5-integration`（带
  `X-Actor-Id`）显示 `ONLINE` 且心跳时间推进。
- 保持 `WORKER_LEASE_ENABLED=false`；此时不得调用真实 lease。

## 3. 构建、上传、校验并发布 Flow

### 3.1 构建包

在仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe scripts\build_phase5_package.py
```

输出为：

```text
dist/rpa_flow_mock_srm_fetch_po-1.0.0.zip
```

构建器会执行与上传接口相同的包策略校验并打印 SHA-256。ZIP 不得包含凭据、
`.env` 或环境地址。

### 3.2 上传包

在 Postman 调用：

```http
POST ${ENGINE_BASE_URL}/api/v1/flows/packages
X-Actor-Id: phase5-test-deployer
Content-Type: multipart/form-data
```

Body 选择 `form-data`：

| Key | 类型 | Value |
| --- | --- | --- |
| `package` | File | `dist/rpa_flow_mock_srm_fetch_po-1.0.0.zip` |
| `scope` | Text | `GLOBAL` |
| `description` | Text | `Phase 5 Mock SRM integration flow` |
| `labels` | Text | `["srm","phase5","demo"]` |

预期 HTTP 201，并检查：

- `flow.rpaFlowId = rpa_flow_mock_srm_fetch_po`
- `version.version = 1.0.0`
- `version.status = DRAFT`
- `validation.status = PASSED`
- 保存 `version.rpaFlowVersionId` 为 `FLOW_VERSION_ID`
- 保存 `version.packageChecksum` 为 `FLOW_CHECKSUM`

同一 Flow 的同一版本不可覆盖。若返回 `FLOW_VERSION_EXISTS`，调用
`GET ${ENGINE_BASE_URL}/api/v1/flows/rpa_flow_mock_srm_fetch_po/versions?scope=GLOBAL`
定位现有 `1.0.0`：若已 `PUBLISHED` 则复用其 ID/checksum；若为 `DRAFT` 则继续
校验发布。若现有版本来源或内容无法确认，停止联调，不要删除、覆盖或伪造版本。

### 3.3 重新校验、发布、绑定预检

依次调用以下 Engine 接口，均带 `X-Actor-Id: phase5-test-deployer`：

```http
POST ${ENGINE_BASE_URL}/api/v1/flow-versions/${FLOW_VERSION_ID}/validate
```

预期 HTTP 200 且 `status = PASSED`，`errors` 为空。

```http
POST ${ENGINE_BASE_URL}/api/v1/flow-versions/${FLOW_VERSION_ID}/publish
Content-Type: application/json

{"reason":"Phase 5 controlled test-server integration"}
```

预期 HTTP 200，`status = PUBLISHED`，返回的 ID 与 checksum 分别等于
`FLOW_VERSION_ID`、`FLOW_CHECKSUM`。

```http
POST ${ENGINE_BASE_URL}/api/v1/flow-versions/validate-binding
Content-Type: application/json

{
  "rpaFlowId": "rpa_flow_mock_srm_fetch_po",
  "rpaFlowVersion": "1.0.0",
  "workflowCode": "srm_fetch_po"
}
```

预期 `valid = true`、`reasonCode = null`、`version.status = PUBLISHED`，且
`version.rpaFlowVersionId` 和 `version.packageChecksum` 精确等于前面记录的值。

## 4. Task 配置、队列隔离与业务数据

1. Task 测试服务配置以下值并重启：

   ```env
   RPA_ENGINE_BASE_URL=<ENGINE_BASE_URL>
   RPA_ENGINE_VALIDATE_BINDING=true
   ```

   从 Task 服务器访问 `${ENGINE_BASE_URL}/health/ready` 必须返回 200。不得关闭
   binding validation。

2. 使用 Task UI 或已授权 API 核对所有 `QUEUED` run。当前 lease 不按 Worker
   capability 过滤，因此开启 lease 前必须取消或隔离所有非本次联调 run。取消接口：

   ```http
   POST ${TASK_BASE_URL}/tasks/{unrelated_task_id}/cancel
   Authorization: Bearer ${ACCESS_TOKEN}
   ```

   不确定是否仍有其他 `QUEUED` run 时，不得开启 lease；不要通过 SQL 改状态。

3. 以下 Task 管理接口都带：

   ```http
   Authorization: Bearer ${ACCESS_TOKEN}
   Content-Type: application/json
   ```

### 4.1 创建 Portal

```http
POST ${TASK_BASE_URL}/portal-accounts

{
  "entityType": "CUSTOMER",
  "erpEntityCode": "MOCK-SRM-DEMO",
  "erpEntityName": "Mock SRM Demo",
  "portalName": "Mock SRM",
  "portalUrl": "<MOCK_SRM_URL>",
  "loginAccount": "mock-user",
  "credentialRef": "mock-srm-phase5-integration",
  "clientOpenMode": "webcontents",
  "clientSessionPartition": "persist:mock-srm-phase5",
  "status": "ENABLED"
}
```

保存 `data.id` 为 `PORTAL_ACCOUNT_ID`。`credentialRef` 只是引用，不得在 Task
输入中放用户名或密码。

### 4.2 创建并启用模板

```http
POST ${TASK_BASE_URL}/workflow-templates

{
  "name": "Mock SRM 拉取 PO",
  "code": "srm_fetch_po",
  "description": "Phase 5 integration demo",
  "entity_type": "CUSTOMER",
  "category": "procurement",
  "status": "DRAFT",
  "version": "1.0.0",
  "input_schema": [
    {"name":"po_no","type":"string","required":true}
  ],
  "business_steps": []
}
```

保存 `data.id` 为 `WORKFLOW_TEMPLATE_ID`，再调用：

```http
POST ${TASK_BASE_URL}/workflow-templates/${WORKFLOW_TEMPLATE_ID}/enable
```

### 4.3 创建精确绑定

```http
POST ${TASK_BASE_URL}/workflow-bindings

{
  "portal_account_id": "<PORTAL_ACCOUNT_ID>",
  "workflow_template_id": "<WORKFLOW_TEMPLATE_ID>",
  "workflow_template_version": "1.0.0",
  "rpa_engine_type": "PLAYWRIGHT_CDP",
  "rpa_flow_id": "rpa_flow_mock_srm_fetch_po",
  "rpa_flow_version": "1.0.0",
  "status": "ENABLED",
  "config": {
    "portalUrl": "<MOCK_SRM_URL>",
    "browserSession": {
      "mode": "MANAGED",
      "headless": true,
      "channel": "chromium",
      "profileRef": null,
      "cdpEndpointRef": null,
      "closePolicy": "CLOSE_ON_FINISH"
    }
  }
}
```

保存 `data.id` 为 `WORKFLOW_BINDING_ID`，并强制检查：

- `status = ENABLED`
- `rpaFlowVersionId = FLOW_VERSION_ID`
- `flowChecksumSnapshot = FLOW_CHECKSUM`

任何 `seed-version-*`、空值或全零 checksum 都表示绑定无效，必须停止联调并修复
Task 到 Engine 的 validation 调用；不得继续创建运行任务。

## 5. 第二阶段启动：Runtime 与 lease

1. 启动独立 Mock SRM 进程：

   ```powershell
   .\.venv\Scripts\python.exe -m nodeskclaw_rpa_engine.mock_srm
   ```

   `GET ${MOCK_SRM_URL}/health/live` 必须返回 200。`MOCK_SRM_URL` 必须与 Portal、
   Binding 和最终 lease snapshot 完全一致，并且由 Engine 启动的浏览器可访问。

2. 优雅停止第一阶段 Engine，确认 Worker 先进入 `DRAINING`、最终为 `OFFLINE`。
   在本地 `.env` 补齐并修改：

   ```env
   RUNTIME_ENABLED=true
   WORKER_LEASE_ENABLED=true
   WORKER_LEASE_RENEW_INTERVAL_SECONDS=2

   CREDENTIAL_RESOLVER_MODE=mock_env
   MOCK_SRM_CREDENTIAL_REF=mock-srm-phase5-integration
   MOCK_SRM_USERNAME=<non-empty synthetic user>
   MOCK_SRM_PASSWORD=<non-empty synthetic password>
   MOCK_SRM_ALLOWED_TENANT_ID=<TENANT_ID>
   MOCK_SRM_ALLOWED_PORTAL_ACCOUNT_ID=<PORTAL_ACCOUNT_ID>
   ```

   `mock_env` 只允许 `APP_ENV=development|test`，且租户、Portal 与
   `credentialRef` 必须与 lease 精确匹配。2 秒 renew 间隔仅用于本次联调观察，
   完成后恢复默认值。

3. 再次确认没有任何 `QUEUED` run，然后启动 Engine。先检查 ready=200、Worker
   ONLINE、Mock SRM live=200；此时尚未创建/启动场景任务，不应领取任何任务。

## 6. 按顺序创建并运行三个场景

每次只创建并启动一条，必须等当前 run 到达终态后才能进行下一条。通用请求：

```http
POST ${TASK_BASE_URL}/tasks
Authorization: Bearer ${ACCESS_TOKEN}
Content-Type: application/json

{
  "title": "<scenario title>",
  "task_type": "srm_fetch_po",
  "portal_account_id": "<PORTAL_ACCOUNT_ID>",
  "workflow_binding_id": "<WORKFLOW_BINDING_ID>",
  "entity_type": "CUSTOMER",
  "erp_entity_code": "MOCK-SRM-DEMO",
  "erp_entity_name": "Mock SRM Demo",
  "priority": "NORMAL",
  "input": {"po_no":"<scenario po_no>"}
}
```

保存 `data.id` 为本场景 `TASK_ID`，再调用：

```http
POST ${TASK_BASE_URL}/tasks/${TASK_ID}/start
Authorization: Bearer ${ACCESS_TOKEN}
```

按此固定顺序执行：

| 顺序 | title | `po_no` | 期望终态 / 错误码 |
| --- | --- | --- | --- |
| 1 | `Phase 5 SUCCESS` | `PO-20260708-001` | `SUCCESS` / 无 |
| 2 | `Phase 5 FAILED` | `PO-NOT-FOUND` | `FAILED` / `BUSINESS_NOT_FOUND` |
| 3 | `Phase 5 WAITING_HUMAN` | `PO-MANUAL-001` | `WAITING_HUMAN` / `HUMAN_VERIFICATION_REQUIRED` |

每次记录 Task ID、Run ID、Lease ID、Worker ID 和终态；不记录 JWT、签名 URL、
密码或浏览器输入。WAITING_HUMAN 是 Type-A：证据上传后服务器浏览器关闭，原会话
不会在人工处理后恢复。

## 7. 每条 run 的端到端验收

通过 Task 日志/管理界面及 Engine 脱敏日志确认 Engine 自动完成以下链路；不要用
Postman 手工代替 Worker 回调：

1. `POST /worker-api/tasks/lease` 返回且仅返回当前专用 run，快照包含精确的
   `tenantId`、模板/绑定/Portal ID、Flow ID/version、`credentialRef`、
   `config.portalUrl`、完整 MANAGED `browserSession` 和带时区的
   `leaseExpiresAt`。
2. `POST /worker-api/tasks/{taskId}/lease/renew` 成功，并返回向后推进的
   `leaseExpiresAt`。
3. `POST /worker-api/runs/{runId}/events` 至少记录 run started 及 Flow 事件。
4. Artifact 链路成功：
   `POST /worker-api/artifacts/upload-url`（请求必须含 `worker_id`、`task_id`、
   `run_id`、`name`、`mime_type`）→ 签名 URL PUT →
   `POST /worker-api/runs/{runId}/artifacts` 元数据登记。
5. `POST /worker-api/runs/{runId}/finish` 与预期终态/错误码一致，且 Task、Run、
   Engine execution attempt 三方终态一致。
6. SUCCESS 至少有结果截图和合同 PDF；FAILED、WAITING_HUMAN 至少有业务/人工
   证据截图、Runtime failure screenshot 和 Trace。Artifact 可由授权接口下载且
   checksum/大小非零。

任一环节失败时立即保持后续场景未排队，记录非敏感的 Task/Run/Lease ID、HTTP
状态、错误码和 Engine 时间戳后排查。当前 event、Artifact metadata 和 finish 是
直接回调，可靠 Outbox 尚未闭环；“Engine 已完成但 Task 未收到 finish”不能视为
通过。

## 8. 停机与收尾

1. 确认没有运行中任务或待领取的专用 `QUEUED` run。
2. 优雅停止 Engine，确认 Worker `DRAINING -> OFFLINE`；再停止 Mock SRM。
3. 将 `.env` 中 `WORKER_LEASE_ENABLED=false`，并把
   `WORKER_LEASE_RENEW_INTERVAL_SECONDS` 恢复为默认值后再部署常驻实例。
4. 仅汇报提交 SHA、Flow Version ID/checksum、Portal/Template/Binding ID、三组
   Task/Run ID、回调结果和非敏感错误。不要提交 `.env`、Flow ZIP、Artifact、
   Trace、截图、下载文件或 runtime cache。
5. 当前生产风险仍包括：lease 不按能力过滤、直接回调无可靠 Outbox、Worker
   生产鉴权未实现、Python Flow 未做进程/容器隔离，以及 Type-A
   WAITING_HUMAN 不支持原浏览器会话续跑。
