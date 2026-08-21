---
id: unknown_dep
display_name: "Fixture unknown_dep"
target_vocab: [水果, 要, 多少, 钱, 三, 个]
scenario:
  situation: "You are at a fruit stall."
  goal: "Buy fruit and find out the price."
  withholding: "No prices are posted, and the vendor names one only if asked."
  slots:
    - id: item
      kind: inform
      description: "Say you want fruit"
      expressible_with: [水果, 要]
    - id: price
      kind: request
      description: "Find out what they cost"
      expressible_with: [多少, 钱]
      depends_on: [vendor]
---

# Fixture unknown_dep

Deliberately invalid — a `validate.py` rule fixture. See tests/test_kb_validate.py.
