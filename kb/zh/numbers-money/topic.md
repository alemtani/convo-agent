---
id: numbers-money
display_name: "Numbers and money (多少钱)"
target_vocab: [零, 一, 二, 两, 三, 四, 五, 六, 七, 八, 九, 十, 百, 钱, 块, 元, 毛, 多少, 一共, 个, 本, 件, 杯, 买, 卖, 要, 有, 没有, 给, 可以, 水果, 茶, 书, 包, 衣服, 大, 小, 贵, 便宜, 太, 好, 很, 一点儿, 别的, 颜色, 红, 白, 黑, 蓝, 绿, 黄, 我, 你, 这, 那, 哪, 什么, 是, 的, 了, 吗, 呢, 不, 也, 还, 请, 问, 谢谢, 再见]
proper_names: []
related: [greetings, shopping]
scenario:
  situation: "You are at a small street stall. Nothing on it is priced. The vendor greets you and waits."
  goal: "Find out what the item costs, find out whether there is a cheaper or smaller one, then say how many you want."
  withholding: "Nothing at the stall carries a price tag. The vendor is busy and says little: they name a price only when a customer asks for one, and they never mention the cheaper and smaller stock kept under the counter."
  slots:
    - id: price
      kind: request
      description: "Find out what the item costs"
      expressible_with: [多少, 钱, 这]
    - id: alternative
      kind: request
      description: "Find out whether a cheaper or smaller one exists"
      expressible_with: [有, 便宜, 小, 的]
    - id: order
      kind: inform
      description: "Say which one you want and how many"
      expressible_with: [要, 买, 两, 个]
---

# Numbers and money (多少钱)

Counting, prices and a small purchase. The learner asks what something costs,
reacts to the answer, asks for a cheaper or smaller one, and places an order.
Vocabulary is HSK 3.0 bands 1–2; grammar is limited to numbers, measure words,
多少钱, 太…了, 有没有, and 的 as "the … one".

**Conversation goal.** The learner should be able to: ask a price
(这个多少钱？), read a spoken price (二十五块), complain about it (太贵了), ask
whether something else exists (有没有便宜的？), and order (我要两个). Answers
stay short — 2–4 words — per the beginner-disfluency mitigation in the design.

**Scope discipline.** Dialogues use only `target_vocab` plus the compositional
phrases listed in `vocab.md`. No vocab outside HSK band 1–2 appears. Several
obvious stall words are out of scope at ceiling 2 and are deliberately absent:
苹果 and 香蕉 are band 3, and so is 老板 — the vendor is never addressed by
title.

**Scenario.** Three slots — two `request`, one `inform` — so the turn cap
derives to 7. Both requests are real obstacles: the learner cannot know the
price until the vendor says it, and cannot know a cheaper one exists until they
ask. The `inform` slot closes the transaction. A learner who packs the whole
purchase into one utterance is not flagged. `expressible_with` names the vocab
that *can* express each slot; it is a hint to the extractor and a handle for
`validate.py`, never a string matcher.

**Extending this topic.** A topic doesn't declare a band — the band ceiling is
*universal* (`_hsk/ceiling.json`), so raising that one number unlocks
higher-band vocab for every topic at once. At ceiling 3 this topic gains the
fruit names and 老板; to grow it, add the vocab and grammar and re-validate.
