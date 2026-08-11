---
id: duplicate_ids
display_name: "Fixture duplicate_ids"
target_vocab: [水果, 要, 多少, 钱, 三, 个]
scenario:
  situation: "You are at a fruit stall."
  goal: "Buy fruit and find out the price."
  slots:
    - id: item
      kind: inform
      description: "Say you want fruit"
      expressible_with: [水果, 要]
    - id: item
      kind: request
      description: "Find out what they cost"
      expressible_with: [多少, 钱]
---

# Fixture duplicate_ids

Deliberately invalid — a `validate.py` rule fixture. See tests/test_kb_validate.py.
