"""最小预测契约示例；仅通过 OpenResearch 计划在沙箱内运行。"""

import json
import os
import sys
from pathlib import Path

# 输入只包含声明的特征，标签由评估端保存。
rows = json.loads(Path(os.environ["OPENRESEARCH_INPUT"]).read_text())
predictions = [
    {"id": row["id"], "prediction": "yes" if int(row["inputs"]["x"]) % 2 else "no"} for row in rows
]
if "--disable-parity" in sys.argv:
    predictions = [{"id": row["id"], "prediction": "yes"} for row in rows]
Path(os.environ["OPENRESEARCH_PREDICTIONS"]).write_text(json.dumps(predictions))
print(f"predicted {len(predictions)} samples")
