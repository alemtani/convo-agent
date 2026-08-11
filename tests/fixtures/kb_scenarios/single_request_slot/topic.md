---
id: single_request_slot
display_name: "Fixture single_request_slot"
target_vocab: [明天, 下雨]
scenario:
  situation: "You are outside with a friend."
  goal: "Find out if it will rain tomorrow."
  slots:
    - id: rain
      kind: request
      description: "Find out whether it will rain tomorrow"
      expressible_with: [明天, 下雨]
---

# Fixture single_request_slot

Deliberately invalid — a `validate.py` rule fixture. See tests/test_kb_validate.py.
