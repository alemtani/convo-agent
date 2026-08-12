---
id: greetings
display_name: "Greetings (你好)"
target_vocab: [你, 您, 好, 我, 他, 她, 们, 老师, 同学, 朋友, 谢谢, 再见, 请, 问, 名字, 叫, 姓, 什么, 是, 认识, 很, 高兴, 早, 早上, 吗, 呢, 不, 也, 对不起, 没关系, 怎么样, 最近]
proper_names: [小明, 小王, 小李, 李]
related: [self-intro, family]
scenario:
  situation: "You meet a classmate on campus in the morning. They greet you first."
  goal: "Introduce yourself, find out their name, and ask how they have been lately."
  slots:
    - id: self_name
      kind: inform
      description: "Say what you are called"
      expressible_with: [我, 叫, 姓]
    - id: partner_name
      kind: request
      description: "Find out their name"
      expressible_with: [你, 叫, 什么, 名字]
      depends_on: [self_name]
    - id: wellbeing
      kind: request
      description: "Find out how they have been lately"
      expressible_with: [最近, 怎么样]
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

**Scenario.** The `scenario:` block above is the reference example for the format
(`docs/SCENARIOS.md`). Three slots — one `inform`, two `request` — so the turn cap
derives to 7. Both `request` slots are real obstacles: no amount of packing lets
the learner know a name they were never told. `expressible_with` names the vocab
that *can* express each slot; it is a hint to the extractor and a handle for
`validate.py`, never a string matcher.

**Extending this topic.** A topic doesn't declare a band — the band ceiling is
*universal* (`_hsk/ceiling.json`, the learner's current level), so it isn't
repeated here. As the learner advances, raising that one ceiling unlocks
higher-band vocab for *every* topic at once; to grow this topic you just add the
new vocab/grammar and re-validate (every word must be ∈ HSK at or below the
ceiling). A topic's actual highest band is *derived* from its vocab, not authored.
Incorporation into the running app needs no schema change: the DB holds only a
`topic_id → kb_path + content_hash` pointer (`DESIGN.md`), so editing this
markdown changes the hash and the loader folds the new content into the cached
prefix on the next session.
