# Phase 5 采购订单 Flow

本目录保存 `rpa_flow_mock_srm_fetch_po` 两个不可变版本的源码树：

- `1.0.0` 用于运行确定性的本地 Mock SRM 成功、失败和 `WAITING_HUMAN` 场景。
- `1.1.0` 登录配置指定的供应商门户，查询订单、打开详情页，并记录门户返回的
  XLSX 下载文件。

Runtime 负责管理浏览器，并注入 `ctx.page`、凭据、选择器、事件和 Artifact API。
两个 Flow 都不会自行启动浏览器、从磁盘读取秘密、调用 Task API 或访问数据库。

## 场景

| `po_no` | 预期结果 | 证据 |
| --- | --- | --- |
| `PO-20260708-001` | `SUCCESS` | 结果截图和合同 PDF |
| `PO-NOT-FOUND` | `FAILED / BUSINESS_NOT_FOUND` | 未找到截图和 Runtime 失败截图 |
| `PO-MANUAL-001` | `WAITING_HUMAN / HUMAN_VERIFICATION_REQUIRED` | 人工检查截图和 Runtime 失败截图 |

测试凭据解析器必须为专用 Mock 凭据引用提供非空的 `username` 和 `password`。
凭据不得进入 Flow 包、任务输入、日志或截图。

构建真实供应商门户 `1.1.0` ZIP（默认版本）：

```powershell
.\.venv\Scripts\python.exe scripts\build_phase5_package.py
```

显式构建确定性 Mock SRM 包：

```powershell
.\.venv\Scripts\python.exe scripts\build_phase5_package.py --version 1.0.0
```

生成文件路径为 `dist/rpa_flow_mock_srm_fetch_po-<version>.zip`，`dist` 目录已被
Git 忽略。已发布的 `rpaFlowId + version` 不可变；Task Binding 必须固定到精确的
Registry 版本 ID 和 checksum。构建器会规范化 ZIP 元数据；如果重新构建会改变已发布
`1.0.0` 的 checksum，则拒绝生成该包。
