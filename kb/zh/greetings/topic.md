---
id: greetings
display_name: "Greetings (你好)"
hsk_band: [1, 2]
target_vocab: [你, 您, 好, 我, 他, 她, 们, 老师, 同学, 朋友, 谢谢, 再见, 请, 问, 名字, 叫, 姓, 是, 认识, 很, 高兴, 早, 早上, 吗, 呢, 不, 也, 对不起, 没关系, 怎么样, 最近]
related: [self-intro, family]
---

# Greetings (你好)

The first topic: opening and closing a conversation politely, exchanging names,
and asking/answering "how are you?". Vocabulary is HSK 3.0 bands 1–2; grammar is
limited to the minimal patterns needed to greet, introduce yourself, and say
goodbye.

**Conversation goal.** The learner should be able to: greet (你好 / 您好 /
早上好), give their name (我叫… / 我姓…), ask the partner's name (你叫什么名字？),
answer "how are you?" (我很好，谢谢。你呢？), and close (再见). Expected answers
are short — 2–4 words — per the beginner-disfluency mitigation in the design.

**Scope discipline.** Dialogues use only `target_vocab` plus the compositional
greeting phrases listed in `vocab.md`. No vocab outside HSK band 1–2 appears.

**Extending this topic.** `hsk_band` is a *ceiling*, not a fixed scope. As the
learner advances, widen the range (e.g. `[1, 2, 3]`), add the new vocab/grammar,
and re-validate against `_hsk/hsk-3.0.json` at the new ceiling — the wordlist
already holds every band, so this is additive, not a regeneration. Incorporation
into the running app needs no schema change: the DB holds only a
`topic_id → kb_path + content_hash` pointer (`DESIGN.md`), so editing this
markdown changes the hash and the loader folds the new content into the cached
prefix on the next session.
