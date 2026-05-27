#!/bin/bash
# setup-profile-vault.sh — 新建 profile 时初始化专属记忆库
# 用法: bash setup-profile-vault.sh <profile_name>
#
# 功能:
#   1. 创建 profile 专属 memory-vault 目录
#   2. 初始化 abstracts.db（含 FTS5 + memory_meta）
#   3. 注册到全局 categories/

set -euo pipefail

if [ $# -lt 1 ]; then
    echo "用法: $0 <profile_name>"
    exit 1
fi

PROFILE="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

PROFILE_DIR="$HOME/.hermes/profiles/$PROFILE/memory-vault"
GLOBAL_VAULT="$HOME/workspace/.global-memory-vault"
SCHEMA="$PROJECT_DIR/schema/v2.sql"

# 1. 创建目录
mkdir -p "$PROFILE_DIR"/{overviews/sessions,storage/sessions}
echo "✅ 目录已创建: $PROFILE_DIR"

# 2. 初始化 DB
if [ -f "$SCHEMA" ]; then
    sqlite3 "$PROFILE_DIR/abstracts.db" < "$SCHEMA"
    echo "✅ 数据库已初始化: abstracts.db (+ FTS5 + memory_meta)"
else
    echo "❌ Schema 文件不存在: $SCHEMA"
    exit 1
fi

# 3. 注册到全局 categories/
if [ -d "$GLOBAL_VAULT/categories" ]; then
    for cat_file in "$GLOBAL_VAULT"/categories/*.md; do
        [ -f "$cat_file" ] || continue
        echo "- $PROFILE: (新建, 暂无记忆)" >> "$cat_file"
    done
    echo "✅ 已注册到全局 categories/"
else
    echo "⚠️  全局 categories/ 不存在，跳过注册"
fi

echo ""
echo "🎉 专属记忆库初始化完成: $PROFILE_DIR"
