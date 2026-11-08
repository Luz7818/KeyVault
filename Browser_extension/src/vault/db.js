// IndexedDB 后端：IBackend（CONTRACTS.md C2）的真身，浏览器侧的持久层。
//
// 库名 keyvault、DB_VERSION=1；stores：meta(键值)、secrets(keyPath id 自增，
// 唯一索引 sha256 与 name)、fingerprints/audit/corrections(keyPath id 自增)。
// 唯一索引冲突靠「先查后写」的契约顺序避免（store.js 的去重路径先
// secretBySha256 再 secretPut）；唯一索引是并发护栏，不是去重逻辑。
//
// 任何 store 形状变更必须递增 DB_VERSION 并在 onupgradeneeded 里写迁移。
//
// node 无 indexedDB——本文件不被 node 测试执行，真机行为靠浏览器走查
// （CONTRACTS.md C2 已声明边界）。除全局 indexedDB 外不触碰 chrome.*。

const DB_NAME = "keyvault";
const DB_VERSION = 1;

let dbPromise = null;

function openDb() {
  if (!dbPromise) {
    dbPromise = new Promise((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains("meta")) {
          db.createObjectStore("meta"); // 键值：key 由 put(value, key) 给
        }
        if (!db.objectStoreNames.contains("secrets")) {
          const secrets = db.createObjectStore("secrets", {
            keyPath: "id", autoIncrement: true,
          });
          secrets.createIndex("sha256", "sha256", { unique: true });
          secrets.createIndex("name", "name", { unique: true });
        }
        if (!db.objectStoreNames.contains("fingerprints")) {
          db.createObjectStore("fingerprints", { keyPath: "id", autoIncrement: true });
        }
        if (!db.objectStoreNames.contains("audit")) {
          db.createObjectStore("audit", { keyPath: "id", autoIncrement: true });
        }
        if (!db.objectStoreNames.contains("corrections")) {
          db.createObjectStore("corrections", { keyPath: "id", autoIncrement: true });
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error ?? new Error("IndexedDB 打开失败"));
    });
  }
  return dbPromise;
}

/** 把一个 IDBRequest 变成 Promise。 */
function done(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("IndexedDB 请求失败"));
  });
}

/** 在单个事务里跑 fn(store)；事务完整提交才算成功。 */
async function withStore(name, mode, fn) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(name, mode);
    let result;
    let failure = null;
    Promise.resolve()
      .then(() => fn(tx.objectStore(name)))
      .then((value) => { result = value; })
      .catch((error) => { failure = error; });
    tx.oncomplete = () => (failure ? reject(failure) : resolve(result));
    tx.onerror = () => reject(failure ?? tx.error ?? new Error("IndexedDB 事务失败"));
    tx.onabort = () => reject(failure ?? tx.error ?? new Error("IndexedDB 事务中止"));
  });
}

export function idbBackend() {
  return {
    async metaGet(key) {
      return withStore("meta", "readonly", async (store) => {
        return (await done(store.get(key))) ?? null;
      });
    },

    async metaSet(key, value) {
      return withStore("meta", "readwrite", (store) => done(store.put(value, key)));
    },

    async secretAll() {
      return withStore("secrets", "readonly", (store) => done(store.getAll()));
    },

    async secretGet(id) {
      return withStore("secrets", "readonly", async (store) => {
        return (await done(store.get(id))) ?? null;
      });
    },

    async secretBySha256(sha) {
      return withStore("secrets", "readonly", async (store) => {
        return (await done(store.index("sha256").get(sha))) ?? null;
      });
    },

    async secretPut(record) {
      return withStore("secrets", "readwrite", (store) => {
        // keyPath:'id' + autoIncrement 的 store：id 属性必须**完全省略**才会自增，
        // 显式 id:null/undefined 会让 key path 求值失败抛 DataError（真机实测）。
        // 所以这里按 C2 承诺替调用方剥离空 id 后再 put；id 有值则按 keyPath upsert。
        if (record.id === null || record.id === undefined) {
          const { id: droppedId, ...withoutId } = record;
          void droppedId;
          return done(store.put(withoutId));
        }
        return done(store.put(record));
      });
    },

    async secretDelete(id) {
      return withStore("secrets", "readwrite", (store) => done(store.delete(id)));
    },

    async fpPut(row) {
      return withStore("fingerprints", "readwrite", (store) => {
        // 有 id 则更新（put），无则追加（add 拿自增 id）
        return row.id !== undefined && row.id !== null
          ? done(store.put(row))
          : done(store.add(row));
      });
    },

    async fpListBySecret(secretId) {
      return withStore("fingerprints", "readonly", async (store) => {
        const all = await done(store.getAll());
        return all.filter((row) => row.secretId === secretId);
      });
    },

    /** 级联删除某记录的全部指纹行（桌面 schema.sql ON DELETE CASCADE +
     * db.py foreign_keys=ON 同语义；含轮换换下、带旧密文的吊销行）。返回行数。 */
    async fpDeleteBySecret(secretId) {
      return withStore("fingerprints", "readwrite", async (store) => {
        const all = await done(store.getAll());
        const doomed = all.filter((row) => row.secretId === secretId);
        for (const row of doomed) {
          await done(store.delete(row.id));
        }
        return doomed.length;
      });
    },

    async auditLast() {
      return withStore("audit", "readonly", async (store) => {
        const request = store.openCursor(null, "prev");
        return new Promise((resolve, reject) => {
          request.onsuccess = () => {
            const cursor = request.result;
            resolve(cursor ? cursor.value : null);
          };
          request.onerror = () => reject(request.error ?? new Error("IndexedDB 请求失败"));
        });
      });
    },

    async auditAppend(row) {
      return withStore("audit", "readwrite", (store) => done(store.add(row)));
    },

    async auditList(limit = 100) {
      return withStore("audit", "readonly", async (store) => {
        const all = await done(store.getAll());
        return all.slice(-limit).reverse();
      });
    },

    async corrAll() {
      return withStore("corrections", "readonly", (store) => done(store.getAll()));
    },

    async corrPut(row) {
      return withStore("corrections", "readwrite", async (store) => {
        if (row.id !== undefined && row.id !== null) return done(store.put(row));
        // 无 id：按 (kind, pattern) upsert，对齐桌面 ON CONFLICT(kind, pattern)
        const all = await done(store.getAll());
        const existing = all.find((c) => c.kind === row.kind && c.pattern === row.pattern);
        if (existing) {
          return done(store.put({ ...existing, platform: row.platform, note: row.note }));
        }
        return done(store.add(row));
      });
    },

    async corrDelete(id) {
      return withStore("corrections", "readwrite", (store) => done(store.delete(id)));
    },

    /** C1#30 reset 的存储侧：关连接 + deleteDatabase。审计随库消失——
     * 这是格式性极限（契约 C4 已声明），导出备份是清库前唯一前置。 */
    async clearAll() {
      if (dbPromise) {
        const db = await dbPromise;
        db.close();
      }
      dbPromise = null;
      await new Promise((resolve, reject) => {
        const request = indexedDB.deleteDatabase(DB_NAME);
        request.onsuccess = () => resolve();
        request.onerror = () => reject(request.error ?? new Error("IndexedDB 删除失败"));
        // 不监听 onblocked 放行：删除被其他连接挡住就等它关闭——reset 必须
        // 真删成功才算成功（C1#30）。单连接纪律见 C2：只有 background/SW
        // 模块级持有 idbBackend 连接（options/popup 走 background 消息），
        // 正常路径 onblocked 不会触发。
      });
    },
  };
}
