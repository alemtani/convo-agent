---
id: inform_only
display_name: "Fixture inform_only"
target_vocab: [水果, 要, 多少, 钱, 三, 个]
scenario:
  situation: "You are at a fruit stall."
  goal: "Buy fruit and find out the price."
  slots:
    - id: item
      kind: inform
      description: "Say you want fruit"
      expressible_with: [水果, 要]
    - id: quantity
      kind: inform
      description: "Say how many"
      expressible_with: [三, 个]
---

# Fixture inform_only

Deliberately invalid — a `validate.py` rule fixture. See tests/test_kb_validate.py.
