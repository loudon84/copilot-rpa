# Phase 4 Runtime 生产基线

Phase 4 实现 Worker Pool 使用的内部 `RunCommandHandler`。本阶段不新增直接运行或
调试 Flow 的 HTTP 接口，默认也不启用真实 lease 轮询。

## 已交付模块

- `FlowLoader`：下载 Registry 中的精确版本包对象、校验 SHA-256、重新执行包校验、
  原子化解压，并按 `rpaFlowId/version/checksum` 缓存。
- `RunContext`：注入输入、凭据、选择器和安全 Runtime 配置的不可变副本，以及托管的
  Page、Artifact Recorder、日志和事件。
- `ManagedBrowserSessionManager`：统一管理 Playwright、Chromium/Chrome/Edge、
  BrowserContext、Page、下载目录、Trace 和确定性资源清理。
- `ArtifactRecorder`：在 Run 目录下记录截图、下载文件、Trace 和日志；检查路径与大小并
  计算文件哈希，随后调用 Task `/worker-api/artifacts/upload-url`、签名 PUT 和 Run
  Artifact 元数据回调。
- `ErrorHandler`：将可重试、业务、需要人工处理、致命、超时及未知错误映射为重试、
  `FAILED` 或 `WAITING_HUMAN`。
- `RpaRuntime`：组合包加载、凭据解析、浏览器、上下文、重试、失败截图、Trace、事件、
  Artifact 和终态 `RunResult`。

Worker Pool 继续负责执行尝试状态和 Task 的 `finish`；Runtime 负责执行 Flow，并返回
`SUCCESS`、`FAILED` 或 `WAITING_HUMAN`。

## 配置

```env
RUNTIME_ENABLED=false
RUNTIME_CACHE_DIR=runtime-cache/flows
RUNTIME_WORK_DIR=runtime-cache/runs
RUNTIME_TIMEOUT_SECONDS=900
RUNTIME_MAX_RETRIES=2
RUNTIME_RETRY_BACKOFF_SECONDS=1
RUNTIME_CLEANUP_ON_FINISH=true
RUNTIME_TRACE_MODE=ON_FAILURE
ARTIFACT_MAX_BYTES=209715200
```

`RUNTIME_ENABLED=true` 依赖既有的 MinIO 包存储配置，但不会隐式设置
`WORKER_LEASE_ENABLED=true`。

Runtime 启用后，`GET /health/ready` 会增加必需依赖 `runtimeFilesystem`，并对
`RUNTIME_CACHE_DIR` 和 `RUNTIME_WORK_DIR` 实际执行目录创建、临时文件写入、读取和
删除。任一检查失败时 readiness 返回 503，响应只包含异常类型，不暴露服务器路径。

`RUNTIME_TRACE_MODE` 支持：

- `OFF`：不启动 Trace。
- `ON_FAILURE`：启动 Trace，但仅在执行最终失败或进入 `WAITING_HUMAN` 时上传。
- `ALWAYS`：无论成功或失败都上传 Trace。

## 浏览器契约

当前仅启用 `browserSession.mode=MANAGED`，支持的 `channel` 为 `chromium`、`chrome`
和 `msedge`。MANAGED 命令的 `closePolicy` 必须为 `ALWAYS` 或
`CLOSE_ON_FINISH`，`profileRef` 和 `cdpEndpointRef` 必须为 null。

Flow 代码只能获得 `ctx.page`。安全配置 `ctx.config.browserSession` 不包含
`profileRef` 或 `cdpEndpointRef` 引用。包校验会拒绝包内任意 Python 文件导入
Playwright 或数据库模块、直接启动
浏览器或调用 CDP，以及直接调用 `open()`。

在部署环境中安装首选的 Playwright 自带浏览器：

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
```

开发机也可以使用本机已安装的 Chrome，并配置 `channel=chrome`。

## Artifact 交付

```text
Flow -> ArtifactRecorder -> 本地 Run 文件
     -> Task POST /worker-api/artifacts/upload-url
     -> 对象存储签名 PUT
     -> Task worker-api/runs/{runId}/artifacts 元数据
```

`upload-url` 请求使用 `worker_id`、`task_id`、`run_id`、`name` 和 `mime_type`。
2026-07-16 对测试服务器 OpenAPI 的只读检查已确认该路由和请求结构；该检查没有上传
Artifact，也没有调用元数据回调。

签名 URL 不会持久化。结构化日志会脱敏常见的签名查询凭据。当
`RUNTIME_CLEANUP_ON_FINISH=true` 时，浏览器资源清理完成后会删除 Run 文件。

## Flow 结构化输出

成功的 `flow.py:run(ctx)` 可以返回 `dict`，Runtime 会把它放入 `RunResult.output`，
Worker 再通过持久化 FINISH Outbox 传给 Task。返回 `None` 的旧 Flow 保持兼容；非
`SUCCESS` 结果禁止携带 output。

输出必须可以由严格 JSON 编码，禁止 `NaN`、`Infinity`、非字符串对象键，以及名称
包含 password、secret、token、credential、authorization 或 cookie 的敏感字段。
UTF-8 JSON 默认不得超过 `RUNTIME_OUTPUT_MAX_BYTES=1048576`。非法或超限分别返回
`FLOW_OUTPUT_INVALID`、`FLOW_OUTPUT_TOO_LARGE`，均按致命错误处理，不重新执行已经
产生外部业务副作用的 Flow。输出正文不会写入 Engine 日志。

## 错误映射

| 异常 | 结果 |
| --- | --- |
| `RpaRetryableError`、Playwright/Python 超时 | 重试至配置上限，随后进入 `FAILED` |
| `RpaBusinessError` | `FAILED` |
| `RpaHumanRequiredError` | `WAITING_HUMAN` |
| `RpaFatalError` | `FAILED` |
| 未知异常 | 以安全错误码 `FLOW_UNHANDLED_ERROR` 进入 `FAILED` |

Runtime 本地文件系统错误使用明确错误码：

| 位置 | 权限错误 | 其他 I/O 错误 |
| --- | --- | --- |
| Flow cache | `FLOW_CACHE_ACCESS_DENIED` | `FLOW_CACHE_WRITE_FAILED` |
| Run 工作目录 | `RUNTIME_WORKDIR_ACCESS_DENIED` | `RUNTIME_WORKDIR_WRITE_FAILED` |

这些错误不会回传本地绝对路径。Linux 部署必须固定使用同一个非 root 服务用户，且
不得让多个 Engine 进程共享 cache/work 目录。

执行最终失败或进入 `WAITING_HUMAN` 时，会尽力采集失败截图。Flow 日志和事件载荷会
通过标准敏感字段脱敏器处理。

## 当前联调门槛

- 2026-07-16 对测试服务器 OpenAPI 的只读检查确认：`lease` 快照结构完整，且 `lease`
  和 `renew` 契约均包含 `leaseExpiresAt`。该检查没有请求 `lease`，没有查看专用真实
  快照，真实 `lease/renew/callback` 链路也尚未完成端到端运行。
- 在专用 Task 数据获得批准，且 `lease` 能解析到精确、有效、已发布的 Registry 版本
  前，真实 Task `lease` 必须保持关闭；禁止回退选择最新版本。
- 默认凭据解析器拒绝非 null 的 `credentialRef`。严格限制作用域的 `mock_env` 解析器
  仅用于 `development`/`test` 环境的 Mock SRM 演示；其 `credentialRef`、`tenantId`、
  `portalAccountId` 和受控 Portal URL 必须与专用 `lease` 完全一致。真实 Portal 凭据仍需
  接入受治理的凭据服务适配器。
- `config.portalUrl` 仅允许用于受控的 Mock Runtime 命令。生产环境仍需通过受治理的
  Task/Portal 配置适配器，根据 `portalAccountId` 解析 Portal。
- 已创建 attempt 的 EVENT 和 FINISH 会持久化到现有 `rpa_callback_outbox`，由后台
  按至少一次语义重试，并使用稳定的 `Idempotency-Key`。Artifact 上传及 metadata
  登记仍为直接调用；只有 attempt 创建前的前置拒绝才使用 direct best-effort 回调。
  Task 必须持久化并按幂等键去重，后续还需结合 `leaseId` 拒绝旧 attempt 发出的
  跨 attempt 陈旧 FINISH。
- 启用 `lease` 轮询前，必须通过专用的真实端到端测试，验证 `lease`、`renew`、
  `event`、Artifact 上传及元数据登记和 `finish`。生产级服务账号鉴权仍是独立的
  发布门槛。
- Python Flow 模块在 Engine 进程内执行。静态策略检查可以降低误用风险，但不构成
  操作系统级沙箱；是否采用进程或容器隔离仍是生产加固待决策项。
- 整个 Flow 重试可能重复产生外部副作用。Phase 5 Flow 必须保持操作幂等；步骤级重试
  留待后续实现。
