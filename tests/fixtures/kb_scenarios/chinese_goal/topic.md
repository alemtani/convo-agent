---
id: chinese_goal
display_name: "Fixture chinese_goal"
target_vocab: [水果, 要, 多少, 钱, 三, 个]
scenario:
  situation: "You are at a fruit stall."
  goal: "买水果，问多少钱。"
  slots:
    - id: item
      kind: inform
      description: "Say you want fruit"
      expressible_with: [水果, 要]
    - id: price
      kind: request
      description: "Find out what they cost"
      expressible_with: [多少, 钱]
      depends_on: [item]
---

# Fixture chinese_goal

Deliberately invalid — a `validate.py` rule fixture. See tests/test_kb_validate.py.
