# Phase 5：版本化 SRM 验证 Flow

Phase 5 为同一个 Flow ID 保留两个不可变版本。本阶段不新增 Engine 生产接口，不修改
数据库表，也不启用 Task lease 轮询。

| 版本 | 目标 | 用途 |
| --- | --- | --- |
| `1.0.0` | 本地确定性 Mock SRM | 可重复验证 `SUCCESS`、`FAILED` 和 `WAITING_HUMAN` |
| `1.1.0` | 由配置提供的供应商 Portal | 登录、订单查询、进入详情页和下载 XLSX |

两个包均使用 Flow ID `rpa_flow_mock_srm_fetch_po`、工作流代码
`srm_fetch_po`、必填输入 `po_no` 和由 Engine 管理的 MANAGED 浏览器。包代码不得包含
部署地址或凭据。

## 组件

- `nodeskclaw_rpa_engine.mock_srm`：仅供 Flow `1.0.0` 使用的独立本地服务。
- `examples/mock-srm-flow/1.0.0` 和 `examples/mock-srm-flow/1.1.0`：版本化包源码。
- `scripts/build_phase5_package.py`：支持版本参数的包构建器，并执行标准包策略校验。
- `scripts/run_phase5_demo.py`：固定使用 `1.0.0` 的本地三场景 Runtime 验证工具。
- `scripts/run_supplier_portal_demo.py`：用于 `1.1.0` 的本地 MANAGED 浏览器冒烟验证工具。

在 `4610` 端口启动 Engine 不会启动或暴露上述任一 Portal。

## Flow 1.0.0：确定性本地 Mock

| 输入 `po_no` | Runtime 结果 | 错误码 | 证据 |
| --- | --- | --- | --- |
| `PO-20260708-001` | `SUCCESS` | 无 | PO 结果截图和合同 PDF |
| `PO-NOT-FOUND` | `FAILED` | `BUSINESS_NOT_FOUND` | 未找到截图、Runtime 失败截图和 Trace |
| `PO-MANUAL-001` | `WAITING_HUMAN` | `HUMAN_VERIFICATION_REQUIRED` | 人工检查截图、Runtime 失败截图和 Trace |

`WAITING_HUMAN` 遵循已冻结的 Type-A 模式：服务器浏览器采集证据后关闭。操作人员另行
处理任务，原 Playwright 上下文不会恢复。

运行独立 Mock Portal：

```powershell
.\.venv\Scripts\python.exe -m nodeskclaw_rpa_engine.mock_srm
```

默认端点：

```text
GET http://127.0.0.1:4600/health/live
GET http://127.0.0.1:4600/
GET http://127.0.0.1:4600/contracts/PO-20260708-001.pdf
```

使用真实 MANAGED Chrome 浏览器运行 `1.0.0` 的全部三个场景：

```powershell
.\.venv\Scripts\python.exe scripts\run_phase5_demo.py `
  --start-mock-srm `
  --channel chrome
```

安装 Playwright Chromium 后可使用 `--channel chromium`。添加 `--headful` 可显示
浏览器；也可以通过 `--scenario success`、`--scenario failed` 或
`--scenario waiting_human` 只选择一个场景。该验证工具使用内存包交付、合成的作用域
凭据解析器、本地事件捕获和本地 Artifact 副本，不访问 PostgreSQL、MinIO 或 Task API。
证据写入 Git 忽略的 `runtime-cache/phase5-demo/artifacts` 目录。

## Flow 1.1.0：通过配置接入供应商 Portal

Flow `1.1.0` 从 `ctx.portal_url` 获取 Portal URL，从 `ctx.credentials` 获取凭据，并从
`ctx.input["po_no"]` 获取订单号，随后执行：

1. 打开配置的登录页面，完成协议勾选并提交登录表单。
2. 通过固定文件名映射解析十张受控演示 CAPTCHA 图片之一。图片缺失或未知时记录证据，
   并返回 `WAITING_HUMAN / HUMAN_VERIFICATION_REQUIRED`。
3. 打开订单列表，查询指定 PO 并进入详情页。订单不存在时返回
   `FAILED / BUSINESS_NOT_FOUND`。
4. 打开下载确认弹窗，等待浏览器下载，并通过 Engine Artifact Recorder 保存文件。
5. 确认结果是非空 XLSX 后再报告成功。

默认验收订单为 `POJS2606030010`，Task 输入仍为动态值。目前观察到 Portal 始终返回
固定文件 `order-20260709122735.xlsx`。该验收文件不是 PDF，也不声明它与所选订单
一一对应。

进行本地浏览器冒烟测试时，地址和凭据只能通过进程环境提供，禁止提交：

```powershell
$env:SUPPLIER_PORTAL_URL = "<supplier-portal-url>"
$env:SUPPLIER_PORTAL_USERNAME = "<username>"
$env:SUPPLIER_PORTAL_PASSWORD = "<password>"
.\.venv\Scripts\python.exe scripts\run_supplier_portal_demo.py `
  --po-no POJS2606030010 `
  --channel chrome
```

`--portal-url` 可以覆盖 `SUPPLIER_PORTAL_URL`，必须通过这两个来源之一提供 URL。凭据只从
`SUPPLIER_PORTAL_USERNAME` 和 `SUPPLIER_PORTAL_PASSWORD` 读取。支持的浏览器
`channel` 为 `chromium`、`chrome` 和 `msedge`；默认以 `headless` 模式执行，使用
`--no-headless` 可显示浏览器。本地证据写入 Git 忽略的
`runtime-cache/supplier-portal-demo/artifacts` 目录。

Task 驱动的 `development`/`test` 执行使用严格限制作用域的解析器。以下值必须由本地
部署配置或密钥注入提供：

```env
CREDENTIAL_RESOLVER_MODE=mock_env
MOCK_SRM_CREDENTIAL_REF=<dedicated-reference>
MOCK_SRM_USERNAME=<portal-user>
MOCK_SRM_PASSWORD=<portal-password>
MOCK_SRM_ALLOWED_TENANT_ID=<dedicated-tenant>
MOCK_SRM_ALLOWED_PORTAL_ACCOUNT_ID=<dedicated-portal-account>
```

三个作用域值必须与 Task `lease` 完全一致。`staging` 和 `production` 环境会拒绝
`mock_env`，且该模式不能替代受治理的凭据服务适配器。

## 构建任一不可变包

构建器默认构建 `1.1.0`，使用 `--version` 可以明确指定目标：

```powershell
.\.venv\Scripts\python.exe scripts\build_phase5_package.py --version 1.1.0
.\.venv\Scripts\python.exe scripts\build_phase5_package.py --version 1.0.0
```

输出文件：

```text
dist/rpa_flow_mock_srm_fetch_po-1.1.0.zip
dist/rpa_flow_mock_srm_fetch_po-1.0.0.zip
```

`--output <path>` 可以覆盖输出位置。构建器输出 SHA256，并使用与 Flow 上传 API 相同的
策略校验 ZIP。每个包仅包含 `manifest.json`、`selectors.json` 和 `flow.py`，不包含
凭据、内部地址或环境配置。

ZIP 元数据已经标准化，因此相同源码在全新检出后仍会生成相同 checksum。构建器还会
验证重新构建的 `1.0.0` 与已发布 checksum 在字节级完全一致；如果历史包发生变化，
则拒绝输出。

既有版本不可覆盖。应将 `1.1.0` 作为新的 Registry 版本发布，并记录返回的 Flow
Version UUID 和 checksum。Task WorkflowBinding 必须同时固定精确的 `1.1.0` 版本和
该不可变快照；禁止回退到最新版本，也禁止复用 `1.0.0` 快照。

## 中央 AutoTask 联调门槛

在以下条件全部满足前，必须保持 `WORKER_LEASE_ENABLED=false`：

- 专用 Portal、精确的 `1.1.0` WorkflowBinding 以及隔离的 Task/Run 均已获准使用。
  不得遗留无关的 `QUEUED` Run，因为当前兼容层 `lease` 接口尚未按 Worker `capability`
  过滤。
- `lease` 快照包含已配置的 Portal URL、完整的 MANAGED 浏览器设置、限定作用域的
  `credentialRef`、精确的 Flow UUID/checksum 和 `leaseExpiresAt`。
- `lease`、`renew`、`event`、XLSX Artifact 上传及元数据登记和 `finish` 回调全部通过
  端到端验证。
- 部署地址和密钥只存在于环境变量或 Task 配置中，不得进入源码、日志、截图或测试
  快照。

回调当前采用直接的尽力调用方式；持久化 Callback Outbox 交付和生产级 Worker 服务
账号鉴权仍待实现。
