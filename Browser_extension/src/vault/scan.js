// 网页泄露扫描引擎 —— kv/ops/scan.py 的浏览器等价。
//
// 与桌面版的输入差异（有意）：桌面扫目录树（os.walk + 二进制/大小跳过），
// 浏览器扫调用方给定的 {name, content}[]——options 页的三种输入模式
// （粘贴文本 / 逐文件读取 / 整目录遍历）在应用层分批发送（C6），引擎无跨批
// 状态。逐行子串匹配的索引算法与桌面 _build_index 同构：按值长度分桶，
// 每行只切同样长度的子串。
//
// 解锁后解密全部指纹参与匹配（active+revoked，repo.py:300 all_scan_fingerprints
// 同语义：吊销的旧值仍是泄露证据，ops/scan.py:1-6）。指纹行的密文语义见
// store.rotate——活跃值的密文在 secret 行，轮换换下的旧值密文随吊销移入指纹
// 行，任何时刻同一密文只有一份；扫描现场解密，明文只在内存瞬时存在。
// 浏览器无运行时金标闸（桌面 AGENTS.md:112 的 scan gate 无等价物，C9#6）——
// npm test 金标是构建期门禁，发布物内规则冻结。

import { LockedError, b64ToBytes, decryptText } from "./crypto.js";
import { auditAppend } from "./store.js";

// 单消息上限按 chrome.runtime 消息通道安全量级取值（C6）
export const MAX_TEXTS_PER_CALL = 100;
export const MAX_CONTENT_CHARS = 1_000_000;
export const MAX_TOTAL_CHARS = 4_000_000;

/** 解密全部指纹（active+revoked），返回 [{value, secretId, name, platform, status, sha256}]。 */
async function loadFingerprints(backend, key) {
  const result = [];
  for (const secret of await backend.secretAll()) {
    for (const fp of await backend.fpListBySecret(secret.id)) {
      // 有自己密文的指纹（轮换换下的旧值）解自己的；否则解 secret 行的现值
      const box = fp.valueIv
        ? { iv: b64ToBytes(fp.valueIv), ct: b64ToBytes(fp.valueCt) }
        : { iv: b64ToBytes(secret.valueIv), ct: b64ToBytes(secret.valueCt) };
      let value = "";
      try {
        value = await decryptText(key, box);
      } catch (_) {
        continue; // 解不开的指纹跳过，不挡住其余扫描（scan.py:52-77 同宽容度）
      }
      if (!value) continue;
      result.push({
        value,
        secretId: secret.id,
        name: secret.name,
        platform: secret.platform,
        status: fp.status,
        sha256: fp.sha256,
      });
    }
  }
  return result;
}

/** 按值长度建索引：len -> 值 -> 指纹列表（scan.py:80-89 同构）。 */
function buildIndex(fingerprints) {
  const byLen = new Map();
  for (const fp of fingerprints) {
    let bucket = byLen.get(fp.value.length);
    if (!bucket) {
      bucket = new Map();
      byLen.set(fp.value.length, bucket);
    }
    const list = bucket.get(fp.value);
    if (list) list.push(fp);
    else bucket.set(fp.value, [fp]);
  }
  return byLen;
}

function validateTexts(texts) {
  if (!Array.isArray(texts)) throw new Error("texts 必须是 {name, content} 数组");
  if (texts.length > MAX_TEXTS_PER_CALL) {
    throw new Error(`单次扫描最多 ${MAX_TEXTS_PER_CALL} 条文本，请分批发送`);
  }
  let total = 0;
  for (const item of texts) {
    const length = typeof item?.content === "string" ? item.content.length : 0;
    if (length > MAX_CONTENT_CHARS) {
      throw new Error(
        `单条文本最多 ${MAX_CONTENT_CHARS} 字符（${item.name ?? "未命名"} 超限），请分批发送`,
      );
    }
    total += length;
  }
  if (total > MAX_TOTAL_CHARS) {
    throw new Error(`单次扫描总字符最多 ${MAX_TOTAL_CHARS}，请分批发送`);
  }
}

/** 扫描一组文本。命中 {textName, lineNo(1 起), secretId, name, platform,
 * status, sha256Prefix(12)}；同 secret 同行多值只出一条（C6）。 */
export async function scanTexts(backend, session, texts, { actor = "extension" } = {}) {
  if (!(await session.isUnlocked())) throw new LockedError();
  validateTexts(texts);

  const key = await session.key();
  const index = buildIndex(await loadFingerprints(backend, key));
  const hits = [];
  for (const item of texts) {
    if (typeof item?.content !== "string") continue;
    const lines = item.content.split(/\r\n|\r|\n/);
    const seenPerLine = new Map(); // lineNo -> Set(secretId)（同 secret 同行去重）
    for (let lineNo = 1; lineNo <= lines.length; lineNo++) {
      const line = lines[lineNo - 1];
      for (const [vlen, valueMap] of index) {
        if (vlen > line.length) continue;
        for (let start = 0; start <= line.length - vlen; start++) {
          const found = valueMap.get(line.slice(start, start + vlen));
          if (!found) continue;
          let seen = seenPerLine.get(lineNo);
          if (!seen) {
            seen = new Set();
            seenPerLine.set(lineNo, seen);
          }
          for (const fp of found) {
            if (seen.has(fp.secretId)) continue;
            seen.add(fp.secretId);
            hits.push({
              textName: item.name ?? "",
              lineNo,
              secretId: fp.secretId,
              name: fp.name,
              platform: fp.platform,
              status: fp.status,
              sha256Prefix: fp.sha256.slice(0, 12),
            });
          }
        }
      }
    }
  }
  await auditAppend(backend, {
    event: "scan", actor, detail: { texts: texts.length, hits: hits.length },
  });
  return hits;
}
