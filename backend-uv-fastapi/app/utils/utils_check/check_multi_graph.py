# app/utils/utils_check/check_multi_graph.py —— 开发期脚本：用假模型离线验证多文件图（不调 API、不花 token）
# 运行：uv run python -m app.utils.utils_check.check_multi_graph

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from app.agents.common import GenerationFailedError, ModelUsage
from app.agents.multi_file_graph import SitePlan, build_multi_file_graph, generate_multi_file

# 合格输出：有 viewport、有两条引用、三个文件都不算短
GOOD_OUTPUT = """### index.html
```html
<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="style.css">
</head><body><h1>hi</h1><script src="script.js"></script></body></html>
```

### style.css
```css
body { margin: 0; font-family: sans-serif; color: #eee; background: #0f172a; }
h1 { color: #4f46e5; letter-spacing: 2px; }
```

### script.js
```js
document.addEventListener('DOMContentLoaded', () => {
  console.log('ready');
});
```
"""

# 不合格输出：只给了一个 html，且缺 viewport、缺两条引用
BAD_OUTPUT = """### index.html
```html
<!DOCTYPE html>
<html><body><h1>没有引用、没有 viewport 的页面</h1></body></html>
```
"""

FAKE_PLAN = SitePlan(site_title="测试站", style_hint="深色", html_outline="一个 h1")

# 假用量：规划一次 10/20，生成一次 100/200（其中思考 150）
# 有了它才能验证"多次调用的用量会被累加"（尤其是重试那一轮）
PLAN_USAGE = {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}
GEN_USAGE = {
    "input_tokens": 100,
    "output_tokens": 200,
    "total_tokens": 300,
    "output_token_details": {"reasoning": 150},
}


def fake_planner() -> RunnableLambda:
    """假规划链。

    ⚠️ 它的返回值必须和**真货同构**：真实 planner 是
    ``llm_structured_client.with_structured_output(SitePlan, include_raw=True)``，
    返回的是 ``{"raw": AIMessage, "parsed": SitePlan, "parsing_error": ...}`` 这个字典。
    如果这里直接返回 SitePlan 对象，plan_node 里的 ``result.get("raw")`` 就会炸 ——
    假货和真货长得不一样，测试就是在骗自己。

    Returns:
        假规划链。
    """
    message = AIMessage(content="", usage_metadata=PLAN_USAGE)
    return RunnableLambda(lambda _: {"raw": message, "parsed": FAKE_PLAN, "parsing_error": None})


def failing_planner() -> RunnableLambda:
    """永远抛异常的假规划链，用来验证"规划失败降级"。

    Returns:
        假规划链。
    """

    def _boom(_):
        raise RuntimeError("模拟规划失败")

    return RunnableLambda(_boom)


def fake_model(outputs: list[str]) -> RunnableLambda:
    """按调用次序依次返回 outputs 的假模型；用完之后一直返回最后一个。

    Args:
        outputs: 每次调用要返回的"模型原文"。

    Returns:
        假模型（可直接当 Runnable 用）。
    """
    box = {"index": 0}

    def _next(_messages):
        index = min(box["index"], len(outputs) - 1)
        box["index"] += 1
        return AIMessage(content=outputs[index], usage_metadata=GEN_USAGE)

    return RunnableLambda(_next)


def run(outputs: list[str], planner: RunnableLambda) -> dict:
    """用指定的假模型 / 假规划跑一遍图。

    Args:
        outputs: 假模型依次返回的原文。
        planner: 假规划链。

    Returns:
        图跑完后的最终 state。
    """
    graph = build_multi_file_graph(model=fake_model(outputs), planner=planner)
    return graph.invoke(
        {"prompt": "做个测试页", "attempts": 0, "usage": ModelUsage()},
        config={"recursion_limit": 25},
    )


def show(label: str, state: dict) -> None:
    """打印一行结果（含累计用量）。

    Args:
        label: 场景名。
        state: 最终 state。
    """
    usage = state["usage"]
    print(
        f"{label} {sorted(state['contents'])} | attempts={state['attempts']} "
        f"| errors={len(state['errors'])} "
        f"| usage(in/out/reasoning)={usage.input_tokens}/{usage.output_tokens}/{usage.reasoning_tokens}"
    )


def main() -> None:
    """跑完全部场景。

    用量可以通过算术核对：
      ① plan(10/20) + generate(100/200)            = 110/220/150
      ② plan(10/20) + generate × 2(100/200 各一次) = 210/420/300   ← 重试也被记账
      ④ 规划失败（无 plan 用量） + generate        = 100/200/150
    """
    show("① 一次成功     :", run([GOOD_OUTPUT], fake_planner()))
    show("② 重试后成功   :", run([BAD_OUTPUT, GOOD_OUTPUT], fake_planner()))
    show("③ 一直不合格   :", run([BAD_OUTPUT], fake_planner()))
    show("④ 规划失败降级 :", run([GOOD_OUTPUT], failing_planner()))

    try:
        generate_multi_file("做个测试页", model=fake_model([BAD_OUTPUT]), planner=fake_planner())
        print("⑤ 入口未抛错   : ✗ 不符合预期")
    except GenerationFailedError as error:
        print("⑤ 入口抛错 ✓   :", str(error)[:50], "| 异常带用量 =", error.usage)


if __name__ == "__main__":
    main()
