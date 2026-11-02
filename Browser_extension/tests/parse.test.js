// 解析链：dotenv/kvline/bare/curl/jsonblob/awscsv + pipeline 顺序与去重。
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  parse, parseDotenv, parseKvline, parseBare, parseCurl, parseJsonblob, parseAwsCsv,
  unquote,
} from "../src/core/parse.js";

function ok(item) {
  assert.ok(!(item && item.rejection), `应放行却拒绝：${JSON.stringify(item)}`);
  return item;
}

function rejected(items) {
  return items.filter((i) => i.rejection);
}

test("unquote：先引号后注释，引号内 # 存活", () => {
  assert.equal(unquote('"xoxb-abc" # 机器人'), "xoxb-abc");
  assert.equal(unquote("xoxb-abc # comment"), "xoxb-abc");
  assert.equal(unquote('"unclosed value'), "unclosed value");
  assert.equal(unquote("'single'"), "single");
});

test("parseKvline：单行 KEY=value，占位符被闸门拒绝", () => {
  const result = parseKvline("DEEPSEEK_API_KEY=sk-abc123_def456ghi789jklmnopqrstu12");
  assert.equal(ok(result[0]).keyName, "DEEPSEEK_API_KEY");
  const bad = parseKvline("OPENAI_API_KEY=${OPENAI_API_KEY}");
  assert.equal(rejected(bad).length, 1);
});

test("parseDotenv：多行 + 引号注释 + 块级主机名弱传播", () => {
  const env = [
    "# 注释行",
    "LLM_BASE_URL=https://api.deepseek.com/v1",
    "LLM_API_KEY=sk-abc123_def456ghi789jklmno",
    "OTHER_TOKEN=xoxb-123456789-abcdefghij",
  ].join("\n");
  const result = parseDotenv(env);
  const candidates = result.filter((r) => !r.rejection);
  assert.equal(candidates.length, 2);
  const sk = candidates.find((c) => c.keyName === "LLM_API_KEY");
  // 弱证据：进 contextHostnames（不是 hostnames）
  assert.deepEqual(sk.contextHostnames, ["api.deepseek.com"]);
  assert.equal(sk.extra.hosts_from_block, true);
});

test("parseBare：四条排除判据", () => {
  assert.equal(ok(parseBare("sk-abc123_def456ghi789jklmno")[0]).kind, "token");
  assert.equal(parseBare("A B C").length, 0);                 // 含空白
  assert.equal(parseBare('{"a":1}').length, 0);               // JSON
  assert.equal(parseBare("KEY=value").length, 0);             // kvline 的活
  assert.equal(parseBare("多行\nsk-abc123def456ghi789").length, 0); // 多行
  assert.equal(ok(parseBare("postgres://u:p@h/db")[0]).kind, "connstring");
});

test("parseCurl：凭据头 + bearer + -u + 主机名强证据", () => {
  const text = 'curl https://api.deepseek.com/v1/chat -H "Authorization: Bearer sk-abc123_def456ghi789jklmno"';
  const result = parseCurl(text);
  const cand = ok(result[0]);
  assert.equal(cand.keyName, "AUTHORIZATION");
  assert.deepEqual(cand.hostnames, ["api.deepseek.com"]); // 强证据 → hostname 级 exact
  assert.equal(cand.extra.scheme, "bearer");

  const basic = parseCurl("curl https://example.com -u alice:s3cret-value-1234");
  assert.equal(ok(basic[0]).keyName, "HTTP_BASIC_PASSWORD");

  const apiKey = parseCurl("curl https://api.anthropic.com/v1 -H 'x-api-key: sk-ant-api03-abcdefghijklmnopqrst'");
  assert.equal(ok(apiKey[0]).keyName, "X_API_KEY"); // 桌面表即此映射；anthropic-api-key 才是 ANTHROPIC_API_KEY
});

test("parseJsonblob：base_url 兄弟传播 + GCP 服务账号一条记录", () => {
  const deep = parseJsonblob('{"base_url":"https://api.deepseek.com/v1","api_key":"sk-abc123_def456ghi789jklmno"}');
  const cand = ok(deep[0]);
  assert.deepEqual(cand.hostnames, ["api.deepseek.com"]);
  assert.equal(cand.extra.base_url, "https://api.deepseek.com/v1");

  const sa = parseJsonblob(JSON.stringify({
    type: "service_account", project_id: "p1", private_key: "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----",
    client_email: "svc@proj.iam.gserviceaccount.com", token_uri: "https://oauth2.googleapis.com/token",
  }));
  assert.equal(sa.filter((r) => !r.rejection).length, 1); // 绝不拆成多条
  assert.equal(ok(sa[0]).keyName, "GCP_SERVICE_ACCOUNT");
});

test("parseAwsCsv：拆两条记录，pair_id 关联", () => {
  const csv = [
    "User name,Access key ID,Secret access key",
    "alice,AKIAIOSFODNN7EXAMPLE,wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
  ].join("\n");
  const result = parseAwsCsv(csv);
  const candidates = result.filter((r) => !r.rejection);
  assert.equal(candidates.length, 2);
  const secret = candidates.find((c) => c.keyName === "AWS_SECRET_ACCESS_KEY");
  const id = candidates.find((c) => c.keyName === "AWS_ACCESS_KEY_ID");
  assert.equal(secret.extra.pair_id, id.extra.pair_id);
});

test("pipeline：结构化优先、(sha256,keyName) 去重、generic_scan 兜底", () => {
  // curl 的 -d JSON 会同时被 curl 和 jsonblob 命中 → 去重后一条
  const curlJson = parse('curl https://api.deepseek.com/v1 -d \'{"api_key":"sk-abc123_def456ghi789jklmno"}\'');
  assert.equal(curlJson.candidates.length, 1);

  // 散文里的裸 token（有数字）被 generic_scan 捞出
  const prose = parse("请看这段说明，密钥是 ghp_abcdefghijklmnopqrstuvwxyz123456 复制可用");
  assert.equal(prose.candidates.length, 1);
  assert.equal(prose.candidates[0].value, "ghp_abcdefghijklmnopqrstuvwxyz123456");

  // 散文里的纯字母长单词不是 token（plausible 过滤）
  assert.equal(parse("this is authentication without any digits").candidates.length, 0);

  // PEM 跨行：整段匹配，不被 base64 主体误抽
  const pem = parse("-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBg\n-----END PRIVATE KEY-----");
  assert.equal(pem.candidates.length, 1);
  assert.equal(pem.candidates[0].kind, "pem");

  // 空输入
  assert.equal(parse("").candidates.length, 0);
});
