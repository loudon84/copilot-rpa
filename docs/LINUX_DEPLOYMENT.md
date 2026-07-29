# Linux 部署指南

本文用于在 Linux 测试机上以 systemd 单进程方式部署 NoDeskClaw RPA Engine
`0.6.0`。示例不包含内网地址或真实凭据；所有占位值必须在服务器本地替换，且不得
提交到 Git。

## 1. 部署基线与边界

- 以下命令以 Playwright 支持的 Ubuntu 24.04 LTS 为基线，并且必须使用 Python `3.12`。
- Engine 使用固定非 root 用户 `nodeskclaw-rpa` 运行，不得以 root 运行浏览器。
- 每个 Engine 实例只启动一个 Python/Uvicorn 进程；不得增加 `--workers`。多进程会
  重复注册 Worker、重复轮询 `lease`，并争用进程内锁保护的 Flow 缓存。
- 如需部署多个实例，每个实例必须使用不同的 `WORKER_ID`，并使用独立的
  `RUNTIME_CACHE_DIR`、`RUNTIME_WORK_DIR` 和 Playwright 浏览器目录。
- PostgreSQL 的 `nodeskclaw_task` 数据库、`rpa_engine` Schema、九张 Engine 表及
  Alembic 基线必须由 DBA 预先准备。Engine 启动时不会建库、建表、执行迁移、
  `stamp` 或种子操作。
- MinIO/S3 存储桶必须由管理员预先创建。Engine 启动时不会创建存储桶。

## 2. 创建用户和目录

以下命令以 Ubuntu 为例：

```bash
sudo useradd --system \
  --home-dir /var/lib/nodeskclaw-rpa-engine \
  --create-home \
  --shell /usr/sbin/nologin \
  nodeskclaw-rpa

sudo install -d -m 0755 -o root -g root \
  /opt/nodeskclaw-rpa-engine
sudo install -d -m 0750 -o root -g nodeskclaw-rpa \
  /etc/nodeskclaw-rpa-engine
sudo install -d -m 0700 -o nodeskclaw-rpa -g nodeskclaw-rpa \
  /var/lib/nodeskclaw-rpa-engine/flows \
  /var/lib/nodeskclaw-rpa-engine/runs \
  /var/lib/nodeskclaw-rpa-engine/ms-playwright
```

Runtime 目录和浏览器目录必须始终归 `nodeskclaw-rpa` 所有。若目录由 root、另一用户
或不同容器 UID 创建，Flow 缓存可能因 `EACCES` 读取失败。修复所有权前必须先停止
Engine，不得在运行中删除缓存。

## 3. 拉取代码并安装 Python 依赖

安装系统基础工具：

```bash
sudo apt-get update
sudo apt-get install -y \
  ca-certificates \
  curl \
  git \
  python3.12 \
  python3.12-venv
```

拉取已确认的 `v0.1` 分支，并记录提交 SHA：

```bash
sudo git clone \
  --branch v0.1 \
  --single-branch \
  https://github.com/loudon84/copilot-rpa.git \
  /opt/nodeskclaw-rpa-engine

sudo git -C /opt/nodeskclaw-rpa-engine status --short
sudo git -C /opt/nodeskclaw-rpa-engine rev-parse HEAD
```

若目标目录已由 `install -d` 创建且 Git 拒绝克隆，可先将仓库克隆到临时目录，再由
管理员复制到 `/opt/nodeskclaw-rpa-engine`。最终代码和虚拟环境应由 root 管理并对
服务用户保持只读。

创建虚拟环境并安装运行依赖：

```bash
cd /opt/nodeskclaw-rpa-engine
sudo python3.12 -m venv .venv
sudo .venv/bin/python -m pip install --upgrade pip
sudo .venv/bin/python -m pip install .
.venv/bin/python -m pip check
```

服务器常驻部署安装项目本身即可；只有需要在服务器运行 Ruff、mypy 和 pytest 时才
安装开发依赖：

```bash
sudo .venv/bin/python -m pip install ".[dev]"
```

## 4. 分步安装 Chromium

先以管理员权限安装 Chromium 所需的系统依赖：

```bash
cd /opt/nodeskclaw-rpa-engine
sudo .venv/bin/python -m playwright install-deps chromium
```

再以 Engine 服务用户安装浏览器，并显式使用 systemd 中相同的浏览器目录：

```bash
sudo -u nodeskclaw-rpa \
  env PLAYWRIGHT_BROWSERS_PATH=/var/lib/nodeskclaw-rpa-engine/ms-playwright \
  /opt/nodeskclaw-rpa-engine/.venv/bin/python \
  -m playwright install chromium
```

部署新 Playwright 版本后必须再次执行浏览器安装命令，确保 Python 包和浏览器版本
匹配。服务器运行时使用 `headless=true`；如设置为 `false`，必须另行部署显示服务，
不属于本指南范围。

Task WorkflowBinding 的 `config.browserSession` 必须使用：

```json
{
  "mode": "MANAGED",
  "headless": true,
  "channel": "chromium",
  "profileRef": null,
  "cdpEndpointRef": null,
  "closePolicy": "CLOSE_ON_FINISH"
}
```

仅执行 `playwright install chromium` 不会安装 Google Chrome。如果 Binding 仍使用
`channel=chrome`，Runtime 会以 `BROWSER_LAUNCH_FAILED` 结束；应先将 Binding
切换为 `chromium`，或由管理员另行安装受支持的 Chrome 渠道。

## 5. 配置 EnvironmentFile

创建仅供管理员和服务用户读取的配置文件：

```bash
sudo install -m 0640 -o root -g nodeskclaw-rpa \
  /dev/null /etc/nodeskclaw-rpa-engine/engine.env
sudoedit /etc/nodeskclaw-rpa-engine/engine.env
```

以下是第一阶段“仅注册和心跳”的安全模板。所有 `example.test` 地址和“请替换”值
都必须替换为测试环境的实际值，秘密不得写入仓库或命令历史：

```env
APP_NAME=nodeskclaw-rpa-engine
APP_ENV=test
APP_HOST=0.0.0.0
APP_PORT=4610
RPA_ENGINE_PUBLIC_BASE_URL=http://engine.example.test:4610
LOG_LEVEL=INFO

DATABASE_ENABLED=true
DATABASE_URL='postgresql+asyncpg://请替换:请替换@db.example.test:5432/nodeskclaw_task'
DATABASE_SCHEMA=rpa_engine
DATABASE_POOL_SIZE=5

MINIO_ENABLED=true
MINIO_ENDPOINT_URL=https://minio.example.test
MINIO_ACCESS_KEY='请替换'
MINIO_SECRET_KEY='请替换'
MINIO_BUCKET=rpa-flow-packages
MINIO_REGION=us-east-1

TASK_API_BASE_URL=https://task.example.test/api/v1/autotask
TASK_ARTIFACT_UPLOAD_BASE_URL=
TASK_AUTH_MODE=none
TASK_API_TIMEOUT_SECONDS=10

WORKER_ENABLED=true
WORKER_LEASE_ENABLED=false
WORKER_ID=server-worker-linux-001
WORKER_TYPE=SERVER_WORKER
WORKER_DEVICE_NAME=nodeskclaw-rpa-engine-linux
WORKER_OS=linux
WORKER_CAPABILITIES='["PLAYWRIGHT_CDP","BROWSER_SESSION_MANAGED","SCREENSHOT","DOWNLOAD"]'
WORKER_TAGS='["linux","test"]'
WORKER_MAX_CONCURRENT_RUNS=1
WORKER_HEARTBEAT_INTERVAL_SECONDS=15
WORKER_POLL_INTERVAL_SECONDS=5
WORKER_LEASE_RENEW_INTERVAL_SECONDS=20
WORKER_OFFLINE_THRESHOLD_SECONDS=45
WORKER_SHUTDOWN_GRACE_SECONDS=30

RUNTIME_ENABLED=false
RUNTIME_CACHE_DIR=/var/lib/nodeskclaw-rpa-engine/flows
RUNTIME_WORK_DIR=/var/lib/nodeskclaw-rpa-engine/runs
RUNTIME_TIMEOUT_SECONDS=900
RUNTIME_MAX_RETRIES=2
RUNTIME_RETRY_BACKOFF_SECONDS=1
RUNTIME_CLEANUP_ON_FINISH=true
RUNTIME_TRACE_MODE=ON_FAILURE
ARTIFACT_MAX_BYTES=209715200

CREDENTIAL_RESOLVER_MODE=disabled

NO_PROXY=127.0.0.1,localhost,db.example.test,minio.example.test,task.example.test,portal.example.test
no_proxy=127.0.0.1,localhost,db.example.test,minio.example.test,task.example.test,portal.example.test
```

`RPA_ENGINE_PUBLIC_BASE_URL` 必须是 Task 和管理端能够访问的 Engine 根地址。
测试机可直接使用 `APP_PORT`；如通过反向代理终止 TLS，则必须填写代理后的 HTTPS
根地址，并确保 `/health`、`/api/v1` 和 OpenAPI 路径正确转发。
`TASK_API_BASE_URL` 必须包含 `/api/v1/autotask`。如 Task 返回的 Artifact 签名上传
URL 使用回环主机，可将 `TASK_ARTIFACT_UPLOAD_BASE_URL` 设置为 Engine 可访问的同一
对象存储源地址；路径和签名查询参数仍由原 URL 提供。

在使用 HTTP/HTTPS 代理的环境中，必须把 PostgreSQL、MinIO、Task、供应商 Portal
以及签名上传 URL 的主机加入大写和小写两组 `NO_PROXY`。如内部 HTTPS 使用私有
CA，应把 CA 安装到系统信任库，不要通过关闭证书校验规避问题。

`TASK_AUTH_MODE=none` 只允许用于受控测试环境。不得把本模板直接用于生产环境。

## 6. 安装并启动 systemd 服务

安装仓库内的服务单元：

```bash
cd /opt/nodeskclaw-rpa-engine
sudo install -m 0644 -o root -g root \
  deploy/systemd/nodeskclaw-rpa-engine.service \
  /etc/systemd/system/nodeskclaw-rpa-engine.service
sudo systemctl daemon-reload
sudo systemctl enable nodeskclaw-rpa-engine.service
sudo systemctl start nodeskclaw-rpa-engine.service
```

该服务固定使用 `nodeskclaw-rpa` 用户、单进程入口和 `UMask=0077`；日志以结构化
JSON 输出到 journald。systemd 通过 `SIGTERM` 请求优雅退出，并给予 60 秒停止时间，
覆盖默认 30 秒的 Worker 收尾窗口。

检查进程、日志和健康状态：

```bash
sudo systemctl status nodeskclaw-rpa-engine.service
sudo journalctl -u nodeskclaw-rpa-engine.service -f
curl --fail --silent --show-error http://127.0.0.1:4610/health/live
curl --fail --silent --show-error http://127.0.0.1:4610/health/ready
```

第一阶段的验收条件：

- `/health/live` 和 `/health/ready` 均返回 HTTP 200。
- 就绪检查响应中 `database`、`objectStorage`、`taskApi` 均为 `healthy`，
  `runtimeFilesystem` 为 `disabled`。
- Task 能看到 `server-worker-linux-001` 注册并持续心跳。
- `WORKER_LEASE_ENABLED=false`、`RUNTIME_ENABLED=false`，Engine 不领取任何 Run。

## 7. 第二阶段启用 Runtime 与 `lease`

只有以下前置条件全部满足后，才能进入第二阶段：

- Flow 包已上传、校验并发布，WorkflowBinding 保存精确的 Flow 版本 UUID 和校验和。
- Binding 的 `browserSession.channel=chromium`、`headless=true`，且 Portal 地址可由
  Linux 主机访问。
- Task 队列中没有无关、无效或缺少 Flow 快照的 `QUEUED` Run。
- PostgreSQL、MinIO、Task、Portal 和 Artifact 签名上传地址均可从 Engine 主机访问。
- 凭据引用、租户 ID 和 Portal 账号 ID 已准备完成，秘密只保存在受控配置中。

先优雅停止第一阶段实例，并确认 Task 中的 Worker 最终变为 `OFFLINE`：

```bash
sudo systemctl stop nodeskclaw-rpa-engine.service
sudo systemctl status nodeskclaw-rpa-engine.service
```

在 `/etc/nodeskclaw-rpa-engine/engine.env` 中修改或追加：

```env
RUNTIME_ENABLED=true
WORKER_LEASE_ENABLED=true

CREDENTIAL_RESOLVER_MODE=mock_env
MOCK_SRM_CREDENTIAL_REF=请替换
MOCK_SRM_USERNAME='请替换'
MOCK_SRM_PASSWORD='请替换'
MOCK_SRM_ALLOWED_TENANT_ID=请替换
MOCK_SRM_ALLOWED_PORTAL_ACCOUNT_ID=请替换
```

`mock_env` 仅允许在 `APP_ENV=test` 的受控联调中使用，并且必须精确限制到一个
`credentialRef`、租户和 Portal 账号。它不是生产凭据方案。

确认配置文件权限仍为 `0640 root:nodeskclaw-rpa` 后启动：

```bash
sudo chown root:nodeskclaw-rpa \
  /etc/nodeskclaw-rpa-engine/engine.env
sudo chmod 0640 /etc/nodeskclaw-rpa-engine/engine.env
sudo systemctl start nodeskclaw-rpa-engine.service
```

先验证就绪检查通过、`runtimeFilesystem=healthy` 且 Worker 为 `ONLINE`，再通过 Task
创建并启动唯一一条专用任务。
Engine 会在后台自动调用 Task Worker API 完成 `lease`、续租、事件、Artifact 和
`finish`；不要使用 Postman 手工代替这些 Worker 回调。已创建 attempt 的 EVENT
和 FINISH 会先写入现有 `rpa_callback_outbox`，再由后台携带稳定
`Idempotency-Key` 按至少一次语义重试。Artifact 上传和 metadata 登记仍为直接
调用；只有 attempt 创建前的前置拒绝才使用 direct best-effort 回调。

同一 `worker_id` 不得并发启动多个 Engine 实例。Worker 启动时会把该 ID 遗留的
`LEASED` / `RUNNING` attempt 恢复为 `ABANDONED`，写入 `ended_at`，并在
同一事务中写入对应的 FINISH Outbox；Task 仍需按 `leaseId` 拒绝跨 attempt 的
陈旧 FINISH。

## 8. 网络与代理检查

至少开放以下方向，具体端口以部署配置为准：

- 入站：Task 和授权管理端到 Engine `APP_PORT`。
- 出站：Engine 到 PostgreSQL、MinIO/S3、Task API、供应商 Portal，以及 Artifact
  签名上传 URL 指向的主机。
- DNS 与 TLS：服务用户必须能解析全部主机名，并信任对应 CA。

从 Engine 主机检查公开健康入口和依赖端点，不要在命令中写入密码或签名 URL：

```bash
curl --fail --silent --show-error http://engine.example.test:4610/health/live
curl --fail --silent --show-error http://engine.example.test:4610/health/ready
curl --fail --silent --show-error https://task.example.test/
curl --fail --silent --show-error https://minio.example.test/
curl --fail --silent --show-error https://portal.example.test/
```

若就绪检查返回 503，使用 journald 中的依赖类型和错误类别定位问题。健康响应会
脱敏，不会返回数据库 URL、密钥或凭据。

## 9. 更新、停机与权限故障

更新代码前先确认没有运行中任务，再优雅停止服务：

```bash
sudo systemctl stop nodeskclaw-rpa-engine.service
cd /opt/nodeskclaw-rpa-engine
sudo git fetch origin
sudo git switch v0.1
sudo git pull --ff-only origin v0.1
sudo .venv/bin/python -m pip install .
sudo .venv/bin/python -m pip check
```

如 Playwright 版本发生变化，重新执行第 4 节的系统依赖和同用户 Chromium 安装步骤，
再启动服务并检查健康状态：

```bash
sudo systemctl start nodeskclaw-rpa-engine.service
curl --fail --silent --show-error http://127.0.0.1:4610/health/ready
```

出现 `PermissionError` 或 `EACCES` 时，先停止服务，再检查 Runtime 和浏览器目录：

```bash
sudo stat /var/lib/nodeskclaw-rpa-engine/flows
sudo stat /var/lib/nodeskclaw-rpa-engine/runs
sudo stat /var/lib/nodeskclaw-rpa-engine/ms-playwright
sudo chown -R nodeskclaw-rpa:nodeskclaw-rpa \
  /var/lib/nodeskclaw-rpa-engine
```

不要让 root、pytest、调试脚本和常驻 Engine 共享同一 Runtime 目录。修复后先进行
就绪检查和本地受控冒烟，再重新创建 Run；旧失败 Run 应保留用于审计。

## 10. 生产部署阻断项

当前版本可用于受控测试和演示，但以下事项完成前不得按生产服务验收：

- `TASK_AUTH_MODE=none` 不是生产鉴权；Worker 服务账号令牌交换尚未闭环。
- `CREDENTIAL_RESOLVER_MODE=mock_env` 仅限测试，缺少生产级凭据服务适配器。
- Task 的 `lease` 能力过滤、生产队列确认、可见性超时、重试和死信行为尚未闭环。
- EVENT 和 FINISH 已使用现有 `rpa_callback_outbox` 至少一次重试；Task 必须持久化
  并按稳定的 `Idempotency-Key` 去重，后续还需结合 `leaseId` 拒绝旧 attempt 发出的
  跨 attempt 陈旧 FINISH。Artifact 上传和 metadata 登记仍为直接调用，attempt
  创建前的前置拒绝仍只能 direct best-effort 回调。
- Python Flow 仍在 Engine 进程内执行，尚无操作系统级进程或容器隔离。
- A 类模式（Type-A）`WAITING_HUMAN` 不能在人工处理后恢复原浏览器会话。
- Task 必须提供外部可访问的 Artifact 对外公开/下载基础 URL，不得返回回环地址。

生产发布前还应补齐密钥托管、TLS、最小网络权限、监控告警、备份恢复、容量测试和
多实例隔离方案，并重新执行完整端到端验收。
