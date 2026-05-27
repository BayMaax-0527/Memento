-- L0: 摘要索引 (SQLite FTS5 全文检索)
CREATE VIRTUAL TABLE memories_fts USING fts5(
    abstract,
    tags,
    content='abstracts',
    content_rowid='id'
);

CREATE TABLE abstracts (
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'session',
    abstract TEXT NOT NULL,
    tags TEXT,
    l1_path TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

-- 触发器：自动同步 FTS5 索引
CREATE TRIGGER abstracts_ai AFTER INSERT ON abstracts BEGIN
    INSERT INTO memories_fts(rowid, abstract, tags) VALUES (new.id, new.abstract, new.tags);
END;

CREATE TRIGGER abstracts_ad AFTER DELETE ON abstracts BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, abstract, tags) VALUES ('delete', old.id, old.abstract, old.tags);
END;

CREATE TRIGGER abstracts_au AFTER UPDATE ON abstracts BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, abstract, tags) VALUES ('delete', old.id, old.abstract, old.tags);
    INSERT INTO memories_fts(rowid, abstract, tags) VALUES (new.id, new.abstract, new.tags);
END;

-- 全局决策/结论（语义去重，slug UNIQUE 兜底）
CREATE TABLE decisions (
    id INTEGER PRIMARY KEY,
    slug TEXT UNIQUE,
    category TEXT,
    content TEXT,
    first_seen TEXT DEFAULT (datetime('now')),
    last_updated TEXT DEFAULT (datetime('now')),
    source_sessions TEXT DEFAULT '[]'
);
