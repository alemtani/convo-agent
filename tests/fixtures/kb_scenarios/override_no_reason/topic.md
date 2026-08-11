---
id: override_no_reason
display_name: "Fixture override_no_reason"
target_vocab: [水果, 要, 多少, 钱, 三, 个]
scenario:
  situation: "You are at a fruit stall."
  goal: "Buy fruit and find out the price."
  max_turns: 9
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

# Fixture override_no_reason

Deliberately invalid — a `validate.py` rule fixture. See tests/test_kb_validate.py.
