# Knowledge base — Chinese (zh)

Catalog of conversation topics. Each entry points to `kb/zh/<id>/` containing
`topic.md` (frontmatter + overview), `vocab.md`, `grammar.md`, `dialogues.md`.
Vocab is verified against `_hsk/hsk-3.0.json` (a `word → band` membership map for
all HSK 3.0 bands). The ceiling a topic is validated at is *universal* —
`_hsk/ceiling.json`, the learner's current level — not authored per topic.

This table is also the catalog `GET /api/topics` serves, so a row's display name
and summary are learner-facing text, not notes to another author. Topic
directories are the source of truth: a topic with no row here still lists (named
from its own frontmatter), and a row for a directory that no longer exists is
ignored.

| id | display name | HSK band | summary |
|---|---|---|---|
| [family](family/topic.md) | Family (家人) | 1–2 | Count a family, name its members, ask ages and what people do. |
| [food-ordering](food-ordering/topic.md) | Ordering food (点菜) | 1–2 | Ask what is good, ask what there is to drink, order a dish and a drink. |
| [greetings](greetings/topic.md) | Greetings (你好) | 1–2 | Greet, exchange names, ask "how are you?", say goodbye. |
| [numbers-money](numbers-money/topic.md) | Numbers and money (多少钱) | 1–2 | Count, ask a price, ask for a cheaper or smaller one, say how many you want. |
| [self-intro](self-intro/topic.md) | Self-introduction (自我介绍) | 1–2 | Say where you are from, what you do, what languages you speak, how old you are. |

Rows are sorted by `id`, which is the order the API returns them in.

_More topics to follow (time and date, weather, directions) — see
`docs/CURRICULUM.md` for where the slate comes from._
