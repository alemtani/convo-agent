---
id: food-ordering
display_name: "Ordering food (点菜)"
target_vocab: [吃, 喝, 菜, 米饭, 面包, 包子, 饺子, 鸡蛋, 肉, 鱼, 水果, 水, 茶, 牛奶, 菜单, 饭馆, 好吃, 要, 想, 点, 来, 有, 没有, 是, 喜欢, 给, 个, 杯, 碗, 一, 二, 两, 三, 十, 钱, 块, 最, 很, 不, 也, 都, 还, 别的, 可以, 和, 这, 那, 我, 你, 们, 好, 请, 问, 谢谢, 再见, 了, 什么, 多少, 几, 怎么样, 吗, 呢]
proper_names: []
related: [greetings, shopping]
scenario:
  situation: "You sit down in a small restaurant. A server comes over with the menu."
  goal: "Find out which dish is best here and what there is to drink, then order a dish and a drink."
  slots:
    - id: recommendation
      kind: request
      description: "Find out which dish is good here"
      expressible_with: [什么, 菜, 最, 好吃]
    - id: drinks
      kind: request
      description: "Find out what there is to drink"
      expressible_with: [有, 什么, 喝]
    - id: order
      kind: inform
      description: "Order a dish and a drink"
      expressible_with: [要, 点, 菜, 个, 杯]
      depends_on: [recommendation, drinks]
---

# Ordering food (点菜)

A restaurant scenario: read nothing, ask everything. Vocabulary is HSK 3.0
bands 1–2; grammar covers ordering verbs (要 / 想 / 点 / 来), the three measure
words a beginner needs (个, 杯, 碗), 有什么 for "what do you have", and
最 + adjective for "which is best".

**Conversation goal.** The learner should be able to: ask what is good
(什么菜最好吃？), ask what there is to drink (你们有什么？), order with a number
and a measure word (我要一杯茶), decline extras (不要了，谢谢), and ask the price
(多少钱？). Expected answers are short — 2–4 words — per the
beginner-disfluency mitigation in the design.

**Scope discipline.** Dialogues use only `target_vocab` plus the compositional
phrases listed in `vocab.md`. No vocab outside HSK band 1–2 appears. That rules
out some obvious restaurant words: 咖啡 and 服务员 are band 3, 餐厅 is band 5,
面条 is not in the index at all. The in-band food and drink nouns are 菜, 米饭,
包子, 饺子, 鸡蛋, 肉, 鱼, 面包, 水果, 水, 茶 and 牛奶 — the menu is built from
those.

**Scenario.** Two `request` slots and one `inform`, so the turn cap derives to
7 (`docs/SCENARIOS.md`). Both requests are real obstacles: the server names no
dish as best and lists no drinks until asked. The `order` slot depends on both,
so a tracker that credits an order before the learner has heard either answer
is flagged. `expressible_with` names the vocab that *can* express each slot; it
is a hint to the extractor and a handle for `validate.py`, never a string
matcher.

**Why the order slot comes last.** The goal is deliberately not "order food" on
its own. A learner can order from a menu they can already read, and learn
nothing. Making the recommendation and the drink list into extractions puts the
work where beginners actually fail — forming a question — and the order then
proves they understood the answers.

**Extending this topic.** A topic doesn't declare a band — the band ceiling is
*universal* (`_hsk/ceiling.json`, the learner's current level). Raising that one
ceiling unlocks higher-band vocab for *every* topic at once; here it would open
咖啡, 服务员 and 辣, which this scenario wants. To grow the topic, add the new
vocab and grammar and re-validate. A topic's actual highest band is *derived*
from its vocab, not authored.
