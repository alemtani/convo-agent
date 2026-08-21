---
id: no_withholding
display_name: "Fixture no_withholding"
target_vocab: [水果, 要, 多少, 钱, 三, 个]
scenario:
  situation: "You are at a fruit stall. The vendor has priced every basket in large numbers."
  goal: "Buy fruit and find out the price."
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

# Fixture no_withholding

Deliberately invalid — a `validate.py` rule fixture. See tests/test_kb_validate.py.
