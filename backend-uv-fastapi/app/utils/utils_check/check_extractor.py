# app/utils/utils_check/check_extractor.py —— 开发期脚本：验证代码块抽取与安全落盘（不调用模型，不花 token）
# 运行：uv run python -m app.utils.utils_check.check_extractor

import shutil

from app.utils.weg_gen.code_extractor import (
    CodeExtractError,
    extract_multi_files,
    extract_single_html,
)
from app.utils.weg_gen.file_writer import UnsafeFileNameError, task_dir, write_files

# 模拟一段"规范"的模型输出：标题行 + 三个标准围栏
# 注意：每个代码块都必须有**收尾围栏**（单独一行 ```，不带语言标记），
#       否则抽取器无法判断块的边界 —— 这份数据曾经漏掉全部收尾围栏，导致 style.css 块丢失
MODEL_OUTPUT = """我为你生成了三个文件。

### index.html
```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <button id="btn">点击</button>
  <script src="script.js"></script>
</body>
</html>
```

### style.css
```css
body { font-family: sans-serif; }
button { padding: 8px 16px; }
```

### script.js
```js
let n = 0;
document.getElementById('btn').onclick = () => { n += 1; };
```
"""

# 模拟一段"不守规矩"的输出：
#  - 第一块没写语言标记，只能靠标题行 "### style.css" 识别
#  - 第二块用 javascript 别名（且 ``` 与 js 之间多了个空格）
#  - 第三块用 html 标记
ALT_OUTPUT = """### style.css
```
body { color: red; }
```

### script.js
``` javascript
console.log('hi');
```

```html
<html><body>hi</body></html>
```
"""

# 模拟"模型漏写收尾围栏"的脏输出：
# ```css 带语言标记，按 CommonMark 不可能是收尾围栏，应被当成新的开始围栏
BROKEN_FENCES = """### index.html
```html
<html><body>hi</body></html>
### style.css
```css
body { color: red; }
### script.js
```js
console.log('hi');
"""

TASK_UUID = "testtask0001"


def main() -> None:
    """跑完全部检查项。"""
    files = extract_multi_files(MODEL_OUTPUT)
    print("① multi 提取     :", sorted(files), {k: len(v) for k, v in files.items()})

    print("② single 提取    :", sorted(extract_single_html(MODEL_OUTPUT)))

    alt = extract_multi_files(ALT_OUTPUT)
    print("③ 别名/标题行回退:", sorted(alt), {k: len(v) for k, v in alt.items()})

    broken = extract_multi_files(BROKEN_FENCES)
    print("④ 漏写收尾围栏   :", sorted(broken), {k: len(v) for k, v in broken.items()})

    rel = write_files(user_id=1, task_uuid=TASK_UUID, files=files)
    directory = task_dir(1, TASK_UUID)
    print("⑤ 落盘           :", rel, "->", sorted(p.name for p in directory.iterdir()))

    try:
        extract_multi_files(MODEL_OUTPUT.split("### script.js")[0])  # 砍掉 js 块
    except CodeExtractError as error:
        print("⑥ 缺块被拦下 ✓   :", error)

    try:
        write_files(user_id=1, task_uuid=TASK_UUID, files={"../../evil.html": "x"})
    except UnsafeFileNameError as error:
        print("⑦ 非法名被拦下 ✓ :", error)

    shutil.rmtree(directory, ignore_errors=True)
    print("清理完成")


if __name__ == "__main__":
    main()
