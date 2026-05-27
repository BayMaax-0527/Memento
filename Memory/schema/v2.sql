-- memory-vault v2 统一 Schema
-- 全局库和专属库共用同一套表结构，靠 source_type 区分来源

-- 主表：所有域共用
CREATE TABLE IF NOT EXISTS abstracts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,         -- 'memory' / 'knowledge'
    abstract TEXT NOT NULL,             -- L0 摘要
    category TEXT,                      -- 大类（项目开发/薪酬分析/...）
    tags TEXT,                          -- 逗号分隔标签
    weight INTEGER DEFAULT 100,        -- 排序权重
    global_hit_ratio REAL DEFAULT 0.5, -- 长期命中率
    session_count INTEGER DEFAULT 0,   -- 经历的会话周期数
    last_accessed_at TEXT,             -- 最后命中时间
    created_at TEXT DEFAULT (datetime('now','localtime')),
    storage_path TEXT,                   -- 指向 L1/L2 文件路径
    version INTEGER DEFAULT 1,          -- topic 版本号
    status TEXT DEFAULT 'active',        -- active / downgraded / superseded
    superseded_by_id INTEGER DEFAULT NULL  -- 被哪个新版本覆盖
);

-- FTS5 全文索引
CREATE VIRTUAL TABLE IF NOT EXISTS abstracts_fts USING fts5(
    abstract, category, tags,
    content='abstracts',
    content_rowid='id'
);

-- 主表 → FTS5 自动同步触发器
CREATE TRIGGER IF NOT EXISTS abstracts_ai AFTER INSERT ON abstracts BEGIN
    INSERT INTO abstracts_fts(rowid, abstract, category, tags)
    VALUES (new.id, new.abstract, new.category, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS abstracts_ad AFTER DELETE ON abstracts BEGIN
    INSERT INTO abstracts_fts(abstracts_fts, rowid, abstract, category, tags)
    VALUES ('delete', old.id, old.abstract, old.category, old.tags);
END;

CREATE TRIGGER IF NOT EXISTS abstracts_au AFTER UPDATE ON abstracts BEGIN
    INSERT INTO abstracts_fts(abstracts_fts, rowid, abstract, category, tags)
    VALUES ('delete', old.id, old.abstract, old.category, old.tags);
    INSERT INTO abstracts_fts(rowid, abstract, category, tags)
    VALUES (new.id, new.abstract, new.category, new.tags);
END;

-- 记忆域扩展（source_type='memory' 时有值）
CREATE TABLE IF NOT EXISTS memory_meta (
    abstract_id INTEGER PRIMARY KEY,
    profile TEXT,                    -- default / work / private
    profile_tag TEXT,                -- 工作 / 个人
    project TEXT,                    -- hr-efficiency / payroll / xiaohongshu
    project_type TEXT,               -- 系统开发 / 工具配置 / 数据分析 / 内容创作
    decision_type TEXT,              -- 决策 / 踩坑 / 经验 / 待办
    importance TEXT,                 -- high / medium / low
    topic TEXT DEFAULT '',           -- 版本覆盖主题键
    FOREIGN KEY (abstract_id) REFERENCES abstracts(id) ON DELETE CASCADE
);

-- 知识域扩展（source_type='knowledge' 时有值）
CREATE TABLE IF NOT EXISTS knowledge_meta (
    abstract_id INTEGER PRIMARY KEY,
    brand_name TEXT,                 -- 珀莱雅 / 欧莱雅（具体品牌）
    brand_tier TEXT,                 -- 国货美妆 / 国际大牌（品牌归类）
    category_l1 TEXT,                -- 美妆护肤 / 食品饮料（一级类目）
    category_l2 TEXT,                -- 面霜 / 乳制品（细分 / 归类）
    target_audience TEXT,            -- 敏感肌 / 减脂人群（具体人群）
    audience_tag TEXT,               -- 成分党 / 职场白领（人群归类）
    FOREIGN KEY (abstract_id) REFERENCES abstracts(id) ON DELETE CASCADE
);

-- 记忆关联表
CREATE TABLE IF NOT EXISTS memory_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,         -- 新记忆的 abstract_id
    target_abstract_id INTEGER NOT NULL,-- 关联目标的 abstract_id
    relation TEXT DEFAULT 'related',    -- related / duplicate / superseded
    created_at TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (source_id) REFERENCES abstracts(id) ON DELETE CASCADE,
    FOREIGN KEY (target_abstract_id) REFERENCES abstracts(id) ON DELETE CASCADE
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_abstracts_source_type ON abstracts(source_type);
CREATE INDEX IF NOT EXISTS idx_abstracts_category ON abstracts(category);
CREATE INDEX IF NOT EXISTS idx_abstracts_weight ON abstracts(weight DESC);
CREATE INDEX IF NOT EXISTS idx_memory_meta_project ON memory_meta(project);
CREATE INDEX IF NOT EXISTS idx_memory_meta_decision ON memory_meta(decision_type);
CREATE INDEX IF NOT EXISTS idx_knowledge_meta_brand ON knowledge_meta(brand_name);
CREATE INDEX IF NOT EXISTS idx_knowledge_meta_brand_tier ON knowledge_meta(brand_tier);
CREATE INDEX IF NOT EXISTS idx_knowledge_meta_audience_tag ON knowledge_meta(audience_tag);
CREATE INDEX IF NOT EXISTS idx_knowledge_meta_category_l2 ON knowledge_meta(category_l2);
CREATE INDEX IF NOT EXISTS idx_memory_meta_project_type ON memory_meta(project_type);
CREATE INDEX IF NOT EXISTS idx_memory_links_source ON memory_links(source_id);

-- 全局决策表
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE,
    category TEXT DEFAULT '',
    content TEXT NOT NULL,
    first_seen TEXT DEFAULT (datetime('now','localtime')),
    last_updated TEXT DEFAULT (datetime('now','localtime')),
    source_sessions TEXT DEFAULT '[]'
);
