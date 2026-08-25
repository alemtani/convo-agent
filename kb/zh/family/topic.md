---
id: family
display_name: "Family (家人)"
target_vocab: [家, 家人, 人, 口, 个, 有, 没, 爸爸, 妈妈, 哥哥, 姐姐, 弟弟, 妹妹, 爷爷, 奶奶, 儿子, 女儿, 孩子, 几, 多少, 多, 大, 岁, 今年, 工作, 做, 学习, 学生, 老师, 医生, 喜欢, 都, 和, 的, 是, 不, 也, 很, 吗, 呢, 我, 你, 他, 她, 们, 好, 请问, 谢谢, 再见, 什么, 谁, 一, 二, 两, 三, 四, 五, 六, 七, 八, 九, 十]
proper_names: []
related: [greetings, self-intro]
scenario:
  situation: "A classmate sits down next to you and asks about your family."
  goal: "Find out how many people are in their family and how old one of their brothers or sisters is. Tell them about your own family too."
  withholding: "The classmate is curious about other people and modest about themselves. They ask, and they answer, but they never bring up their own family first and never mention their brothers and sisters unless asked about them."
  slots:
    - id: my_family
      kind: inform
      description: "Say how many people are in your family and who they are"
      expressible_with: [我, 家, 有, 口, 人, 爸爸, 妈妈, 和]
    - id: their_family_size
      kind: request
      description: "Find out how many people are in their family"
      expressible_with: [你, 家, 有, 几, 口, 人]
    - id: sibling_age
      kind: request
      description: "Find out how old one of their brothers or sisters is"
      expressible_with: [哥哥, 姐姐, 弟弟, 妹妹, 多, 大, 今年, 岁]
---

# Family (家人)

Talk about the people at home: who they are, how many, how old, and what they
do. Vocabulary is HSK 3.0 bands 1–2; grammar is limited to the counting,
age, and job patterns a beginner needs to hold this exchange.

**Conversation goal.** The learner should be able to: count a family
(我家有五口人), name its members (爸爸、妈妈和我), ask a family's size
(你家有几口人？), ask an age (你哥哥多大？) and answer it (他今年二十岁), and ask
what someone does (你爸爸做什么工作？). Expected answers are short — 2–4 words —
per the beginner-disfluency mitigation in the design.

**Scope discipline.** Dialogues use only `target_vocab` plus the compositional
phrases listed in `vocab.md`. No vocab outside HSK band 1–2 appears. The obvious
words for this topic that do *not* survive the ceiling are 年纪 (band 3),
妻子 and 丈夫 (band 4), and 年龄 (band 5). 多大 and 几口人 are the in-band ways to
ask the same things.

**Scenario.** Three slots — one `inform`, two `request` — so the turn cap
derives to 7. Both `request` slots are real obstacles: no packing lets the
learner know a family's size or a sibling's age before the partner says it.
`expressible_with` names the vocab that *can* express each slot; it is a hint
to the extractor and a handle for `validate.py`, never a string matcher.

**The pressure this scenario applies.** The classmate volunteers nothing. They
answer what is asked and hand the question back with 呢, so the gap sits where
the learner needs the question form — 几口人 and 多大 — and nowhere else.

**Extending this topic.** A topic doesn't declare a band — the band ceiling is
*universal* (`_hsk/ceiling.json`, the learner's current level), so it isn't
repeated here. Raising that one ceiling unlocks 年纪, 妻子, and 丈夫 here and
higher-band vocab everywhere else at the same time; to grow this topic you add
the new vocab/grammar and re-validate. A topic's actual highest band is *derived*
from its vocab, not authored.
