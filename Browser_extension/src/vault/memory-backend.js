// 内存后端：IBackend（CONTRACTS.md C2）的最小实现，与 db.js（IndexedDB 版）同接口。
//
// 用途：node 单测（node 无 indexedDB，已声明边界）。
// 语义对齐点：
// - secretPut 在 id 为 null/undefined 时自增分配，否则按 id 覆盖（upsert）；
// - fpPut 有 id 则更新、无则追加；
// - auditAppend 只追加（append-only 由接口形状保证，没有 update/delete 通道）；
// - corrPut 无 id 时按 (kind, pattern) upsert——对齐桌面 insert_correction 的
//   ON CONFLICT(kind, pattern) DO UPDATE SET platform/note（repo.py:412-421）；
// - 全部读路径返回拷贝，调用方改返回值不会渗回存储。
//
// clearAll() 是 C1#30 reset 的存储侧接缝：内存形态等价于「删库」，把全部
// Map/数组复位。_audit 是测试篡改通道（直接改内部数组，等价桌面 DROP TRIGGER
// 后改行的做法）——仅测试用，业务代码不许碰。

function clone(value) {
  return structuredClone(value);
}

export function memoryBackend() {
  const meta = new Map();
  const secrets = new Map(); // id -> record
  let nextSecretId = 1;
  const fingerprints = [];   // {id, sha256, secretId, status, firstSeenAt, revokedAt, [valueIv, valueCt]}
  let nextFpId = 1;
  const audit = [];          // 追加；auditLast 取末尾
  let nextAuditId = 1;
  const corrections = new Map();
  let nextCorrId = 1;

  const backend = {
    async metaGet(key) {
      return meta.has(key) ? clone(meta.get(key)) : null;
    },

    async metaSet(key, value) {
      meta.set(key, clone(value));
    },

    async secretAll() {
      return [...secrets.values()].map(clone);
    },

    async secretGet(id) {
      const record = secrets.get(id);
      return record ? clone(record) : null;
    },

    async secretBySha256(sha) {
      for (const record of secrets.values()) {
        if (record.sha256 === sha) return clone(record);
      }
      return null;
    },

    async secretPut(record) {
      let id = record.id;
      if (id === null || id === undefined) id = nextSecretId++;
      secrets.set(id, clone({ ...record, id }));
      return id;
    },

    async secretDelete(id) {
      secrets.delete(id);
    },

    async fpPut(row) {
      if (row.id !== undefined && row.id !== null) {
        const index = fingerprints.findIndex((f) => f.id === row.id);
        if (index >= 0) {
          fingerprints[index] = clone(row);
          return row.id;
        }
      }
      const stored = clone({ ...row, id: nextFpId++ });
      fingerprints.push(stored);
      return stored.id;
    },

    async fpListBySecret(secretId) {
      return fingerprints.filter((f) => f.secretId === secretId).map(clone);
    },

    /** 级联删除某记录的全部指纹行（桌面 schema.sql ON DELETE CASCADE +
     * db.py foreign_keys=ON 同语义；含轮换换下、带旧密文的吊销行）。返回行数。 */
    async fpDeleteBySecret(secretId) {
      let removed = 0;
      for (let i = fingerprints.length - 1; i >= 0; i--) {
        if (fingerprints[i].secretId === secretId) {
          fingerprints.splice(i, 1);
          removed += 1;
        }
      }
      return removed;
    },

    async auditLast() {
      return audit.length ? clone(audit[audit.length - 1]) : null;
    },

    async auditAppend(row) {
      const stored = clone({ ...row, id: nextAuditId++ });
      audit.push(stored);
      return stored.id;
    },

    async auditList(limit = 100) {
      return audit.slice(-limit).reverse().map(clone);
    },

    async corrAll() {
      return [...corrections.values()].map(clone);
    },

    async corrPut(row) {
      if (row.id !== undefined && row.id !== null) {
        corrections.set(row.id, clone(row));
        return row.id;
      }
      for (const existing of corrections.values()) {
        if (existing.kind === row.kind && existing.pattern === row.pattern) {
          const merged = clone({ ...existing, platform: row.platform, note: row.note });
          corrections.set(existing.id, merged);
          return existing.id;
        }
      }
      const stored = clone({ ...row, id: nextCorrId++ });
      corrections.set(stored.id, stored);
      return stored.id;
    },

    async corrDelete(id) {
      corrections.delete(id);
    },

    /** C1#30 reset 的存储侧：整库复位（IndexedDB 版等价于 deleteDatabase）。 */
    async clearAll() {
      meta.clear();
      secrets.clear();
      fingerprints.length = 0;
      audit.length = 0;
      corrections.clear();
      nextSecretId = 1;
      nextFpId = 1;
      nextAuditId = 1;
      nextCorrId = 1;
    },
  };

  // 测试篡改通道：audit 数组的活引用（auditList 返回的是拷贝，改不到真身）。
  backend._audit = audit;
  return backend;
}
