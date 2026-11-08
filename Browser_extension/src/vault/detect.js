// 检测组合入口 —— 检测装配的**唯一**入口（CONTRACTS.md C4）。
//
// 终结旧版两处各自组装的漂移（旧 background.js:78 自带 detectText、旧
// store.js 又自行 import parse/resolve 组装）：background 的 capture /
// store / store-direct 与 store 的 importText 一律调这里，不再各自拼装。
//
// 与桌面版的对位：kv/ops/backup.py 里 parse → resolve 的调用序 +
// kv/detect/corrections.py 的「detect 不碰数据库，规则由调用方注入」——
// 这里每次从 backend 现读 corrections，纯函数式组合，同一 backend+文本
// 结果确定（detect.test.js 有确定性断言）。
//
// promptable 口径 = C9#1 的有意放宽：isTrusted 全集（manual/exact/high/
// medium/ambiguous）且 platform != "unknown"。浏览器没有桌面 inbox，
// medium/ambiguous 不放宽就永远没有入库路径（歧义在 UI 显示「厂商未定」，
// 存后可 set-platform 修）。

import { parse } from "../core/parse.js";
import { resolve, isTrusted } from "../core/resolve.js";
import { CorrectionSet } from "../core/corrections.js";

/**
 * 把一段文本过完整检测链：装载 corrections → parse → 逐候选 resolve。
 *
 * @param backend 存储后端（只读 corrAll；不写任何东西）
 * @param {string} text 待检测文本
 * @param {{pageTitle?: string|null, sourceHint?: string}} options
 *   pageTitle 喂 resolve 的窗口/页面标题信号（桌面第 5 级 window 证据）；
 *   sourceHint 是解析器的来源标注（"extension" | "import" | …）。
 * @returns {Promise<Array<{value,keyName,kind,platform,confidence,evidence,
 *   source,maskStyle,preview,promptable}>>} 每个候选一条；不含任何审计副作用。
 */
export async function detectText(backend, text, { pageTitle = null, sourceHint = "extension" } = {}) {
  const corrections = CorrectionSet.fromRows(await backend.corrAll());
  const result = parse(text, { sourceHint });
  const findings = [];
  for (const candidate of result.candidates) {
    const verdict = resolve(candidate, corrections, { windowTitle: pageTitle ?? null });
    const maskStyle = verdict.maskStyle ?? [0, 4];
    findings.push({
      value: candidate.value,
      keyName: candidate.keyName,
      kind: candidate.kind,
      platform: verdict.platform,
      confidence: verdict.confidence,
      evidence: verdict.evidence,
      source: verdict.source,
      maskStyle,
      preview: candidate.preview("*", ...maskStyle),
      promptable: isTrusted(verdict) && verdict.platform !== "unknown",
    });
  }
  return findings;
}
