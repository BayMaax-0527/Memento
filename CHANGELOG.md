# Changelog

## v4.0 (2026-05-28)

### ✨ 新功能
- **语义去重**：注入时嵌入向量比对，≥0.85 合并同类，0.65~0.85+否定词 supersede
- **超时衰减**：freshness_score 按 daily(30天)/arch(180天)/rule(永不衰减) 三类窗口衰减
- **时效标签**：active → stale → archived 自动生命周期状态转移
- **引用计数**：hit_count + sliding_hit_count + freshness_score 多维排序
- **定期压缩**：auto.py compact 深度归档 + 健康报告
- **恢复命令**：auto.py recover --id/--query/list-archived
- **检索排序**：生命周期感知排序公式，superseded/archived 默认隐藏

### 🔧 改进
- 检索过滤从 `status != 'superseded'` 改为 `status IN ('active', 'downgraded', 'stale')`
- `_sync_to_global()` LIKE 模糊匹配兜底，失败日志记录
- 跨 profile 引用计数通过 global_id_map 转换
- 配置参数集中到 config/lifecycle.yaml，不硬编码

### 🐛 修复
- 排序公式 freshness 权重过低 (×1→×10)
- `archive_freshness_zero_days` 配置未生效（硬编码 60 天）
- retriever.py 缺少 `import requests` 导致健康检查静默失败
- `stale_miss_count` 加载后未使用
- `recover_by_id` 解除 superseded_by 条件过窄
- compact 链接清理可能影响正常关联

### 📚 文档
- 新增 ReadMe/DESIGN.md：完整架构设计文档
- 新增 ReadMe/CHANGELOG-v2-lifecycle.md：升级对比文档
- 新增 Memory/config/lifecycle.yaml：生命周期参数配置
- 新增 Engine/migrations/v2_lifecycle.sql：数据库迁移脚本

---

## v3.0 (2026-05-27)

### ✨ 新功能
- **二击保险版本管理**：同 topic 注入时，第一次降级为 `downgraded`，第二次确认后才 `superseded`
- **图谱推理**：tag 重叠关联 + 显式关联 + 2 跳 BFS 传递闭包
- **LLM Rerank**：`--rerank` 参数对搜索结果重排序
- **统一客户端**：`client.py` 封装所有 LLM/Embedding 调用，支持 LM Studio / DeepSeek / OpenAI
- **配置向导**：`setup.py` 交互式配置生成
- **独立 profile vault**：为 Hermes 5 个 profile 都创建了独立的记忆库
- **双库图谱同步**：`memory_links` 同时写入全局库和 profile 库

### 🔧 改进
- 全部 `subprocess curl` 替换为 `requests`（跨平台兼容）
- 配置扁平化：支持 provider 继承（compress 默认 = main）
- 路径相对化：`resolve_path()` 自动解析，任意位置解压即用
- `soft_dedup` 补入全局库写入（之前只存在于 profile 库）
- 语义搜索 numpy 向量化加速

### 🐛 修复
- `profile_vault_base` 为空时静默 fallback 导致重复 L0 → 改为显式报错
- `write_global_l0()` 缺少内容去重导致重复写入
- `tag_overlap` 自环 Bug（source_id = target_abstract_id）
- profile 库缺少 version/status/topic 列

---

## v2.0 (2026-05-25)

- 首次重构：L0/L1/L2 三级存储，双源隔离（session/doc）
- 双模型管道：DeepSeek 压缩 + LM Studio 提取
- FTS5 全文检索 + 中文 LIKE 兜底

---

## v1.0 (2026-05-24)

- 初始版本，替代完全移除的 Hindsight
- 单级存储，纯 JSON 配置
