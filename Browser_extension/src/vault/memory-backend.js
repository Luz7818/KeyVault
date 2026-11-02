// 内存后端：store.js 的存储接口的最小实现。
// 用途：node 单测（无 IndexedDB）；以及未来「换浏览器/导出调试」的参考实现。
// 接口即契约——db.js（IndexedDB 版）必须实现同一组方法。

export function memoryBackend() {
  const meta = new Map();
  const secrets = new Map();   // id -> record
  let nextSecretId = 1;
  const fingerprints = [];     // {id, sha256, secretId, status, firstSeenAt, revokedAt}
  const audit = [];            // 追加；auditLast 取末尾
  let nextFpId = 1;
  let nextAuditId = 1;
  const corrections = new Map();
  let nextCorrId = 1;

  return {
    async metaGet(key) { return meta.get(key) ?? null; },
    async metaSet(key, value) { meta.set(key, value); },

    async secretAll() { return [...secrets.values()].map((r) => ({ ...r })); },
    async secretGet(id) {
      const r = secrets.get(id);
      return r ? { ...r } : null;
    },
    async secretBySha256(sha) {
      const r = [...secrets.values()].find((x) => x.sha256 === sha);
      return r ? { ...r } : null;
    },
    async secretPut(record) {
      if (record.id === null || record.id === undefined) {
        record = { ...record, id: nextSecretId++ };
      }
      secrets.set(record.id, { ...record });
      return record.id;
    },
    async secretDelete(id) { secrets.delete(id); },

    async fpPut(row) {
      if (row.id !== undefined) {
        const index = fingerprints.findIndex((f) => f.id === row.id);
        if (index >= 0) { fingerprints[index] = { ...row }; return; }
      }
      fingerprints.push({ ...row, id: nextFpId++ });
    },
    async fpListBySecret(secretId) {
      return fingerprints.filter((f) => f.secretId === secretId).map((f) => ({ ...f }));
    },

    async auditLast() {
      return audit.length ? { ...audit[audit.length - 1] } : null;
    },
    async auditAppend(row) { audit.push({ ...row, id: nextAuditId++ }); },
    async auditList(limit = 100) {
      return audit.slice(-limit).reverse().map((r) => ({ ...r }));
    },

    async corrAll() {
      return [...corrections.values()].map((r) => ({ ...r }));
    },
    async corrPut(row) {
      if (row.id === undefined) row = { ...row, id: nextCorrId++ };
      corrections.set(row.id, { ...row });
      return row.id;
    },
    async corrDelete(id) { corrections.delete(id); },
  };
}
