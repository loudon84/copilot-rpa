from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response

SUCCESS_PO = "PO-20260708-001"
NOT_FOUND_PO = "PO-NOT-FOUND"
MANUAL_PO = "PO-MANUAL-001"

_PORTAL_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AutoTask Mock SRM</title>
  <style>
    :root { color-scheme: light; font-family: Inter, "Microsoft YaHei", sans-serif; }
    body { margin: 0; background: #f3f6fb; color: #172033; }
    header { background: #163a70; color: white; padding: 18px 32px; }
    main { width: min(900px, calc(100% - 32px)); margin: 32px auto; }
    .card {
      background: white;
      border-radius: 12px;
      padding: 24px;
      box-shadow: 0 8px 28px #163a7018;
    }
    label { display: block; margin: 14px 0 6px; font-weight: 600; }
    input {
      box-sizing: border-box;
      width: 100%;
      padding: 10px 12px;
      border: 1px solid #bdc9d9;
      border-radius: 6px;
    }
    button, .button {
      display: inline-block;
      margin-top: 16px;
      padding: 10px 18px;
      border: 0;
      border-radius: 6px;
      background: #1468d4;
      color: white;
      cursor: pointer;
      text-decoration: none;
    }
    .hidden { display: none; }
    .notice { margin-top: 18px; padding: 14px; border-radius: 8px; }
    .success { background: #e9f8ef; color: #17643a; }
    .error { background: #fff0f0; color: #9d2424; }
    .warning { background: #fff5d9; color: #795600; }
    .meta { color: #637083; font-size: 14px; }
    table { width: 100%; margin-top: 12px; border-collapse: collapse; }
    td, th { padding: 10px; border-bottom: 1px solid #e4e9f0; text-align: left; }
  </style>
</head>
<body>
  <header><strong>AutoTask Mock SRM</strong> · Phase 5 可重复演示环境</header>
  <main>
    <section id="login-panel" class="card">
      <h1>供应商门户登录</h1>
      <p class="meta">本页面只接受非空演示凭据，不保存或发送凭据信息。</p>
      <label for="username">用户名</label>
      <input id="username" autocomplete="off">
      <label for="password">密码</label>
      <input id="password" type="password" autocomplete="off">
      <button id="login-button" type="button">登录</button>
      <div id="login-error" class="notice error hidden" role="alert">
        请输入用户名和密码
      </div>
    </section>

    <section id="workspace" class="card hidden">
      <h1>采购订单查询</h1>
      <label for="po-number">PO 编号</label>
      <input id="po-number" autocomplete="off">
      <button id="search-button" type="button">查询</button>
      <div id="search-status" data-state="idle" aria-live="polite"></div>

      <div id="po-result" class="notice success hidden">
        <strong>采购订单已找到</strong>
        <table>
          <tr><th>PO 编号</th><td id="result-po-number"></td></tr>
          <tr><th>供应商</th><td>AutoTask Demo Supplier</td></tr>
          <tr><th>状态</th><td>已审批</td></tr>
        </table>
        <a id="download-contract" class="button" href="#">下载合同</a>
      </div>

      <div id="not-found" class="notice error hidden" role="alert">
        未查询到采购订单
      </div>

      <div id="human-check" class="notice warning hidden" role="alert">
        需要完成 CAPTCHA / MFA 人工验证。本次服务器浏览器会话不会恢复。
      </div>
    </section>
  </main>
  <script>
    const byId = (id) => document.getElementById(id);
    const hide = (id) => byId(id).classList.add("hidden");
    const show = (id) => byId(id).classList.remove("hidden");

    byId("login-button").addEventListener("click", () => {
      const username = byId("username").value.trim();
      const password = byId("password").value;
      if (!username || !password) {
        show("login-error");
        return;
      }
      hide("login-error");
      hide("login-panel");
      show("workspace");
    });

    byId("search-button").addEventListener("click", () => {
      const po = byId("po-number").value.trim().toUpperCase();
      hide("po-result");
      hide("not-found");
      hide("human-check");
      const status = byId("search-status");
      if (po === "PO-MANUAL-001") {
        status.dataset.state = "human";
        status.textContent = "查询被人工验证拦截";
        show("human-check");
        return;
      }
      if (po !== "PO-20260708-001") {
        status.dataset.state = "not-found";
        status.textContent = "查询完成：无结果";
        show("not-found");
        return;
      }
      status.dataset.state = "success";
      status.textContent = "查询完成：已找到";
      byId("result-po-number").textContent = po;
      const download = byId("download-contract");
      download.href = `/contracts/${encodeURIComponent(po)}.pdf`;
      download.download = `${po}-contract.pdf`;
      show("po-result");
    });
  </script>
</body>
</html>
"""


def _contract_pdf(po_number: str) -> bytes:
    text = f"AutoTask Mock SRM contract for {po_number}"
    content = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<</Type /Catalog /Pages 2 0 R>>",
        b"<</Type /Pages /Kids [3 0 R] /Count 1>>",
        (
            b"<</Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources <</Font <</F1 5 0 R>>>> /Contents 4 0 R>>"
        ),
        b"<</Length "
        + str(len(content)).encode("ascii")
        + b">>\nstream\n"
        + content
        + b"\nendstream",
        b"<</Type /Font /Subtype /Type1 /BaseFont /Helvetica>>",
    ]
    document = bytearray(b"%PDF-1.4\n%AutoTask\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(document))
        document.extend(f"{number} 0 obj\n".encode("ascii"))
        document.extend(body)
        document.extend(b"\nendobj\n")
    xref_offset = len(document)
    document.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    document.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        document.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    document.extend(
        (
            f"trailer\n<</Size {len(objects) + 1} /Root 1 0 R>>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(document)


def create_mock_srm_app() -> FastAPI:
    application = FastAPI(
        title="AutoTask Mock SRM",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @application.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok", "service": "autotask-mock-srm"}

    @application.get("/", response_class=HTMLResponse)
    async def portal() -> HTMLResponse:
        return HTMLResponse(
            _PORTAL_HTML,
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": (
                    "default-src 'self'; style-src 'unsafe-inline'; "
                    "script-src 'unsafe-inline'; object-src 'none'"
                ),
            },
        )

    @application.get("/contracts/{po_number}.pdf")
    async def contract(po_number: str) -> Response:
        normalized = po_number.strip().upper()
        if normalized != SUCCESS_PO:
            raise HTTPException(status_code=404, detail="Contract not found")
        return Response(
            _contract_pdf(normalized),
            media_type="application/pdf",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{normalized}-contract.pdf"'
                ),
                "Cache-Control": "no-store",
            },
        )

    return application


app = create_mock_srm_app()
