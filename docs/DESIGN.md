# Convo Agent — Design Specification

## Project Purpose

A Mandarin conversation practice agent that provides realistic conversational practice with pronunciation and tone feedback. Uses Claude as the conversational AI engine and Azure Speech Services for speech processing and pronunciation assessment.

---

## Locked Architecture & Tech Stack

| Component | Choice | Rationale |
|---|---|---|
| **Platform** | Web app (React/Next.js frontend, Python backend) | |
| **Interaction** | Turn-by-turn (push-to-talk) | |
| **STT** | Azure Speech-to-Text | Keeps everything in the Azure ecosystem |
| **Pronunciation** | Azure Pronunciation Assessment | Only production-grade option for Mandarin tone scoring without building a research system |
| **TTS** | Azure Neural TTS or edge-tts | Azure has excellent Mandarin voices; edge-tts is free (uses Edge's API) |
| **LLM** | Claude API (Anthropic) | Conversation engine + feedback generation |
| **Storage** | SQLite (via aiosqlite) | Simple structured data, sufficient for MVP |
| **Budget tier** | Azure PA at moderate usage (~$10-30/mo) | |

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────┐
│                      USER DEVICE                         │
│  Mic → Audio capture → [Send audio blob per turn]        │
└──────────────┬──────────────────────────────────────────┘
               │ audio
               ▼
┌──────────────────────────────────────────────────────────┐
│                     BACKEND SERVER                        │
│                                                           │
│  ┌─────────────┐     ┌──────────────────┐                │
│  │  STT Engine  │     │  Pronunciation   │                │
│  │  (Azure STT) │     │  Assessment      │                │
│  │             │     │  (Azure PA API)   │                │
│  │  → best-    │     │  → tone scores    │                │
│  │    guess     │     │  → phoneme scores │                │
│  │    transcript│     │  → per-word acc.  │                │
│  └──────┬──────┘     └────────┬─────────┘                │
│         │                     │                           │
│         ▼                     ▼                           │
│  ┌──────────────────────────────────────┐                │
│  │       CONVERSATION ENGINE (LLM)      │                │
│  │                                       │                │
│  │  Inputs:                              │                │
│  │   - transcript (from STT)             │                │
│  │   - pronunciation scores (from PA)    │                │
│  │   - conversation sketch               │                │
│  │   - conversation history              │                │
│  │   - forgiveness_level param           │                │
│  │   - topic scope + proficiency profile │                │
│  │                                       │                │
│  │  Outputs:                             │                │
│  │   - partner response (Chinese text)   │                │
│  │   - coherence judgment (internal)     │                │
│  │   - turn annotations (internal log)   │                │
│  └──────────────┬───────────────────────┘                │
│                 │                                         │
│                 ▼                                         │
│  ┌──────────────────────────┐                            │
│  │  TTS (Azure / edge-tts)  │──→ audio response to user  │
│  └──────────────────────────┘                            │
│                                                           │
│  ┌──────────────────────────────────────┐                │
│  │  FEEDBACK ENGINE (every N turns)     │                │
│  │                                       │                │
│  │  Inputs:                              │                │
│  │   - accumulated turn annotations      │                │
│  │   - pronunciation scores per turn     │                │
│  │   - conversation sketch vs. actual    │                │
│  │                                       │                │
│  │  Outputs:                             │                │
│  │   - coherence/content feedback        │                │
│  │   - grammar corrections               │                │
│  │   - pronunciation/tone feedback       │                │
│  │   - per-topic score updates ──────────┼──┐            │
│  └──────────────────────────────────────┘  │            │
│                                              │            │
│  ┌───────────────────────────────────────┐   │            │
│  │  PROFICIENCY PROFILE (persistent DB)  │◄──┘            │
│  │                                        │               │
│  │  Per topic:                            │               │
│  │   - measured scores (recent window)    │               │
│  │   - self-reported weakness flag        │               │
│  │   - derived strength (EMA w/ decay)    │               │
│  │   - history over time (for charts)     │               │
│  │                                        │               │
│  │  Read at session start →               │               │
│  │    topic weighting for sketch gen      │               │
│  │  Written after feedback rounds →       │               │
│  │    updated scores                      │               │
│  └───────────────────────────────────────┘               │
└──────────────────────────────────────────────────────────┘
```

---

## Per-Turn Data Flow

```
User presses "talk" → records audio → releases → audio blob sent to backend

Backend (parallel):
  ├─ Azure STT → Chinese transcript (best-guess)
  └─ Azure Pronunciation Assessment → per-syllable tone scores, accuracy scores
       (reference text = STT transcript, two-pass)

Both results → Conversation Engine (Claude API):
  System prompt includes:
    - Role (e.g. "You are 阿姨, the user's aunt")
    - Conversation sketch (generated at session start)
    - Topic scope (all studied topics)
    - Forgiveness level parameter
    - Proficiency profile summary
  User message includes:
    - Transcript
    - Pronunciation scores (as structured data)
    - Conversation history

  Claude returns (structured JSON):
    - partner_response: Chinese text + pinyin
    - turn_annotation: {
        coherence: on_track | tangent | derailing,
        grammar_notes: [...],
        tone_errors: [...],  // cross-referenced with Azure PA scores
        topic_tags: ["family", "greetings"]
      }
    - should_give_feedback: bool (true every N turns)

If should_give_feedback:
  Feedback Engine (separate Claude call):
    - Input: accumulated turn annotations for last N turns
    - Output: structured feedback (English) covering:
        content/coherence, grammar, pronunciation/tones
    - Side effect: update proficiency profile in SQLite
        → new score entries per topic touched
        → recalculate derived_strength (EMA)

partner_response → TTS (Azure/edge-tts) → audio back to user
If feedback round: feedback text displayed in UI alongside audio
```

---

## Core Design Decisions

### Tone Evaluation — The Central Technical Risk

Standard STT does contextual disambiguation: if you say "mā" with the wrong tone but in a sentence where 妈 is the only plausible word, it transcribes 妈 anyway. This hides mistakes.

**Solution:** Azure Pronunciation Assessment. It gives per-phoneme accuracy scores and tone correctness scores for Mandarin specifically. The reference-text requirement is solved via a two-pass approach: STT transcribes first, then Azure PA assesses pronunciation quality against that transcript.

### Dual-Path Processing (Graceful Degradation)

The conversation partner and the evaluator operate on different representations of what the user said:

- **Conversation path:** Uses best-guess transcription (context-aware STT). The partner "understands" you the way a patient relative would — filling in gaps from context. Keeps the conversation flowing.
- **Evaluation path:** Uses pronunciation assessment scores (Azure PA). These get logged silently and surfaced during feedback rounds.

The "forgiveness threshold" is a parameter on the conversation path:

```
forgiveness_level: 0.0 (strict) → 1.0 (very patient)

At 0.8 (default for beginner):
  - Wrong tone but right word in context → continue, log for feedback
  - Wrong word but plausible response → continue, log
  - Unintelligible / completely off-topic → "对不起，你能再说一次吗？"

At 0.3 (advanced mode):
  - Wrong tone → partner "mishears" and responds to what you literally said
  - Simulates real miscommunication consequences
```

Implemented as part of the conversation LLM's system prompt, parameterized by the forgiveness level. The LLM receives both the best-guess transcript AND the pronunciation assessment scores, and the prompt instructs it on when to "notice" errors vs. let them slide.

### Conversation Validation — LLM Judge with Soft Constraints

The system generates a **conversation sketch** before each session — not a script, but a loose structure:

```
Conversation sketch:
- Phase 1: Greeting exchange (1-2 turns)
- Phase 2: Ask about family / how someone is doing (2-3 turns)
- Phase 3: Discuss a recent activity or plan (2-3 turns)
- Phase 4: Closing / goodbye (1-2 turns)

Current topics in scope: greetings, family, likes/dislikes
Target vocabulary: 你好, 你怎么样, 家人, 妈妈, 爸爸, 喜欢, ...
```

At each turn, the conversation LLM gets the sketch + history and judges coherence on a simple scale: **on-track / acceptable tangent / derailing**. Only "derailing" triggers a gentle redirect.

---

## Proficiency Profile Data Model

```json
{
  "topic_id": "family",
  "display_name": "Family (家人)",
  "measured_scores": [
    {"date": "2026-06-10", "coherence": 0.8, "grammar": 0.6, "pronunciation": 0.7},
    {"date": "2026-06-08", "coherence": 0.9, "grammar": 0.7, "pronunciation": 0.65}
  ],
  "self_reported_weak": false,
  "derived_strength": 0.72,
  "last_practiced": "2026-06-10"
}
```

### Topic Selection Weighting

```
weight(topic) = base_weight
  + bias_strength * (1 - derived_strength)     // weaker → higher weight
  + staleness_bonus(days_since_practiced)       // haven't seen it in a while
  + self_report_bonus(if self_reported_weak)    // user flagged it

bias_strength: 0.0 (uniform random) → 1.0 (heavily favor weak topics)
```

**Decay via exponential moving average (EMA):**

```
strength = alpha * latest_score + (1-alpha) * previous_strength
```

With alpha ≈ 0.3. Recent sessions matter more; old struggles fade as you improve. Topics not practiced for a long time gradually decay toward a "needs review" threshold.

---

## Planned Project Structure

```
convo-agent/
├── backend/
│   ├── main.py              # FastAPI server
│   ├── stt.py               # Azure STT integration
│   ├── pronunciation.py     # Azure PA integration
│   ├── conversation.py      # Claude conversation engine
│   ├── feedback.py          # Feedback generation + profile updates
│   ├── tts.py               # Text-to-speech
│   ├── profile.py           # Proficiency profile CRUD
│   ├── topic_selection.py   # Weighted topic selection logic
│   ├── models.py            # Data models
│   └── db.py                # SQLite setup
├── frontend/
│   ├── src/
│   │   ├── App.tsx
│   │   ├── components/
│   │   │   ├── ConversationView.tsx
│   │   │   ├── AudioRecorder.tsx
│   │   │   ├── FeedbackPanel.tsx
│   │   │   ├── ProfileDashboard.tsx  # strength charts
│   │   │   └── TopicManager.tsx      # add/flag topics
│   │   └── api/
│   └── package.json
├── schema.sql                # proficiency profile tables
├── docs/
│   └── DESIGN.md             # this file
└── README.md
```

---

## Technical Risks & Mitigations

1. **Azure PA reference text problem.** PA scores against a known reference. In free conversation, you don't know what the user will say. **Mitigation:** Two-pass — STT transcribes first, then Azure PA assesses against that transcript. Tone correctness per syllable is still reported even in this mode.

2. **Latency.** STT + PA + LLM + TTS in sequence could be slow. **Mitigation:** Run STT and PA in parallel (both take audio input). Stream LLM response. Start TTS on partial output. Target: <3s response time.

3. **Conversation sketch rigidity vs. uselessness.** Too detailed = script; too vague = can't judge coherence. **Mitigation:** Start with very loose sketches (phase labels + topic pools), tune based on real usage.

4. **Tone assessment accuracy for beginners.** Beginners produce disfluent speech; Azure PA may struggle with long pauses and false starts. **Mitigation:** Keep early utterances short (conversation partner asks simple questions expecting 2-4 word answers). Gradually increase expected complexity.

---

## MVP Scope

### Included

1. Turn-by-turn conversation: audio in → Azure STT + Azure PA → Claude conversation → TTS audio out
2. Three hardcoded topics (greetings, self-introduction, family)
3. Feedback every 3 turns (hardcoded)
4. Forgiveness level fixed at 0.8 (patient listener)
5. Basic proficiency profile — stores scores, no decay/EMA yet, no charts
6. Minimal web UI: talk button, conversation transcript display, feedback panel

### Deferred

- Custom topic management UI (add topics via config file initially)
- Self-reported weakness flagging
- Proficiency charts / trend visualization
- Decay/recency logic (simple average first, EMA later)
- Tunable forgiveness level (hardcoded first)
- Conversation sketch generation (start with single-prompt approach)
- Session history / replay

### Build Order

1. Backend speech pipeline (Azure STT + Azure PA integration) — de-risks the hardest part
2. Claude conversation engine with basic system prompt
3. Wire up TTS for audio response
4. Feedback engine (every N turns)
5. SQLite proficiency storage
6. React frontend with audio recorder + display
