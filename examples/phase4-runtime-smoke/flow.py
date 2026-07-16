async def run(ctx):
    message = ctx.input["message"]
    await ctx.page.set_content(
        f"<html><body><h1 id='result'>{message}</h1></body></html>"
    )
    visible = await ctx.page.locator(ctx.selectors["result"]).inner_text()
    if visible != message:
        raise RuntimeError("Rendered message did not match input")
    await ctx.artifacts.screenshot("phase4-runtime-result")
    await ctx.events.emit("PHASE4_FLOW_COMPLETED", message="Phase 4 Flow completed")
