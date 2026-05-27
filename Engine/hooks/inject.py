#!/usr/bin/env python3
"""
hooks/inject.py — 会话启动时的记忆/知识自动注入

用法:
    python3 hooks/inject.py                   输出全部（记忆+知识+决策）
    python3 hooks/inject.py --source session  仅输出记忆
    python3 hooks/inject.py --source doc      仅输出知识
    python3 hooks/inject.py --no-l1           仅输出全局决策

输出格式:
    <memory-context>
    # 已记录的全局决策
    - tech-stack: FastAPI+Vue3+ECharts

    # 💬 记忆
    ## 1. 人效系统：6迭代计划...

    # 📄 知识库
    ## 2. FastAPI 最佳实践...
    </memory-context>
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from retriever import auto_inject, setup_logging
setup_logging()


def main():
    no_l1 = "--no-l1" in sys.argv
    source = None
    if "--source" in sys.argv:
        idx = sys.argv.index("--source")
        if idx + 1 < len(sys.argv):
            source = sys.argv[idx + 1]

    text = auto_inject(source_type=source)

    if not text.strip():
        print("")  # 空输出，skip
        return

    if no_l1:
        # 去除 L1 摘要部分（记忆和知识库标题后的内容都是 L1）
        import re as _re
        text = _re.sub(r"\n# 💬 记忆.*?(?=\n# 📄 知识库|\Z)", "", text, flags=_re.DOTALL)
        text = _re.sub(r"\n# 📄 知识库.*?(?=\Z)", "", text, flags=_re.DOTALL)
        text = text.strip()

    print(f"<memory-context>\n{text}\n</memory-context>")


if __name__ == "__main__":
    main()
