-- KeyVault 全部 DDL。PRAGMA 由 db.py 逐连接设置（foreign_keys 是每连接的，
-- journal_mode 是持久的），这里只放结构。
--
-- 设计要点：
--   * 只有 secret.value_blob / fingerprint.value_blob / inbox.value_blob /
--     binding.canary_blob 是 DPAPI 密文。其余全部明文可搜索 —— 于是 list /
--     search / scan / 网页 / audit 从不需要调 CryptUnprotectData。
--   * mask_head / mask_tail 是**存下来的**，不是推导的，而且存的是「头尾两段原文」
--     而不是合成好的掩码串。于是掩码字符成为纯显示偏好 —— 改 mask_char 配置立刻
--     生效，永远不需要重算全库。存合成串的话，改配置会静默什么都不做。
--     list / scan / 网页 / audit 因此从不需要调 CryptUnprotectData。
--     value_len 同理单独存。
--   * audit 的追加式由触发器强制，不靠约定；chain_hash 让删行即使有人 drop 掉
--     触发器也能被发现。

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- DPAPI 绑定金丝雀。开库时试解密它；失败即「这个 vault 是在另一个 Windows
-- 账户下建的，blob 不能迁移」。没有它，症状是一屏 CryptUnprotectData failed (13)
-- 且完全不知道为什么。
CREATE TABLE IF NOT EXISTS binding (
    id           INTEGER PRIMARY KEY CHECK (id = 1),
    canary_blob  BLOB NOT NULL,
    canary_text  TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS secret (
    id                INTEGER PRIMARY KEY,
    name              TEXT NOT NULL UNIQUE COLLATE NOCASE,
    platform          TEXT NOT NULL,
    confidence        TEXT NOT NULL CHECK (confidence IN
                        ('manual','exact','high','medium','ambiguous','none')),
    evidence          TEXT NOT NULL DEFAULT '',
    detect_source     TEXT NOT NULL DEFAULT 'none',
    key_name          TEXT,
    kind              TEXT NOT NULL DEFAULT 'token' CHECK (kind IN
                        ('token','json','pem','pair','connstring','other')),
    value_blob        BLOB NOT NULL,
    sha256            TEXT NOT NULL UNIQUE,
    mask_head         TEXT NOT NULL DEFAULT '',
    mask_tail         TEXT NOT NULL DEFAULT '',
    value_len         INTEGER NOT NULL,
    status            TEXT NOT NULL DEFAULT 'active' CHECK (status IN
                        ('active','revoked','expired')),
    note              TEXT NOT NULL DEFAULT '',
    source_url        TEXT NOT NULL DEFAULT '',
    origin            TEXT NOT NULL DEFAULT 'manual',
    extra_json        TEXT NOT NULL DEFAULT '{}',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    expires_at        TEXT,
    last_used_at      TEXT,
    last_revealed_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_secret_platform ON secret(platform);
CREATE INDEX IF NOT EXISTS idx_secret_status   ON secret(status);
CREATE INDEX IF NOT EXISTS idx_secret_expires  ON secret(expires_at)
    WHERE expires_at IS NOT NULL;

-- 这条记录**曾经持有过的每一个值**。独立于 secret 是因为 rotate 必须保留旧
-- sha256，而 scan 必须匹配包括已吊销在内的全部历史 —— 一把吊销的 key 躺在
-- git 历史里仍然是泄漏路径的证据。
CREATE TABLE IF NOT EXISTS fingerprint (
    id            INTEGER PRIMARY KEY,
    secret_id     INTEGER NOT NULL REFERENCES secret(id) ON DELETE CASCADE,
    sha256        TEXT NOT NULL UNIQUE,
    mask_head     TEXT NOT NULL DEFAULT '',
    mask_tail     TEXT NOT NULL DEFAULT '',
    value_len     INTEGER NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('active','revoked','expired')),
    value_blob    BLOB,
    first_seen_at TEXT NOT NULL,
    revoked_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_fingerprint_secret ON fingerprint(secret_id);
CREATE INDEX IF NOT EXISTS idx_fingerprint_status ON fingerprint(status);

CREATE TABLE IF NOT EXISTS alias (
    id        INTEGER PRIMARY KEY,
    secret_id INTEGER NOT NULL REFERENCES secret(id) ON DELETE CASCADE,
    name      TEXT NOT NULL UNIQUE COLLATE NOCASE
);

CREATE INDEX IF NOT EXISTS idx_alias_secret ON alias(secret_id);

-- 真多对多，不是分隔字符串：kv list --tag prod 必须是索引 join。
-- LIKE '%prod%' 会同时匹配到 production，还要在 Python 侧过滤。
CREATE TABLE IF NOT EXISTS tag (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);

CREATE TABLE IF NOT EXISTS secret_tag (
    secret_id INTEGER NOT NULL REFERENCES secret(id) ON DELETE CASCADE,
    tag_id    INTEGER NOT NULL REFERENCES tag(id)    ON DELETE CASCADE,
    PRIMARY KEY (secret_id, tag_id)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_secret_tag_tag ON secret_tag(tag_id);

CREATE TABLE IF NOT EXISTS correction (
    id         INTEGER PRIMARY KEY,
    kind       TEXT NOT NULL CHECK (kind IN ('exact','keyname','hostname','window','group')),
    pattern    TEXT NOT NULL,
    platform   TEXT NOT NULL,
    note       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE (kind, pattern)
);

-- secret_id 刻意**不设外键**：审计行必须活得比记录本身久。
-- 只存 12 位 sha256 前缀，绝不存完整指纹 —— 审计日志是最可能被导出或粘进
-- bug 报告的东西。
CREATE TABLE IF NOT EXISTS audit (
    id            INTEGER PRIMARY KEY,
    ts            TEXT NOT NULL,
    event         TEXT NOT NULL,
    actor         TEXT NOT NULL,
    secret_id     INTEGER,
    name_snapshot TEXT,
    sha256_prefix TEXT,
    detail_json   TEXT NOT NULL DEFAULT '{}',
    chain_hash    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_ts     ON audit(ts);
CREATE INDEX IF NOT EXISTS idx_audit_event  ON audit(event);
CREATE INDEX IF NOT EXISTS idx_audit_secret ON audit(secret_id);

CREATE TRIGGER IF NOT EXISTS audit_no_update
BEFORE UPDATE ON audit
BEGIN
    SELECT RAISE(ABORT, 'audit log is append-only');
END;

CREATE TRIGGER IF NOT EXISTS audit_no_delete
BEFORE DELETE ON audit
BEGIN
    SELECT RAISE(ABORT, 'audit log is append-only');
END;

-- 存指纹，**绝不存值**。擦除 helper 只需要比对指纹，它根本不需要机密。
CREATE TABLE IF NOT EXISTS pending_wipe (
    id          INTEGER PRIMARY KEY,
    sha256      TEXT NOT NULL,
    secret_id   INTEGER,
    deadline_at TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','done','skipped'))
);

CREATE INDEX IF NOT EXISTS idx_pending_wipe_status ON pending_wipe(status);

-- 仅 kv watch --spool 使用。剪切板衍生内容静息状态永不明文。
CREATE TABLE IF NOT EXISTS inbox (
    id              INTEGER PRIMARY KEY,
    sha256          TEXT NOT NULL,
    mask_head       TEXT NOT NULL DEFAULT '',
    mask_tail       TEXT NOT NULL DEFAULT '',
    value_len       INTEGER NOT NULL,
    value_blob      BLOB NOT NULL,
    kind            TEXT NOT NULL DEFAULT 'token',
    key_name        TEXT,
    platform        TEXT NOT NULL,
    confidence      TEXT NOT NULL,
    evidence        TEXT NOT NULL DEFAULT '',
    candidates_json TEXT NOT NULL DEFAULT '[]',
    suggested_name  TEXT NOT NULL DEFAULT '',
    source          TEXT NOT NULL DEFAULT 'clipboard',
    context_json    TEXT NOT NULL DEFAULT '{}',
    captured_at     TEXT NOT NULL,
    ttl_until       TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','accepted','rejected','expired')),
    decided_at      TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_inbox_pending ON inbox(sha256) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_inbox_status ON inbox(status);

CREATE TABLE IF NOT EXISTS setting (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
