#!/usr/bin/env python3
"""
hooks/remember_doc.py — v2 文档知识注入入口

当你说"注入知识"时执行：
  ① 保存原始文档到 Knowledge/raw/（重名自动时间戳）
  ② 转 .md（如原是 Excel/PDF → 由我提前转好）
  ③ 调 remember.py --source doc

用法:
    cd ~/workspace/memory-vault && python3 hooks/remember_doc.py <文件路径>
"""

import subprocess, sys, shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
KNOWLEDGE_RAW = ROOT.parent / "Knowledge" / "raw"


def save_to_raw(src_path: Path) -> Path:
    """保存原始文件到 raw/，重名时加时间戳。"""
    KNOWLEDGE_RAW.mkdir(parents=True, exist_ok=True)
    dst = KNOWLEDGE_RAW / src_path.name
    if dst.exists():
        stamp = datetime.now().strftime("%Y%m%d")
        stem = dst.stem
        suffix = dst.suffix
        dst = KNOWLEDGE_RAW / f"{stem}_{stamp}{suffix}"
    shutil.copy2(str(src_path), str(dst))
    print(f"  📁 raw → {dst}", file=sys.stderr)
    return dst


def main():
    if len(sys.argv) < 2:
        print("用法: remember_doc.py <文件路径> [--slug 自定义名称]")
        sys.exit(1)

    file_path = Path(sys.argv[1])
    if not file_path.exists():
        print(f"❌ 文件不存在: {file_path}", file=sys.stderr)
        sys.exit(1)

    extra_args = sys.argv[2:] if len(sys.argv) > 2 else []

    # 01 保存原始文件到 raw/（保留原件，无论格式）
    save_to_raw(file_path)

    # 02 确定待处理文件路径（如已是 .md 直接使用）
    md_path = file_path
    result = subprocess.run(
        ["python3", str(ROOT / "hooks" / "remember.py"),
         "--source", "doc", "--file", str(md_path)] + extra_args,
        capture_output=True, text=True, timeout=600,
    )
    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
