---
id: self-intro
display_name: "Self-introduction (自我介绍)"
target_vocab: [我, 你, 他, 她, 好, 是, 的, 也, 和, 很, 不, 吗, 呢, 请, 问, 叫, 名字, 认识, 高兴, 谢谢, 再见, 人, 国, 中国, 外国, 北京, 从, 来, 住, 在, 工作, 上班, 做, 学生, 大学生, 学习, 大学, 学校, 老师, 医生, 说, 会, 汉语, 英语, 外语, 一点儿, 岁, 今年, 几, 二, 十, 什么, 哪, 哪儿]
proper_names: [小明, 小王, 小李, 王, 李, 上海, 美国, 英国]
related: [greetings, family]
scenario:
  situation: "You are at a language exchange meetup. A stranger sits down next to you, says hello, and waits for you to start."
  goal: "Find out where they are from and what they do, and tell them the same about yourself."
  withholding: "The stranger came to listen and lets the other person go first. They will answer anything they are asked, but they do not offer where they are from or what they do for a living until someone asks them."
  slots:
    - id: self_origin
      kind: inform
      description: "Say where you are from"
      expressible_with: [我, 是, 人, 国, 从, 来]
    - id: partner_origin
      kind: request
      description: "Find out where they are from"
      expressible_with: [你, 是, 哪, 哪儿, 国, 人]
    - id: self_job
      kind: inform
      description: "Say what you do — work or study"
      expressible_with: [我, 是, 学生, 工作, 学习]
    - id: partner_job
      kind: request
      description: "Find out what they do — work or study"
      expressible_with: [你, 做, 什么, 工作, 学习]
---

# Self-introduction (自我介绍)

The second topic: saying who you are past your name. Where you are from, what
you do, what languages you speak, and how old you are. Vocabulary is HSK 3.0
bands 1–2; grammar is limited to the patterns those four facts need.

**Conversation goal.** The learner should be able to: state nationality
(我是中国人 / 我从北京来), state a job or course of study (我是大学生 /
我在大学工作), say what languages they speak (我会说一点儿汉语), give an age
(我今年二十岁), and ask each of these back (你是哪国人？/ 你做什么工作？).
Answers are short — 2–4 words — per the beginner-disfluency mitigation in the
design.

**Relation to `greetings`.** That topic ends where this one starts. Names are
exchanged there and are not slots here; this topic assumes the partner is
already greeted and goes straight to the facts behind the name.

**Scope discipline.** Dialogues use only `target_vocab`, the compositional
phrases listed in `vocab.md`, and the declared `proper_names`. Place and country
names (上海, 美国, 英国) sit outside HSK bands 1–2, so they are whitelisted as
proper names rather than taught as vocabulary — a beginner recognises their own
country without learning the characters.

**Scenario.** Four slots — two `inform`, two `request` — so the turn cap derives
to 8 (`docs/SCENARIOS.md`). The obstacle is symmetric: the learner must both give
their own two facts and pull the partner's two out, and no packing lets them know
a country they were never told. No slot declares `depends_on`: the four facts are
independent, and any order the learner picks is a real conversation.
`expressible_with` names the vocab that *can* express each slot; it is a hint to
the extractor, never a string matcher.
