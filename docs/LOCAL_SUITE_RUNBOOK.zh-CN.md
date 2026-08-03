# AutoTask 本地服务运行与交接手册

本文档用于同事接管 AutoTask 本地联调环境，覆盖 Auth、Task、RPA Engine 和
SMC-Copilot Client。文档不包含密码、Token、数据库连接串或对象存储密钥。

## 1. 代码与分支

| 组件 | 仓库 | 分支 | 默认端口 |
| --- | --- | --- | --- |
| Auth、Task | `https://github.com/YuweiSu529/nodeskclaw.git` | `v0.1` | `4510`、`4520` |
| RPA Engine | `https://github.com/loudon84/copilot-rpa.git` | `v0.1` | `4610` |
| SMC-Copilot Client | `https://github.com/YuweiSu529/copilot-autotask.git` | `v0.1` | 桌面应用 |

Windows 当前约定目录：

```text
D:\AutoTask-Workspace\
  nodeskclaw\
    nodeskclaw-backend\
    nodeskclaw-task\
  nodeskclaw-rpa-engine\
  copilot-autotask\
```

接管前分别执行 `git status`。`.env`、`.env.development`、`runtime/`、
`runtime-cache/`、`storage/`、Artifact、截图、Trace 和 Flow ZIP 都是环境或运行
产物，不得提交。

## 2. 环境要求

- Windows 10/11 x64；常驻 Linux Engine 另见 `LINUX_DEPLOYMENT.md`。
- Python 3.12、Git、uv、Node.js 和 npm。
- PostgreSQL 16。不得把数据库连接串写入 Git。
- Chrome 或 Playwright Chromium。真实门户 Flow 当前使用 MANAGED 浏览器。
- Engine 启用 Registry/Runtime 时需要可用的 PostgreSQL 和 S3 兼容对象存储。

首次安装依赖：

```powershell
cd D:\AutoTask-Workspace\nodeskclaw\nodeskclaw-backend
uv sync

cd D:\AutoTask-Workspace\nodeskclaw\nodeskclaw-task
uv sync

cd D:\AutoTask-Workspace\nodeskclaw-rpa-engine
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\python.exe -m playwright install chromium

cd D:\AutoTask-Workspace\copilot-autotask
npm ci
```

### 全新机器数据库初始化

4510 和 4520 使用两个独立数据库。PostgreSQL 管理员在自动提交模式下逐条执行，
密码现场设置并通过安全渠道保管：

```sql
CREATE ROLE nodeskclaw_backend_local LOGIN PASSWORD '<现场设置强密码>';
CREATE DATABASE nodeskclaw_backend_local
  OWNER nodeskclaw_backend_local
  ENCODING 'UTF8'
  TEMPLATE template0;

CREATE ROLE nodeskclaw_task_local LOGIN PASSWORD '<现场设置强密码>';
CREATE DATABASE nodeskclaw_task_local
  OWNER nodeskclaw_task_local
  ENCODING 'UTF8'
  TEMPLATE template0;
```

两份 `.env` 分别指向对应数据库。首次部署显式执行一次迁移，成功后运行期保持
`SKIP_AUTO_MIGRATE=1`：

```powershell
cd D:\AutoTask-Workspace\nodeskclaw\nodeskclaw-backend
uv run alembic upgrade head
uv run alembic current

cd D:\AutoTask-Workspace\nodeskclaw\nodeskclaw-task
uv run alembic upgrade head
uv run alembic current
```

Auth 首次启动会按 `INIT_ADMIN_ACCOUNT` 创建管理员，并在控制台显示随机初始密码；
首次登录后立即改密。Engine 的九张表属于 Task 数据库内的 `rpa_engine` Schema，
由 Engine 自己的 Alembic 基线管理，不能用上述两个迁移命令替代。

## 3. 配置文件与凭据

三个服务的 `.env` 只能通过受控渠道交接，不能随源码发送。新机器从各仓库的
`.env.example` 复制后填写。

### Auth

- `DATABASE_URL`：Auth 数据库连接。
- `JWT_SECRET`、`ENCRYPTION_KEY`：必须安全交接；随意重建会使既有 Token 或加密
  数据失效。
- `INIT_ADMIN_ACCOUNT`：仅用于受控初始化，不在 Git 或操作手册记录账号密码。
- `SKIP_AUTO_MIGRATE`：接管现有数据库时设为 `1`，迁移由管理员单独执行。

### Task

- `DATABASE_URL`：Task 数据库连接。
- `JWT_SECRET`：必须与 Auth 的 JWT 签名配置一致。
- `NODESKCLAW_BACKEND_URL=http://127.0.0.1:4510`。
- `RPA_ENGINE_BASE_URL=http://127.0.0.1:4610`。
- `RPA_ENGINE_VALIDATE_BINDING=true`。
- `SEED_DATA_ENABLED=false`、`SKIP_AUTO_MIGRATE=1`。
- `SUCCESSOR_JOB_ENABLED=true`：启用成功后继任务生成。
- `ARTIFACT_UPLOAD_BASE_URL`：填写 Engine 能访问的 Task 地址；同机通常使用
  `http://127.0.0.1:4520`。
- `ARTIFACT_DOWNLOAD_BASE_URL`：填写 Client 所在电脑能访问的 Task 地址，例如
  `http://<服务机可达IP>:4520`。上传和下载地址可以不同。

### RPA Engine

- `APP_HOST=0.0.0.0`、`APP_PORT=4610`。
- `RPA_ENGINE_PUBLIC_BASE_URL=http://<Task可达的Engine地址>:4610`。
- `DATABASE_ENABLED=true`、`DATABASE_URL`、`DATABASE_SCHEMA=rpa_engine`。
- `MINIO_ENABLED=true` 及对应 endpoint、bucket、Access Key 和 Secret Key。
- `TASK_API_BASE_URL=http://127.0.0.1:4520/api/v1/autotask`。
- 当前 Worker API 兼容层使用 `TASK_AUTH_MODE=none`。`service_account` 的 Token
  交换尚未实现，不能误当成生产鉴权方案。
- 完整运行需同时设置 `WORKER_ENABLED=true`、`RUNTIME_ENABLED=true` 和
  `WORKER_LEASE_ENABLED=true`。只做健康检查时优先关闭 lease。
- 当前演示凭据解析使用 `CREDENTIAL_RESOLVER_MODE=mock_env`，仅允许 development
  或 test，并且只支持一个精确的 `credentialRef + tenantId + portalAccountId`。

Portal 表单中的 `credentialRef` 必须与 `MOCK_SRM_CREDENTIAL_REF` 完全一致。
`persist:...` 是 Client 浏览器的 `clientSessionPartition`，不是凭据引用。用户名和
密码只填写在 Engine 的受限环境配置中；新 Portal 因 Portal ID 变化，需要更新
Engine 配置并重启。此单凭据实现只能用于联调演示。

### Client

开发模式使用不提交的 `.env.development`：

```env
VITE_AUTOTASK_API_MODE=remote
VITE_AUTOTASK_AUTH_BACKEND_URL=http://127.0.0.1:4510
VITE_AUTOTASK_TASK_BACKEND_URL=http://127.0.0.1:4520
VITE_AUTOTASK_RPA_ENGINE_URL=http://127.0.0.1:4610
```

跨电脑安装包应把三个 `127.0.0.1` 改为 Client 可访问的服务机地址后重新构建。
Client 直接使用 Engine Registry IPC 功能时还需放行 4610；仅使用任务功能时主要
访问 4510 和 4520。

## 4. 启动顺序

先确认 PostgreSQL 和对象存储已经运行，再按 Auth、Task、Engine、Client 顺序
启动。以下命令均在独立 PowerShell 窗口执行。

### 4.1 Auth

```powershell
cd D:\AutoTask-Workspace\nodeskclaw\nodeskclaw-backend
$env:SKIP_AUTO_MIGRATE = '1'
uv run uvicorn app.main:app --host 0.0.0.0 --port 4510
```

### 4.2 Task

Task 会从 `.env` 读取迁移和种子开关。接管现有数据库时先确认两项安全值；下面
仍显式设置进程变量，使本次启动意图可见：

```powershell
cd D:\AutoTask-Workspace\nodeskclaw\nodeskclaw-task
$env:SKIP_AUTO_MIGRATE = '1'
$env:SEED_DATA_ENABLED = 'false'
uv run uvicorn app.main:app --host 0.0.0.0 --port 4520
```

### 4.3 RPA Engine

```powershell
cd D:\AutoTask-Workspace\nodeskclaw-rpa-engine
.\.venv\Scripts\python.exe -m nodeskclaw_rpa_engine
```

Engine 启动后会注册 Worker、发送心跳并轮询 lease。不要同时启动两个使用相同
`WORKER_ID` 的 Engine 实例。启动 lease 前先确认 Task 中没有无关的 `QUEUED` Run。

### 4.4 Client

开发运行：

```powershell
cd D:\AutoTask-Workspace\copilot-autotask
npm run start
```

构建 Windows x64 内部安装包：

```powershell
npm run make -- --arch=x64
```

安装版配置在构建时固化，修改源码目录的 `.env.development` 不会改变已经安装的
程序。端点或 UI 更新后必须重新构建并在测试电脑重新安装。

## 5. 启动后检查

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:4510/api/v1/health
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:4520/health
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:4610/health/live
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:4610/health/ready
```

期望四项均为 HTTP 200。Engine readiness 中 `required=true` 的 `database`、
`objectStorage`、`taskApi` 和 `runtimeFilesystem` 必须全部为 `healthy`。

```powershell
netstat -ano | Select-String -Pattern ':4510\s',':4520\s',':4610\s'
```

Task Worker 列表应看到配置的 `WORKER_ID` 在线；Engine 日志会持续出现心跳和空
lease 轮询。日志不得包含密码、Token 或签名 URL。

## 6. 日常运行流程

1. 在 Client 新建 Portal，填写门户地址、登录账号、`credentialRef` 和 Session
   分区；不在 Client 填写密码。
2. 新建并启用 Workflow Template。
3. 新建 Workflow Binding，填写精确 Flow ID 和版本，校验后保存。Binding 必须
   保存 Registry 返回的精确 Flow Version UUID 和 checksum。
4. 新建任务。DRAFT 后继任务先由用户补齐预计交货日期，再点击运行。
5. Task `start` 创建 `QUEUED` Run；Engine 的 lease 轮询领取后进入 `RUNNING`。
6. 在任务详情观察事件、终态和证据。截图可预览，XLSX 等文件通过下载地址保存到
   用户下载目录。

Engine 没有单独的“立即执行 Flow”入站接口。运行入口始终是 Task 创建并启动任务，
Engine 通过 Worker lease 主动领取。

## 7. 停止与重启

- 先停止 Client，再在 Engine 窗口按 `Ctrl+C`，等待 Worker 从 `DRAINING` 转为
  `OFFLINE`。
- 再停止 Task 和 Auth。
- 不要直接结束仍在执行真实 Flow 的 Engine 进程，必须先确认没有 `RUNNING` Run。
- 机器重启后按第 4 节重新启动；PostgreSQL Windows 服务通常自动启动，仍需检查。

## 8. 常见故障

| 现象 | 检查 |
| --- | --- |
| Client 登录 `Internal server error` | Client 是否能连接服务机 4510；Auth 是否健康；JWT 和数据库配置是否有效 |
| Client 一直加载 | 4510/4520 endpoint 是否为 Client 可达地址；检查开发工具网络错误 |
| Engine readiness 503 | 查看具体 required dependency；检查数据库、对象存储、Task 和 Runtime 目录 |
| Worker 为 OFFLINE | `WORKER_ENABLED`、Task 地址、Worker ID 和 register/heartbeat 日志 |
| Run 一直 QUEUED | `WORKER_LEASE_ENABLED`、`RUNTIME_ENABLED`、Worker 能力、Binding 精确版本及更早的无效 QUEUED Run |
| 凭据解析失败 | `credentialRef`、tenantId、portalAccountId 是否与 Engine `.env` 精确匹配；修改后是否重启 |
| 截图可见但下载 Not Found | `ARTIFACT_DOWNLOAD_BASE_URL` 是否为 Client 可达地址；签名 URL 是否过期 |
| Artifact 上传失败 | `ARTIFACT_UPLOAD_BASE_URL` 是否为 Engine 可达地址；目录或 S3 bucket 是否可写 |
| Binding 保存 500 | Engine 4610 是否健康；Flow 是否发布；Flow ID、版本、UUID 和 checksum 是否一致 |
| Task 启动时意外迁移 | `.env` 或启动窗口是否设置 `SKIP_AUTO_MIGRATE=1` |

## 9. 当前限制和离职交接事项

1. `mock_env` 只支持单个 Portal 凭据；多 Portal 必须实现受管凭据服务适配器。
2. Engine `service_account` Token 交换尚未实现；`TASK_AUTH_MODE=none` 只能用于受控
   内部测试网络。
3. Type-A `WAITING_HUMAN` 不恢复原浏览器会话，人工处理后需重新运行任务。
4. Task HTTP lease 是兼容层，不是带 ACK、死信和能力路由的生产队列。
5. Flow 在 Engine 进程中运行，尚无操作系统级隔离；外部写操作必须自行保证幂等。
6. Portal 响应不回显 `credentialRef`；编辑时留空表示保持原值。
7. Flow 上传、发布的 Engine IPC 基础已在 Client 中预留，但完整图形化管理页面仍待
   完成；当前可使用 Engine Postman 集合或 API。
8. 数据库迁移、备份、Secret 交接和防火墙变更必须由对应管理员执行并留痕。

## 10. 交接验收清单

- [ ] 三个仓库均从 `v0.1` 拉取，`git status` 无意外改动。
- [ ] Auth、Task、Engine 的 `.env` 已通过安全渠道配置，Git 中无秘密。
- [ ] PostgreSQL、对象存储及 4510/4520/4610 健康检查通过。
- [ ] Worker 注册和心跳正常，停止时能进入 OFFLINE。
- [ ] Client 可登录并查询 Portal、模板、Binding、任务和证据。
- [ ] 使用专用测试 Task 完成一次 SUCCESS，Artifact 可预览和下载。
- [ ] 新 Portal 的 `credentialRef` 精确匹配已验证；密码未进入 Client 或 Task。
- [ ] 已记录数据库备份负责人、Secret 保管人、服务机地址和防火墙负责人。
