# Phase 2 Flow Registry 接口

本地默认地址：`http://127.0.0.1:4610`。其他环境请通过部署配置或
Postman Collection 变量指定地址。

Phase 2 测试环境尚未接入 JWT。除健康检查外，请求必须携带：

```text
X-Actor-Id: <调用者标识>
X-Tenant-Id: <仅 TENANT Flow 必填>
```

这些请求头仅用于测试环境的租户上下文和审计记录，不是生产鉴权。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/v1/flows` | 查询可见 Flow，支持 `scope/status/search/limit/offset` |
| `POST` | `/api/v1/flows/packages` | multipart 上传、校验并创建 DRAFT 版本 |
| `GET` | `/api/v1/flows/{rpaFlowId}` | Flow 详情及版本 |
| `GET` | `/api/v1/flows/{rpaFlowId}/versions` | 版本列表 |
| `POST` | `/api/v1/flows/{rpaFlowId}/disable` | 禁用 Flow，禁止新绑定 |
| `POST` | `/api/v1/flows/{rpaFlowId}/rollback` | 将指定旧版本重新发布，并弃用其他已发布版本 |
| `GET` | `/api/v1/flow-versions/{id}` | 版本详情 |
| `POST` | `/api/v1/flow-versions/{id}/validate` | 从 MinIO 重新下载并校验 |
| `POST` | `/api/v1/flow-versions/{id}/publish` | 校验并发布 |
| `POST` | `/api/v1/flow-versions/{id}/deprecate` | 弃用已发布版本 |
| `POST` | `/api/v1/flow-versions/{id}/disable` | 禁用版本 |
| `POST` | `/api/v1/flow-versions/validate-binding` | 给 Task 服务提供绑定校验 |
| `GET` | `/api/v1/flow-versions/{id}/package` | 307 跳转到短时 MinIO 签名地址 |

`rpaFlowId` 映射 `rpa_flows.flow_key`，`rpaFlowVersionId` 是
`rpa_flow_versions.id` 的 UUID。API 返回的 `packageChecksum` 带
`sha256:` 前缀，数据库只保存 64 位小写十六进制值。

## 上传

`POST /api/v1/flows/packages` 使用 `multipart/form-data`：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `package` | file | ZIP 包，必须包含根目录 `manifest.json` 和 `flow.py` |
| `scope` | string | `GLOBAL` 或 `TENANT`，默认 `GLOBAL` |
| `description` | string | 可选 |
| `labels` | string | JSON 字符串数组，例如 `["srm","demo"]` |

默认限制：压缩包 50 MiB、解压后 200 MiB、500 个文件、100 倍压缩比。
校验过程不会 import 或执行 `flow.py`，只用 AST 验证顶层
`async def run(ctx)`。绝对路径、`..`、反斜杠路径、符号链接、加密条目
以及 `.env`/`credentials.json`/`secrets.json` 会被拒绝。

对象键固定为：

```text
flows/{rpaFlowId}/{version}/{sha256}.zip
```

同一 Flow 的同一版本不可覆盖。上传事务失败时，数据库回滚并清理本次
未提交的对象。

## 错误响应

Flow Registry 业务错误统一为：

```json
{
  "error": {
    "code": "FLOW_VERSION_NOT_FOUND",
    "message": "Flow Version was not found",
    "details": null
  }
}
```

请求参数校验不会回显原始输入，错误码为
`REQUEST_VALIDATION_FAILED`。

## 状态与回滚

发布严格经过数据库触发器约束的状态机：

```text
DRAFT -> VALIDATING -> PUBLISHED -> DEPRECATED
                         |              |
                         +-> DISABLED <-+
```

Registry 回滚只调整版本发布状态，不修改 `nodeskclaw-task` 中已经存在的
WorkflowBinding。绑定回滚仍应由 Task 服务把绑定指回旧版本完成。
