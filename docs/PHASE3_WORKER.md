# Phase 3 Worker 池

Phase 3 新增内部 Worker Pool，以及适配当前 `nodeskclaw-task` lease API 的
兼容客户端。可以在不启用 lease 轮询的情况下启用 Worker 注册和 heartbeat。
在注入 Phase 4 `RunCommandHandler` 之前，Runtime 执行保持禁用。

## 安全默认值

```env
WORKER_ENABLED=false
WORKER_LEASE_ENABLED=false
```

- `WORKER_ENABLED=false`：不调用 Task Worker 注册、heartbeat 或 lease 接口。
- `WORKER_ENABLED=true` 且 `WORKER_LEASE_ENABLED=false`：只进行注册和
  heartbeat。这是 Phase 3 支持的真实冒烟测试配置。
- `WORKER_LEASE_ENABLED=true`：必须注入 Runtime `RunCommandHandler`；
  否则应用会在启动前的构造阶段失败。
- 应用不会创建表或执行表迁移。Phase 3 复用 `rpa_worker_instances` 和
  `rpa_execution_attempts`。
- Redis 不是 Phase 3 的依赖项。

## 配置

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `WORKER_ID` | `server-worker-001` | Task 和 Engine 共用的稳定 Worker ID |
| `WORKER_TYPE` | `SERVER_WORKER` | `SERVER_WORKER` 或 `LOCAL_AGENT` |
| `WORKER_DEVICE_NAME` | `nodeskclaw-rpa-engine` | 可观测的设备名称 |
| `WORKER_CAPABILITIES` | MANAGED + 截图/下载演示能力 | JSON 字符串数组 |
| `WORKER_TAGS` | `[]` | Engine 内部 JSON 标签数组 |
| `WORKER_MAX_CONCURRENT_RUNS` | `1` | 本地并发槽位数 |
| `WORKER_HEARTBEAT_INTERVAL_SECONDS` | `15` | Task 和 Engine 的 heartbeat 周期 |
| `WORKER_POLL_INTERVAL_SECONDS` | `5` | 启用时的 lease 轮询周期 |
| `WORKER_LEASE_RENEW_INTERVAL_SECONDS` | `20` | lease 续期周期 |
| `WORKER_OFFLINE_THRESHOLD_SECONDS` | `45` | 只读接口判定 heartbeat 过期的阈值 |
| `WORKER_SHUTDOWN_GRACE_SECONDS` | `30` | 优雅 drain 等待时间 |
| `TASK_API_TIMEOUT_SECONDS` | `10` | 单次 Task API 请求超时时间 |

Worker 模式要求 `DATABASE_ENABLED=true`。Worker 必须声明
`PLAYWRIGHT_CDP` 和 `BROWSER_SESSION_MANAGED`；dispatch 前还会校验 Flow
专属能力。公开演示配置还会声明大写的 `SCREENSHOT` 和 `DOWNLOAD`，以匹配
Phase 5 manifest。

## Engine 只读接口

所有请求都必须携带测试环境的 `X-Actor-Id` 请求头。

```http
GET /api/v1/workers?status=ONLINE&capability=PLAYWRIGHT_CDP&limit=50&offset=0
GET /api/v1/workers/{workerId}
```

当已存储的 ONLINE/BUSY heartbeat 超过配置阈值时，响应状态按 `OFFLINE`
计算。Phase 3 不提供 drain/resume 修改接口。

## Task lease 契约

### 2026-07-16 测试服务器 Schema 验证

对测试服务器 OpenAPI 的只读检查确认，`WorkerLeaseResponse` 现已同时公开
原有 dispatch 字段和 Phase 3 不可变执行快照：

```text
taskId
runId
leaseId
workflowBindingId
portalAccountId
rpaFlowId
input
tenantId
workflowTemplateId
workflowCode
rpaEngineType
rpaFlowVersion
credentialRef
config.portalUrl
config.browserSession
leaseExpiresAt
```

renew 响应 Schema 也会返回 `leaseExpiresAt`。文档定义的 Worker Artifact
upload-url 操作为 `POST /worker-api/artifacts/upload-url`，请求字段如下：

```text
worker_id
task_id
run_id
name
mime_type
```

因此，字段结构兼容性检查已经通过。本次检查为只读操作：没有请求 lease，
没有检查专用的真实执行快照，也没有执行 Task 驱动的端到端 Run。不得将其视为
register、heartbeat、lease、renew 或 callback 行为成功的证据。

兼容的 lease payload 结构如下：

```json
{
  "tenantId": "tenant-1",
  "workflowTemplateId": "template-1",
  "workflowCode": "fetch_po",
  "rpaEngineType": "PLAYWRIGHT_CDP",
  "rpaFlowVersion": "1.0.0",
  "credentialRef": "credential-1",
  "config": {
    "portalUrl": "http://127.0.0.1:4600",
    "browserSession": {
      "mode": "MANAGED",
      "headless": true,
      "channel": "chromium",
      "profileRef": null,
      "cdpEndpointRef": null,
      "closePolicy": "ALWAYS"
    }
  },
  "leaseExpiresAt": "2026-07-14T12:00:00Z"
}
```

缺少 version、过期时间或 browser-session 字段会导致契约被拒绝。Engine 会将
`rpaFlowId + rpaFlowVersion + tenantId` 解析为唯一且精确的 active、published
Registry 版本，绝不会用最新版本替代。

当前测试环境 Task OpenAPI 的 Worker 请求体使用 snake_case，
`WorkerLeaseResponse` 使用 camelCase。兼容客户端会保留这一混合传输契约；
Engine 公共 API 响应仍使用 camelCase。

在完成以下所有受控联调门禁前，应保持 `WORKER_LEASE_ENABLED=false`：

1. 用于 Engine 测试的专用 Task binding/run 数据已获批准。
2. `rpaFlowId + rpaFlowVersion + tenantId` 能解析为精确的 active、published
   Registry 版本；Engine 绝不会替换为最新版本。
3. 专用 Mock credential reference、tenant、Portal Account 作用域以及受控的
   `config.portalUrl` 与 lease 快照一致。
4. 真实 lease、renew、event、Artifact upload/metadata 和 finish callback
   全部端到端通过。
5. 上生产前 Task 已持久化并按 `Idempotency-Key` 去重回调，能够结合 `leaseId`
   拒绝旧 attempt 的陈旧 FINISH，且生产 service-account 鉴权已经完成。

## Attempt 与停机行为

- 新接受的 lease 以 `dispatchMode=LEASE` 和 `LEASED` 记录。
- 进入 Handler 时状态变为 `RUNNING`；执行结束时使用终态。
- 重复的 `leaseId` 不会创建或 dispatch 另一个 attempt。
- 同一 `runId` 的新 `leaseId` 会在 PostgreSQL 事务 advisory lock 保护下取得
  下一个 `attemptNo`。
- renew 失败后，只允许 Handler 继续运行至已知 lease 到期时间；随后取消执行
  并记录为 `ABANDONED`。
- Worker 启动时会把同一 `worker_id` 遗留的 `LEASED` / `RUNNING` attempt
  恢复为 `ABANDONED`，并在同一事务中写入对应的 FINISH Outbox；终态 attempt
  会写入 `ended_at`。因此同一 `worker_id` 不得并发启动多个 Engine 实例。
- 优雅停止时先记录 `DRAINING`，等待活动槽位结束，再记录 `OFFLINE`。

当前 Engine 基线中，已创建 attempt 的 EVENT 和 FINISH callback 会持久化到现有
`rpa_callback_outbox`，由后台按至少一次语义重试，并在每次重试中使用稳定的
`Idempotency-Key`。因此 Task 必须持久化并按该键去重；后续还需结合 `leaseId`
拒绝旧 attempt 发出的跨 attempt 陈旧 FINISH。Artifact 上传和 metadata 登记仍为
直接调用。只有在 attempt 创建前被前置校验拒绝时，才使用 direct best-effort
callback 兜底。

## 真实冒烟测试边界

使用专用 ID `server-worker-phase3-smoke`，保持
`WORKER_LEASE_ENABLED=false`，并在获得单独授权后仅验证：

1. Task register 返回 HTTP 200，且 Task envelope 表示成功。
2. Task heartbeat 返回 HTTP 200，且 Task envelope 表示成功。
3. Engine `rpa_worker_instances` 变为 ONLINE，且 heartbeat 持续推进。
4. 关闭时，Engine 内部状态变为 OFFLINE。

Phase 3 冒烟测试期间不得调用真实 Task lease 接口。2026-07-16 的 OpenAPI
验证没有执行此冒烟流程。
