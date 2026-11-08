/** parse.js 测试：六解析器定序、去重、generic_scan 兜底、块级主机名弱证据、分词器。 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { parse, tokenize, normalize, unquote, splitPairs } from "../src/core/parse.js";
import { Candidate, Rejection, hostsIn } from "../src/core/candidate.js";

const SK48 = `sk-${"A".repeat(48)}`;
const HEX32 = "0123456789abcdef0123456789abcdef";
const ALNUM40 = "abcdefghij0123456789abcdefghij0123456789ab";
const ALNUM36 = "abcdefghij0123456789abcdefghij012345678";

const accepted = (text) => parse(text, { sourceHint: "golden" }).candidates;
const rejected = (text) => parse(text, { sourceHint: "golden" }).rejected;

test("裸值解析：sk- 与 15 字符真令牌都无长度下限放行", () => {
  const bare = accepted(SK48);
  assert.equal(bare.length, 1);
  assert.equal(bare[0].kind, "token");
  assert.equal(bare[0].keyName, null);
  assert.equal(bare[0].source, "golden");
});

test("裸 sk- 歧义落 openai-compatible 且候选≥5、绝不猜单一厂商", () => {
  const verdictFixtures = accepted("sk-xxxxxxxxxxxx-placeholder");
  assert.equal(verdictFixtures.length, 0); // sk-模板被闸门拦下，不产候选
});

test("15 字符真令牌无长度下限放行（桌面踩过的坑）", () => {
  const rows = accepted("TOKEN=Ab3xK9mQ2pL7vR1");
  assert.equal(rows.length, 1);
  assert.equal(rows[0].keyName, "TOKEN");
  assert.equal(accepted("Ab3xK9mQ2pL7v").length, 1);
});

test("AWS_ACCESS_KEY_ID 不被键名拒绝清单误杀", () => {
  const rows = accepted("AWS_ACCESS_KEY_ID=AKIAABCDEFGHIJKLMNOP");
  assert.equal(rows.length, 1);
  assert.equal(rows[0].keyName, "AWS_ACCESS_KEY_ID");
  assert.equal(rows[0].value, "AKIAABCDEFGHIJKLMNOP");
});

test("PEM 整块一条化，主体 base64 不再被抽成裸 token", () => {
  const pem = [
    "-----BEGIN PRIVATE KEY-----",
    "A1".repeat(100), // 会命中 TOKEN_RUN 的连续字母数字段
    "-----END PRIVATE KEY-----",
  ].join("\n");
  const rows = accepted(pem);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].kind, "pem");
  assert.ok(rows[0].value.startsWith("-----BEGIN"));
});

test("带密码的连接串一条化，kind=connstring", () => {
  const rows = accepted("postgres://app_user:secretPassword@db.internal.example:5432/app");
  assert.equal(rows.length, 1);
  assert.equal(rows[0].kind, "connstring");
  assert.equal(rows[0].value, "postgres://app_user:secretPassword@db.internal.example:5432/app");
});

test("GCP 服务账号 JSON 一条化，非机密字段进 extra", () => {
  const text = JSON.stringify({
    type: "service_account",
    project_id: "my-proj",
    private_key_id: "abc123def",
    private_key: "-----BEGIN PRIVATE KEY-----\nMIIEvA\n-----END PRIVATE KEY-----\n",
    client_email: "sa@my-proj.iam.gserviceaccount.com",
    token_uri: "https://oauth2.googleapis.com/token",
  });
  const rows = accepted(text);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].kind, "json");
  assert.equal(rows[0].keyName, "GCP_SERVICE_ACCOUNT");
  assert.equal(rows[0].extra.project_id, "my-proj");
  assert.deepEqual(rows[0].hostnames, ["oauth2.googleapis.com", "my-proj.iam.gserviceaccount.com"]);
  // 规范化值是键排序后的紧凑 JSON
  assert.ok(rows[0].value.startsWith('{"client_email":'));
});

test("AWS credentials CSV 拆两条并共享 pair_id", () => {
  const text = [
    "User name,Access key ID,Secret access key",
    `deploy,AKIAABCDEFGHIJKLMNOP,${ALNUM40}`,
  ].join("\n");
  const rows = accepted(text);
  assert.equal(rows.length, 2);
  const secret = rows[0];
  const identifier = rows[1];
  assert.equal(secret.keyName, "AWS_SECRET_ACCESS_KEY");
  assert.equal(identifier.keyName, "AWS_ACCESS_KEY_ID");
  assert.equal(secret.extra.pair_id, identifier.extra.pair_id);
  assert.match(secret.extra.pair_id, /^aws-\d+$/);
  assert.equal(secret.extra.aws_pair_role, "secret");
  assert.equal(identifier.extra.aws_pair_role, "identifier");
  assert.equal(secret.extra.aws_user_name, "deploy");
});

test(".env 块级主机名只作弱证据：进 context_hostnames 不进 hostnames", () => {
  const text = [
    "LLM_BASE_URL=https://api.deepseek.com/v1",
    `LLM_API_KEY=${SK48}`,
    "LLM_MODEL=deepseek-chat",
    "LLM_TIMEOUT=30",
  ].join("\n");
  const rows = accepted(text);
  assert.equal(rows.length, 1);
  assert.deepEqual(rows[0].keyName, "LLM_API_KEY");
  assert.deepEqual(rows[0].hostnames, []);
  assert.deepEqual(rows[0].contextHostnames, ["api.deepseek.com"]);
  assert.equal(rows[0].extra.hosts_from_block, true);
});

test(".env 块级主机名不改判同块 GITLAB_TOKEN（判定级回归）", () => {
  const text = [
    "LLM_BASE_URL=https://api.deepseek.com/v1",
    `LLM_API_KEY=${SK48}`,
    `GITLAB_TOKEN=glpat-${"b".repeat(20)}`,
  ].join("\n");
  const rows = accepted(text);
  assert.equal(rows.length, 2);
  // 两个凭据键名候选都拿到弱证据，但判定留给 resolve 分级。
  assert.ok(rows.every((row) => row.contextHostnames.length === 1));
});

test("多 provider 块不传播强证据：键名各自判定", () => {
  const text = [
    "OPENAI_BASE_URL=https://api.openai.com/v1",
    `DEEPSEEK_API_KEY=${SK48}`,
    "OPENAI_API_KEY=sk-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
  ].join("\n");
  const rows = accepted(text);
  assert.equal(rows.length, 2);
  assert.deepEqual(rows.map((row) => row.keyName), ["DEEPSEEK_API_KEY", "OPENAI_API_KEY"]);
});

test("dotenv：export 前缀、引号、引号内 #、CRLF", () => {
  const quoted = accepted('export DEEPSEEK_API_KEY="sk-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"\nexport GITHUB_TOKEN=\'ghp_abcdefghij0123456789abcdefghij012345678\'');
  assert.equal(quoted.length, 2);

  const hashInside = accepted('SLACK_TOKEN="xoxb-abcdefghij0123456789" # 机器人令牌');
  assert.equal(hashInside.length, 1);
  assert.equal(hashInside[0].value, "xoxb-abcdefghij0123456789");

  const crlf = accepted(`DEEPSEEK_API_KEY=${SK48}\r\nGITHUB_TOKEN=ghp_${ALNUM36}\r\n`);
  assert.equal(crlf.length, 2);
});

test("dotenv：注释与空值拒绝", () => {
  assert.equal(rejected("# 这里什么都没有").length, 0);
  const empty = rejected("API_KEY=");
  assert.ok(empty.some((item) => item instanceof Rejection && item.reason === "empty"));
});

test("curl：结构化解析先于 bare，URL 主机名随候选携带", () => {
  const text = `curl https://api.deepseek.com/v1/chat/completions -H 'Authorization: Bearer ${SK48}'`;
  const rows = accepted(text);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].keyName, "AUTHORIZATION");
  assert.equal(rows[0].extra.scheme, "bearer");
  assert.deepEqual(rows[0].hostnames, ["api.deepseek.com"]);
  assert.equal(rows[0].value, SK48);
});

test("curl：反斜杠与反引号续行、Content-Type 不算凭据", () => {
  const backslash = accepted([
    "curl https://api.deepseek.com/v1/chat/completions \\",
    "  -H 'Content-Type: application/json' \\",
    `  -H 'Authorization: Bearer ${SK48}' \\`,
    `  -d '{"model":"deepseek-chat"}'`,
  ].join("\n"));
  assert.equal(backslash.length, 1);
  assert.equal(backslash[0].value, SK48);

  const backtick = accepted([
    "curl https://api.moonshot.cn/v1/chat/completions `",
    `  -H "Authorization: Bearer ${SK48}"`,
  ].join("\n"));
  assert.equal(backtick.length, 1);
  assert.deepEqual(backtick[0].hostnames, ["api.moonshot.cn"]);

  assert.equal(accepted("curl https://api.openai.com/v1/models -H 'Content-Type: application/json'").length, 0);
});

test("curl：x-api-key 头与 basic auth", () => {
  const xapi = accepted(`curl https://api.anthropic.com/v1/messages -H 'x-api-key: ${"abcdefghij0123456789ab"}' -H 'anthropic-version: 2023-06-01'`);
  assert.equal(xapi.length, 1);
  assert.equal(xapi[0].keyName, "X_API_KEY");

  const basic = accepted("curl -u deploy:s3cretValue https://deploy.internal.example/");
  assert.equal(basic.length, 1);
  assert.equal(basic[0].keyName, "HTTP_BASIC_PASSWORD");
  assert.equal(basic[0].value, "s3cretValue");
  assert.equal(basic[0].extra.basic_user_len, 6);
});

test("jsonblob：base_url 主机名传播给兄弟凭据字段", () => {
  const text = `{"base_url":"https://api.deepseek.com/v1","api_key":"${SK48}"}`;
  const rows = accepted(text);
  assert.equal(rows.length, 1);
  assert.deepEqual(rows[0].hostnames, ["api.deepseek.com"]);
  assert.equal(rows[0].keyName, "API_KEY");
});

test("jsonblob：散文中的 JSON 与记录列表", () => {
  const inProse = accepted(`配置是这样 {"api_key":"${SK48}","base_url":"https://api.siliconflow.cn/v1"} 存好的`);
  assert.equal(inProse.length, 1);
  assert.deepEqual(inProse[0].hostnames, ["api.siliconflow.cn"]);

  const list = accepted(`[{"api_key":"${SK48}","base_url":"https://api.deepseek.com/v1"},{"api_key":"ghp_${ALNUM36}"}]`);
  assert.equal(list.length, 2);
  assert.equal(list[0].span, "[0]json object");
});

test("去重：同键同值一条，同值异键两条", () => {
  const same = accepted(`DEEPSEEK_API_KEY=${SK48}\nDEEPSEEK_API_KEY=${SK48}`);
  assert.equal(same.length, 1);
  const diff = accepted(`DEEPSEEK_API_KEY=${SK48}\nDEEPSEEK_API_KEY_COPY=${SK48}`);
  assert.equal(diff.length, 2);
});

test("generic_scan 兜底：散文里捞 key", () => {
  const rows = accepted(`我的令牌是 ghp_${ALNUM36} 别告诉别人`);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].span, "line 1");
});

test("generic_scan 兜底：纯字母长单词与中文散文不产候选", () => {
  assert.equal(accepted("authentication authorization implementation").length, 0);
  assert.equal(accepted("这是一段说明文字，里面没有任何密钥").length, 0);
});

test("占位符走拒绝通道而非低置信候选", () => {
  const result = parse("sk-xxxxxxxx", { sourceHint: "golden" });
  assert.equal(result.candidates.length, 0);
  assert.ok(result.rejected.some((item) => item.reason === "placeholder"));
  const reasons = new Set(rejected("OPENAI_API_KEY=${OPENAI_API_KEY}").map((item) => item.reason));
  assert.ok(reasons.has("placeholder"));
});

test("hostsIn：URL 主机名与邮箱域名，去重保序", () => {
  assert.deepEqual(
    hostsIn("https://API.DeepSeek.com/v1 探针 deploy@Example.COM https://api.deepseek.com/v2"),
    ["api.deepseek.com", "example.com"],
  );
  assert.deepEqual(hostsIn(""), []);
});

test("unquote 与 splitPairs：先引号后注释，export 前缀", () => {
  assert.equal(unquote('"xoxb-abc" # 机器人'), "xoxb-abc");
  assert.equal(unquote("'it''s'"), "it"); // 单引号取到第二个引号为止
  assert.equal(unquote("plain # comment"), "plain");
  assert.equal(unquote('open "quote'), 'open "quote'); // 未闭合只去掉开头引号
  assert.deepEqual(splitPairs("export A=1\n# 注释\nB = 2")[0], [1, "A", "1"]);
  assert.deepEqual(splitPairs("export A=1\n# 注释\nB = 2")[1], [3, "B", "2"]); // = 后的空白被 \s* 吃掉
});

test("POSIX 分词器：引号、转义、空 token", () => {
  assert.deepEqual(tokenize("curl -H 'Authorization: Bearer sk-x'"), ["curl", "-H", "Authorization: Bearer sk-x"]);
  assert.deepEqual(tokenize('curl -H "Content-Type: application/json"'), ["curl", "-H", "Content-Type: application/json"]);
  assert.deepEqual(tokenize("a '' b"), ["a", "", "b"]);
  assert.deepEqual(tokenize('a"b c"d'), ["ab cd"]);
  assert.deepEqual(tokenize("a\\ b"), ["a b"]);
  assert.deepEqual(tokenize('a "b\\"c" d'), ["a", 'b"c', "d"]);
});

test("POSIX 分词器：引号不平衡退回空白切分（与 shlex ValueError 回退一致）", () => {
  assert.deepEqual(tokenize("it's"), ["it\"s"]);
  assert.deepEqual(tokenize("a 'unterminated"), ["a", '"unterminated']);
});

test("normalize：反斜杠续行与反引号续行", () => {
  assert.equal(normalize("curl -H \\\n  'x: y'"), "curl -H 'x: y'");
  assert.equal(normalize("curl `\n  -H 'x: y'"), "curl -H 'x: y'");
});

test("bare 的四条排除：多行、空白、JSON、KEY=value", () => {
  assert.equal(accepted("a b c d e f g h i j k l m n o p").length, 0); // 含空白
  // 紧凑 JSON 交给 jsonblob 处理且只有一条（bare 排除 {[ 开头，不产生重复候选）
  const fromJson = accepted('{"api_key":"abcdefgh12345678"}');
  assert.equal(fromJson.length, 1);
  assert.equal(fromJson[0].span, "json object");
  assert.equal(accepted("KEY=abcdefghijklmnop").length, 1); // kvline 而非 bare
});

test("parse 返回的候选带掩码能力（C4 接缝）", () => {
  const [row] = accepted(`GITHUB_TOKEN=ghp_${ALNUM36}`);
  assert.deepEqual(row.maskParts(4, 4), ["ghp_", "5678"]);
  assert.equal(row.preview("*", 4, 4), "ghp_****5678");
  assert.match(row.sha256(), /^[0-9a-f]{64}$/);
  assert.ok(row instanceof Candidate);
});
