-- ==========================================
-- v2 记忆生命周期管理系统 — 数据库迁移
-- 运行于 profile 库和全局库的 abstracts.db
-- ==========================================

-- 1. abstracts 表：新增生命周期字段

-- 引用追踪
ALTER TABLE abstracts ADD COLUMN last_ref_session TEXT;
ALTER TABLE abstracts ADD COLUMN hit_count INTEGER DEFAULT 0;
ALTER TABLE abstracts ADD COLUMN sliding_hit_count INTEGER DEFAULT 0;

-- 时效系数 [0, 1]，基于最后命中时间和衰减窗口
ALTER TABLE abstracts ADD COLUMN freshness_score REAL DEFAULT 1.0;

-- 生命周期分类：'daily'(30天) / 'arch'(180天) / 'rule'(INF不衰减)
ALTER TABLE abstracts ADD COLUMN lifecycle_category TEXT DEFAULT 'daily';

-- 2. memory_links 表：新增跳转字段
ALTER TABLE memory_links ADD COLUMN redirect_to INTEGER;

-- 3. 冷启动初始化：所有现有 L0 从零基线开始
UPDATE abstracts SET
  last_accessed_at   = COALESCE(last_accessed_at, datetime('now','localtime')),
  hit_count          = 1,
  sliding_hit_count  = 1,
  freshness_score    = 1.0,
  status             = COALESCE(NULLIF(status, ''), 'active'),
  lifecycle_category = CASE
    WHEN abstract LIKE '%架构%' OR abstract LIKE '%设计%'
         OR abstract LIKE '%方案%' OR abstract LIKE '%architecture%'
         OR abstract LIKE '%design%'
    THEN 'arch'
    WHEN abstract LIKE '%流程%' OR abstract LIKE '%规范%'
         OR abstract LIKE '%必须%' OR abstract LIKE '%禁止%'
         OR abstract LIKE '%规则%' OR abstract LIKE '%policy%'
         OR abstract LIKE '%rule%'
    THEN 'rule'
    ELSE 'daily'
  END
WHERE source_type = 'memory';
