"""分级判定解析器。

优先级（高到低）：

    0  用户对这个**具体值**的纠正      -> manual
    1  主机名，直接关联（同一条 curl / 同一个 JSON 对象）  -> exact
    2  键名（附着在值上）              -> high
    3  值形态，且该形态独有             -> medium
    4  主机名，上下文级（同一块 .env 里别处）  -> high
    5  窗口标题                        -> high
    6  值形态歧义 / 需要上下文          -> ambiguous 或 unknown（拒绝落盘）
    7  什么都没命中                   -> none

**第 1 级和第 4 级都是主机名，但强度不同。** 直接关联的主机名（这把 key 就是发给
那个 endpoint 的）压过键名；上下文级的主机名（这块配置里提到过那个主机）排在键名
和形态之后。不区分的话，一块含 deepseek base_url 的 .env 会把同块里的
GITLAB_TOKEN 误判成 deepseek —— 而那是个**看起来确定**的错判。

**窗口标题排在形态之后，不是之前。** 计划原稿把它插在主机名和键名之间，那是错的：
窗口标题是关于「环境」的证据，不是关于「值」的证据。从标题写着 DeepSeek 的浏览器
窗口里复制一把 ghp_ 开头的 GitHub token，会被误判成 DeepSeek —— 而形态本来能
100% 判对。窗口标题真正的用武之地是**值附着信号全部失败或歧义时**：从厂商控制台
复制一把裸 sk-，那是唯一能救回它的信号。

分级的另一个好处是免费送你一行人话证据。加权打分做不到这点，而且它会让两个弱
信号联手压过一个强信号。
"""

from __future__ import annotations

from dataclasses import dataclass

from kv.detect import rules
from kv.detect.corrections import CorrectionSet
from kv.model import (
    CONFIDENCE_AMBIGUOUS,
    CONFIDENCE_EXACT,
    CONFIDENCE_HIGH,
    CONFIDENCE_MANUAL,
    CONFIDENCE_MEDIUM,
    CONFIDENCE_NONE,
    PLATFORM_OPENAI_COMPATIBLE,
    PLATFORM_UNKNOWN,
    Candidate,
    Verdict,
)

CONFIDENCE_BY_STAGE = {"hostname": CONFIDENCE_EXACT, "keyname": CONFIDENCE_HIGH,
                       "shape": CONFIDENCE_MEDIUM, "window": CONFIDENCE_HIGH}


@dataclass(frozen=True)
class Deferred:
    """形态阶段命中了但**不能定论**：歧义，或缺上下文。

    先把它搁着，让窗口标题有机会救回来；救不回来再如实返回。
    """

    verdict: Verdict


def _verdict(platform: str, stage: str, evidence: str, *,
             confidence: str | None = None, candidates=(), rule=None,
             source: str | None = None) -> Verdict:
    source_rule = rule or rules.BY_PLATFORM.get(platform)
    return Verdict(
        platform=platform,
        confidence=confidence or CONFIDENCE_BY_STAGE[stage],
        source=source or stage,
        evidence=evidence,
        candidates=tuple(candidates),
        mask_style=source_rule.mask_style if source_rule else (0, 4),
        kind=source_rule.kind if source_rule else "token",
    )


def _pick_by_specificity(hits, value_text: str | None = None):
    """从多条命中里挑最具体的。平局返回 (None, 全部) 表示歧义。

    两步收窄，每步都对应一个实测出来的错判：

    1. **按平台去重。** 同一条规则可能因两个关键词都命中而出现两次（窗口标题
       "OpenAI API keys - ChatGPT" 同时含 openai 和 chatgpt），那不是歧义，
       是同一个答案被数了两遍。
    2. **多条命中时用值形态收窄。** `api.openai.com` 同时挂在 openai(30) 和
       openai-project(88) 上，光看 specificity 会让裸 sk- 被判成 openai-project
       —— 而 sk-proj- 形态根本不符。

    形态只在「同级有多条命中」时用来收窄，**单条命中一律接受**。反例是真实的：
    Vercel 的 token 并不带 `vercel_` 前缀，拿形态去否决单条命中会误杀它。
    多条命中却一条形态都不符时返回 None —— 让下一级（形态级）去说，它更懂值。
    """
    if not hits:
        return None, ()
    unique: dict[str, object] = {}
    for rule in hits:
        unique.setdefault(rule.platform, rule)
    deduped = list(unique.values())
    if len(deduped) == 1:
        return deduped[0], ()

    if value_text is not None:
        fitting = [r for r in deduped if rules.shape_fits(r.platform, value_text)]
        if not fitting:
            return None, ()
        if len({r.platform for r in fitting}) == 1:
            return fitting[0], ()
        deduped = fitting

    best = max(h.specificity for h in deduped)
    winners = [h for h in deduped if h.specificity == best]
    if len(winners) == 1:
        return winners[0], ()
    return None, tuple(winners)


def _match_hosts(hostnames) -> list:
    """主机名匹配：精确，或 .后缀。api.deepseek.com 命中 deepseek.com 的规则。"""
    found = []
    for host in hostnames or ():
        lowered = host.lower().strip(".")
        if not lowered:
            continue
        exact = rules.HOST_INDEX.get(lowered)
        if exact:
            found.extend(exact)
            continue
        for known, matched in rules.HOST_INDEX.items():
            if lowered.endswith("." + known):
                found.extend(matched)
    return found


def _hostname_stage(cand: Candidate, corr: CorrectionSet) -> Verdict | None:
    if not cand.hostnames:
        return None

    user = corr.hostname_hits(cand.hostnames)
    if user:
        if len(set(user)) == 1:
            return _verdict(user[0], "hostname",
                            f"用户纠正的主机名规则命中 {cand.hostnames[0]}",
                            confidence=CONFIDENCE_MANUAL)
        return _verdict(PLATFORM_UNKNOWN, "hostname", "用户主机名规则互相冲突",
                        confidence=CONFIDENCE_AMBIGUOUS, candidates=sorted(set(user)))

    winner, tied = _pick_by_specificity(_match_hosts(cand.hostnames), cand.text())
    if winner:
        return _verdict(winner.platform, "hostname",
                        f"主机名 {cand.hostnames[0]}", rule=winner)
    if tied:
        return _verdict(PLATFORM_UNKNOWN, "hostname", "多条主机名规则同级命中",
                        confidence=CONFIDENCE_AMBIGUOUS,
                        candidates=sorted({r.platform for r in tied}))
    return None


def _keyname_stage(cand: Candidate, corr: CorrectionSet) -> Verdict | None:
    if not cand.key_name:
        return None

    user = corr.keyname_hits(cand.key_name)
    if user:
        if len(set(user)) == 1:
            return _verdict(user[0], "keyname",
                            f"用户纠正的键名规则命中 {cand.key_name}",
                            confidence=CONFIDENCE_MANUAL)
        return _verdict(PLATFORM_UNKNOWN, "keyname", "用户键名规则互相冲突",
                        confidence=CONFIDENCE_AMBIGUOUS, candidates=sorted(set(user)))

    hits = [rule for pattern, rule in rules.KEYNAME_COMPILED
            if pattern.search(cand.key_name)]
    winner, tied = _pick_by_specificity(hits, cand.text())
    if winner:
        return _verdict(winner.platform, "keyname",
                        f"键名 {cand.key_name}", rule=winner)
    if tied:
        return _verdict(PLATFORM_UNKNOWN, "keyname", "多条键名规则同级命中",
                        confidence=CONFIDENCE_AMBIGUOUS,
                        candidates=sorted({r.platform for r in tied}))
    return None


def _shape_stage(cand: Candidate, corr: CorrectionSet) -> Verdict | Deferred | None:
    text = cand.text()
    hits = [rule for pattern, rule in rules.SHAPE_COMPILED if pattern.match(text)]
    if not hits:
        return None

    winner, tied = _pick_by_specificity(hits)

    if winner is None:
        return Deferred(_ambiguous_verdict(tied, corr))

    if winner.needs_context:
        # 走到这里说明主机名和键名两级都没命中 —— 形态本身不可信。
        return Deferred(_verdict(
            PLATFORM_UNKNOWN, "shape",
            f"{winner.display} 的形态（{winner.note or '裸值'}）缺少键名或主机名佐证，拒绝猜测",
            confidence=CONFIDENCE_NONE, rule=winner,
        ))

    return _verdict(winner.platform, "shape", f"值形态匹配 {winner.display}", rule=winner)


def _ambiguous_verdict(tied, corr: CorrectionSet) -> Verdict:
    """平局：多条规则共享同一个形态。诚实报告歧义，**绝不猜一个厂商**。"""
    platforms = sorted({r.platform for r in tied})
    group_id = next((r.group for r in tied if r.group), None)

    override = corr.group_platform(group_id)
    if override:
        return _verdict(override, "shape",
                        f"组默认纠正 {group_id} -> {override}",
                        confidence=CONFIDENCE_MANUAL)

    label = rules.GROUP_LABEL.get(group_id) if group_id else None
    if not label:
        return _verdict(PLATFORM_UNKNOWN, "shape", "多条形态规则同级命中",
                        confidence=CONFIDENCE_AMBIGUOUS, candidates=platforms)

    members = sorted({p for p in platforms if p != label})
    return _verdict(
        label, "shape",
        f"形态歧义（{group_id}）：{len(members)} 个平台共用这一形态，"
        "没有主机名或键名就无法区分",
        confidence=CONFIDENCE_AMBIGUOUS, candidates=members,
    )


def _window_stage(cand: Candidate, corr: CorrectionSet, title: str | None) -> Verdict | None:
    """环境证据。只在值附着信号全部失败或歧义时才走到这里。"""
    if not title:
        return None

    user = corr.window_hits(title)
    if user:
        if len(set(user)) == 1:
            return _verdict(user[0], "window",
                            "用户纠正的窗口标题规则命中（原始标题不留存）",
                            confidence=CONFIDENCE_MANUAL)
        return None

    lowered = title.lower()
    value_text = cand.text()
    hits = [rule for needle, rule in rules.WINDOW_INDEX
            if needle in lowered and rules.shape_fits(rule.platform, value_text)]
    winner, tied = _pick_by_specificity(hits, value_text)
    if winner:
        return _verdict(winner.platform, "window",
                        f"前台窗口标题含 {winner.display} 的关键词（原始标题不留存）",
                        rule=winner)
    return None


def _context_hostname_stage(cand: Candidate, corr: CorrectionSet) -> Verdict | None:
    """块级主机名：同一块 .env 里别处出现的 endpoint。

    **弱于键名和形态。** 只有那两级都没给出结论时才走到这里 —— 于是
    `LLM_BASE_URL=https://api.deepseek.com/v1` 能救回同块的裸 sk-，却不会把同块的
    GITLAB_TOKEN=glpat-… 改判成 deepseek（那个在第 2 级就定了）。
    """
    if not cand.context_hostnames:
        return None

    user = corr.hostname_hits(cand.context_hostnames)
    if len(set(user)) == 1:
        return _verdict(user[0], "shape",
                        f"用户纠正的主机名规则命中 {cand.context_hostnames[0]}",
                        confidence=CONFIDENCE_MANUAL, source="correction")

    winner, tied = _pick_by_specificity(_match_hosts(cand.context_hostnames), cand.text())
    if winner is None:
        return None
    return _verdict(
        winner.platform, "shape",
        f"同一块配置里的主机名 {cand.context_hostnames[0]}",
        confidence=CONFIDENCE_HIGH, source="context", rule=winner,
    )


def resolve(cand: Candidate, corr: CorrectionSet | None = None,
            *, window_title: str | None = None) -> Verdict:
    """对一个候选做判定。纯函数：不开库、不碰剪切板、不写任何东西。"""
    corr = corr or CorrectionSet.empty()

    pinned = corr.exact_platform(cand.sha256())
    if pinned:
        platform, when = pinned
        return _verdict(platform, "shape",
                        f"你在 {when or '此前'} 亲自指定过这个值",
                        confidence=CONFIDENCE_MANUAL, source="correction")

    for stage in (_hostname_stage, _keyname_stage):
        verdict = stage(cand, corr)
        if verdict is not None:
            return verdict

    outcome = _shape_stage(cand, corr)
    if isinstance(outcome, Verdict):
        return outcome

    contextual = _context_hostname_stage(cand, corr)
    if contextual is not None:
        return contextual

    windowed = _window_stage(cand, corr, window_title)
    if windowed is not None:
        return windowed

    if isinstance(outcome, Deferred):
        return outcome.verdict

    return _verdict(PLATFORM_UNKNOWN, "none", "没有任何规则匹配",
                    confidence=CONFIDENCE_NONE)


def describe(verdict: Verdict) -> str:
    """一行判定摘要。歧义时列出候选 —— 让人看见「它知道自己不确定」。"""
    marker = {"manual": "=", "exact": "=", "high": "+", "medium": "~",
              "ambiguous": "?", "none": "!"}.get(verdict.confidence, "?")
    head = f"{verdict.platform}{marker}"
    if verdict.candidates:
        return f"{head}  候选 {', '.join(verdict.candidates)}"
    return head
