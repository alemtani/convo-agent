# Seed dialogues — Greetings (你好)

Short exchanges that feed the session **sketch** (per `DESIGN.md`). They model
the arc — greet → names → how-are-you → close — using only `vocab.md` words plus
proper names (王, 李, 小明). Lines are short on purpose (beginner disfluency
mitigation: the partner asks questions expecting 2–4 word answers).

## Dialogue 1 — Meeting for the first time

> A: 你好！
> B: 你好！
> A: 我叫小明。你叫什么名字？
> B: 我叫小王。
> A: 认识你很高兴。
> B: 认识你很高兴。

## Dialogue 2 — How are you / and you?

> A: 你好吗？
> B: 我很好，谢谢。你呢？
> A: 我也很好。
> B: 你最近怎么样？
> A: 很好！

## Dialogue 3 — Polite, with a teacher, and closing

> A: 老师，您好！
> B: 你好！
> A: 这是我朋友，他姓李。
> B: 你好！认识你很高兴。
> C: 您好！
> B: 好，再见！
> A: 老师再见！

## Notes for the sketch generator

- Open with 你好 / 您好 (pick polite if the partner is framed as 老师).
- Mid-arc beats: exchange names (叫 / 姓 / 名字), then a 你好吗 / 最近怎么样
  check-in answered with 很 + adjective and bounced back with 呢.
- Close on 再见 once `target_turns` is approached.
