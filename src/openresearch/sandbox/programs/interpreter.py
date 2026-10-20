# OpenResearch interpreter probe
"""可信解释器探测，只输出实际容器元数据，不加载或安装实验依赖。"""

import json
import platform

print(
    json.dumps(
        {
            "python_full_version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "system": platform.system(),
            "machine": platform.machine(),
            "libc": platform.libc_ver()[0],
            "libc_version": platform.libc_ver()[1],
        }
    )
)
