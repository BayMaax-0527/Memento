# Changelog

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
