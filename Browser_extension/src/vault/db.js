// IndexedDB 后端 —— memory-backend.js 同一接口的浏览器实现。
// 库名 keyvault；stores：meta / secrets / fingerprints / audit / corrections。
// 单连接惰性打开；MV3 SW 被回收后下次调用重开（IndexedDB 连接随 SW 存活）。

const DB_NAME = "keyvault";
const DB_VERSION = 1;

function openDb() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      db.createObjectStore("meta");
      const secrets = db.createObjectStore("secrets", { keyPath: "id", autoIncrement: true });
      secrets.createIndex("sha256", "sha256", { unique: true });
      secrets.createIndex("name", "name", { unique: true });
      db.createObjectStore("fingerprints", { keyPath: "id", autoIncrement: true });
      const audit = db.createObjectStore("audit", { keyPath: "id", autoIncrement: true });
      audit.createIndex("id", "id");
      const corrections = db.createObjectStore("corrections", { keyPath: "id", autoIncrement: true });
      corrections.createIndex("id", "id");
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

let dbPromise = null;

async function db() {
  if (!dbPromise) {
    dbPromise = openDb().catch((error) => {
      dbPromise = null;
      throw error;
    });
  }
  return dbPromise;
}

function requestAsPromise(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function tx(storeName, mode, fn) {
  const database = await db();
  const transaction = database.transaction(storeName, mode);
  const store = transaction.objectStore(storeName);
  const result = await fn(store);
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve(result);
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error);
  });
}

async function txAll(storeNames, fn) {
  const database = await db();
  const transaction = database.transaction(storeNames, "readwrite");
  const result = await fn(transaction);
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve(result);
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error);
  });
}

function unwrap(requestOrValue) {
  return requestAsPromise(requestOrValue);
}

export function idbBackend() {
  return {
    async metaGet(key) {
      return (await tx("meta", "readonly",
        (s) => unwrap(s.get(key)))) ?? null;
    },
    async metaSet(key, value) {
      await tx("meta", "readwrite", (s) => unwrap(s.put(value, key)));
    },

    async secretAll() {
      return tx("secrets", "readonly", (s) => unwrap(s.getAll()));
    },
    async secretGet(id) {
      return (await tx("secrets", "readonly",
        (s) => unwrap(s.get(id)))) ?? null;
    },
    async secretBySha256(sha) {
      return (await tx("secrets", "readonly",
        (s) => unwrap(s.index("sha256").get(sha)))) ?? null;
    },
    async secretPut(record) {
      return tx("secrets", "readwrite", (s) => unwrap(s.put(record)));
    },
    async secretDelete(id) {
      await tx("secrets", "readwrite", (s) => unwrap(s.delete(id)));
    },

    async fpPut(row) {
      await tx("fingerprints", "readwrite", (s) => unwrap(s.add(row)));
    },
    async fpListBySecret(secretId) {
      return tx("fingerprints", "readonly", async (s) => {
        const all = await unwrap(s.getAll());
        return all.filter((f) => f.secretId === secretId);
      });
    },

    async auditLast() {
      return (await tx("audit", "readonly", async (s) => {
        const all = await unwrap(s.getAll());
        return all.length ? all[all.length - 1] : null;
      })) ?? null;
    },
    async auditAppend(row) {
      await tx("audit", "readwrite", (s) => unwrap(s.add(row)));
    },
    async auditList(limit = 100) {
      return tx("audit", "readonly", async (s) => {
        const all = (await unwrap(s.getAll())).sort((a, b) => b.id - a.id);
        return all.slice(0, limit);
      });
    },

    async corrAll() {
      return tx("corrections", "readonly", (s) => unwrap(s.getAll()));
    },
    async corrPut(row) {
      return tx("corrections", "readwrite", (s) => unwrap(s.put(row)));
    },
    async corrDelete(id) {
      await tx("corrections", "readwrite", (s) => unwrap(s.delete(id)));
    },
  };
}

export async function deleteDatabase() {
  if (dbPromise) {
    const database = await dbPromise;
    database.close();
    dbPromise = null;
  }
  await requestAsPromise(indexedDB.deleteDatabase(DB_NAME));
}
