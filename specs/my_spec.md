# Specification: Exam Preparation for Students ("Prüfungsvorbereitung für Schüler")

> **Language policy for this document.**
> This specification uses **English** for all structure, instructions, and
> technical concepts (headings, architecture, Gherkin keywords, YAML keys
> and comments). **User-facing copy** — text that literally appears in the
> app — is kept in its original language (German / Portuguese) and marked
> `# USER-FACING — DO NOT TRANSLATE`. Such strings must be preserved exactly
> and ideally live in the i18n layer (de / pt / en).
>
> **Format mix:** Markdown for overview and architecture, YAML for
> configuration, Gherkin for testable behavior. This document is intended
> as input for an AI model that will build the application with
> **Google ADK 2.0** and deploy to Google Cloud.

---

## 1. Overview

**Goal:** A web application that helps school students (ages 10–18, grades
5–12) prepare
for exams by generating an interactive multiple-choice quiz based on grade,
subject, and topic.

**Technology base:** Google ADK 2.0 (Agent Development Kit) for agent
orchestration; containerized deployment to Google Cloud Run, with Vertex AI
providing the Gemini models used for LLM processing.

**Guiding principles:**

- Child-friendly, colorful, lively, with animal mascots and themes children
  enjoy.
- Multilingual; automatic language selection by IP, manually overridable.
- **No sign-in required** — no accounts, no login, data-minimal.
- Security first: every user prompt is screened before it reaches the LLM.
- Deterministic, quality-gated quiz creation (LLM-as-a-judge).
- Grounding uses relevance-filtered localized Wikipedia with internal model
  knowledge as fallback; other providers remain controlled extension points.

---

## 2. Architecture: two layers

ADK is a Python framework for the **agent layer**. It does not provide a
production child-facing web UI. Two layers are therefore kept separate:

```
┌─────────────────────────────────────────────────────────┐
│  PRESENTATION LAYER (web frontend)                       │
│  - Child-friendly layout (design tokens)                 │
│  - Flag dropdown, chat window, quiz rendering            │
│  - HTML export & share button                            │
└───────────────────────────┬─────────────────────────────┘
                            │  API (HTTP / streaming)
┌───────────────────────────┴─────────────────────────────┐
│  AGENT LAYER (Google ADK 2.0)                            │
│  - App plugin: security checkpoint + token-budget guard  │
│  - Conversation agent (collect info)                     │
│  - Curriculum preflight + quiz workflow graph            │
│  - Relevance-filtered Wikipedia + extension points       │
└───────────────────────────┬─────────────────────────────┘
                            │
┌───────────────────────────┴─────────────────────────────┐
│  PERSISTENCE / CLOUD                                      │
│  - Quiz store (frozen quizzes for share links)           │
│  - Admin log (feedback, security, quality failures)      │
│  - Usage counters (per-anonymous-user + global)          │
└─────────────────────────────────────────────────────────┘
```

**ADK integration requirement:** Quiz creation is implemented as an ADK 2.0
`Workflow` graph (Section 6). Cross-cutting security and token-budget hooks
must not be passed as extra `Workflow` constructor fields because unsupported
Pydantic fields can be discarded. They are registered globally on the ADK
`App` through a `BasePlugin`, with checks before a run and token accounting
after success or failure.

---

## 3. Design & layout (reusable)

All visual design is defined centrally as **design tokens**, not scattered
across code, so layouts are reused rather than recreated. Recommended:
tokens as a config file, implemented via a utility-CSS approach (e.g.
Tailwind) plus a component library.

```yaml
# design-tokens.yaml
theme:
  name: "Animal Adventure"
  # Two variants for the wide 10-16 age range:
  variants:
    playful:        # younger children (10-13)
      mascots_visible: true
      decorative_icon_density: high
    cool:           # older students (14-18); feels more grown-up
      mascots_visible: false
      decorative_icon_density: low

  colors:
    primary:    "#FF6B35"   # warm orange
    secondary:  "#4ECDC4"   # fresh teal
    accent:     "#FFD23F"   # sunny yellow
    success:    "#95E06C"   # correct answer
    error:      "#FF8FA3"   # soft red (not aggressive)
    background: "#FFF9F0"   # warm cream background
    text:       "#2D3047"   # dark, readable blue-grey

  fonts:
    heading: "Baloo 2"      # rounded, child-friendly (Google Fonts)
    body:    "Nunito"       # highly readable (Google Fonts)

  mascots:
    # Original, application-owned artwork; never render mascots as
    # operating-system/vendor Unicode animal characters.
    - id: "fox"
      asset_name: "felix"
      name: "Felix der Fuchs"
    - id: "owl"
      asset_name: "olivia"
      name: "Olivia die Eule"
    - id: "dragon"
      asset_name: "dino"
      name: "Dino der Drache"

  difficulty_emojis:
    easy:   "🌱"
    medium: "⭐"
    hard:   "🚀"

  feedback_emojis:
    correct:   "🎉"
    incorrect: "💪"   # encouraging, not punishing
```

### 3.1 Mascot Encouragement Phrases

To keep children motivated, each mascot delivers localized correctness and incorrectness phrases. 

The Portuguese correctness phrases are:
- **Felix der Fuchs (Fox)**: `"Fantástico! Está absolutamente correto!"`
- **Olivia die Eule (Owl)**: `"Fantástico! Você entendeu perfeitamente!"`
- **Dino der Drache (Dragon)**: `"Força de Dragão! Você é genial!"`

The Portuguese incorrectness phrases are:
- **Felix der Fuchs (Fox)**: `"Quase lá! Erros nos ajudam a aprender!"`
- **Olivia die Eule (Owl)**: `"Cabeça erguida! Vamos aprender isso juntos!"`
- **Dino der Drache (Dragon)**: `"Não desanime! Na próxima você consegue!"`

### 3.2 Cross-platform Brand Asset Set

The web UI uses original storybook-explorer artwork for
Felix, Olivia, and Dino so the characters look consistent on Linux, macOS,
Windows, Android, and iOS. Transparent face and full-body exports are provided
at 64, 128, 256, and 512 pixels. The face variants are used in mascot
selection, the loader, and answer explanations.

The same asset family provides `favicon.ico`, 16/32-pixel favicons, an Apple
touch icon, 192/512-pixel application icons, and 1280×640 PNG/JPEG social
preview images. The favicon must be available at `/favicon.ico`; Open Graph
and Twitter metadata must reference the public JPEG preview. Source masters
and a deterministic Pillow build script are retained in `assets/`.

The mascot sources and listed production derivatives are dedicated under
**CC0 1.0 Universal (CC0-1.0)**. The repository must retain the accompanying
AI-provenance notice describing the original character direction, human
selection/review, generation date, and production processing. This asset
license does not replace the license governing other repository content and
does not grant trademark or patent rights.

---

## 4. Internationalization

```yaml
# i18n-config.yaml
languages:
  supported:
    - code: "de"
      label: "Deutsch"
      flag: "🇩🇪"
      countries: ["DE", "AT", "CH"]
    - code: "pt"
      label: "Português"
      flag: "🇧🇷"
      countries: ["BR", "PT"]
    - code: "en"
      label: "English"
      flag: "🇺🇸"
      countries: ["US", "GB", "*"]   # fallback
  default_fallback: "en"
  detection:
    method: "ip_geolocation"
    manual_override: true            # dropdown, top right
    preselect_detected: true         # detected language is preselected
```

```gherkin
Feature: Automatic language detection

  Scenario: Visitor from Germany
    Given a visitor with a German IP address opens the site
    When the page loads
    Then the site is displayed in German
    And the flag dropdown (top right) has 🇩🇪 Deutsch preselected

  Scenario: Visitor from Brazil
    Given a visitor with a Brazilian IP address opens the site
    When the page loads
    Then the site is displayed in Portuguese
    And the flag dropdown (top right) has 🇧🇷 Português preselected

  Scenario: Manual selection overrides detection
    Given the site was displayed in German based on IP
    When the visitor selects 🇧🇷 Português in the dropdown
    Then the entire site switches to Portuguese immediately

  Scenario: Country without a supported language
    Given a visitor from an uncovered country opens the site
    When the page loads
    Then the site is displayed in English (fallback)
```

### 4.1 Dynamic Translations and Dictionary Mapping (Strict i18n Rule)

> [!IMPORTANT]
> **Strict Dynamic Translation Rule**: No user-facing text, alerts, helper messages, correctness encouragements, exported labels, or footer captions may be hardcoded statically in HTML/CSS markup. All elements must be dynamically rendered at runtime from centralized language dictionary structures representing supported locales (DE, PT, EN fallback). This ensures perfect i18n synchronization and translation integrity.

All interface elements, custom error pages, and loading overlays must leverage client-side reactive language switching through standard key-value map retrieval.

---

## 5. Chat & information gathering

**Page title (USER-FACING — DO NOT TRANSLATE per locale; this is the German
default):** „Prüfungsvorbereitung für Schüler"

The visitor communicates with the AI assistant via a chat window. On start,
the assistant greets the user and asks for the information needed to build
the quiz.

```yaml
# conversation-config.yaml
required_info:
  - key: "grade"        # Schuljahr
    label_de: "Schuljahr"
    label_pt: "Ano escolar"
  - key: "subject"      # Fach
    label_de: "Fach"
    label_pt: "Matéria"
  - key: "topic"        # Thema
    label_de: "Thema"
    label_pt: "Tema"

messages:
  # USER-FACING — DO NOT TRANSLATE. Provide per locale (de/pt/en).
  welcome_de: >
    Willkommen! Ich helfe dir eine Prüfung zu erstellen, damit du dich
    auf eine Prüfung in der Schule vorbereiten kannst!
  ask_info_de: >
    Für welches Fach und Thema möchtest du lernen und in welchem Schuljahr?

terminology:
  # "Prüfung" may be presented to the user as "Quiz" (more friendly).
  user_facing_term_de: "Quiz"
```

The browser submits its predefined form values as structured JSON containing
`grade`, `subject`, `topic`, and `preferred_language`. The agent parses this
deterministically and skips the additional LLM extraction call. Natural
language chat remains supported and uses structured LLM extraction only when
the prompt is not a valid frontend payload. Missing language values always
fall back to English.

```gherkin
Feature: Information gathering before quiz creation

  Scenario: Complete information in one message
    Given the assistant has greeted the user
    When the user enters grade, subject, and topic
    Then the assistant recognizes all three values
    And the assistant starts quiz creation

  Scenario: Incomplete information requires a follow-up
    Given the assistant has greeted the user
    When the user provides only the subject
    Then the assistant politely asks for the missing grade and topic
    And the assistant starts the quiz only once all three values are present
```

---

## 6. Quiz creation: the quality-gated workflow

The ADK `Workflow` graph separates request collection, curriculum preflight,
optional grounding, generation, judging, validated output, clarification, and
quality-failure terminals. Request-scoped state includes a typed `QuizContext`
(`grade`, `subject`, `topic`, and `preferred_language`) that is reused by
feedback and quality diagnostics.

```yaml
# quiz-config.yaml
quiz:
  question_count: 10
  questions_shown: "one_at_a_time"
  answer_options:
    min: 3
    max: 5
  correct_answers_per_question: 1
  selection: "single_click"

judge:
  enabled: true
  model: "gemini-2.5-flash"
  temperature: 0.1
  checks:
    - "Questions fit grade, subject, topic, and curriculum guidance"
    - "Exactly one answer is correct"
    - "The correct index points to the factually correct option"
    - "Difficulty matches the grade level"
  on_first_rejection: "regenerate_with_judge_reason"
  max_attempts: 2
  on_second_rejection: "fail_closed"
  on_exception: "fail_closed"

knowledge_sources:
  default:
    - "llm_internal"
  optional_grounding:
    - "localized_wikipedia"
```

**The principal stages:**

1. **Information collection.** Parse deterministic frontend JSON directly, or
   extract missing values from natural language.
2. **Curriculum preflight.** Classify the exact grade/subject/topic combination
   as `compatible`, `needs_clarification`, or `incompatible` before grounding
   and generation (Section 6.3).
3. **Knowledge grounding.** Search localized Wikipedia and retain content only
   when the article title is relevant to every meaningful topic term.
4. **Quiz generation.** Generate exactly ten multiple-choice questions under
   the preflight's authoritative `difficulty_guidance`.
5. **Quality check.** A separate judge verifies structure, factual correctness,
   exact topic fit, and grade-level scope. The first rejection reason is passed
   to the generator for one materially corrected retry.
6. **Terminal routing.** Only a passed quiz reaches the presentation layer.
   A second rejection or judge exception routes to a localized fail-closed
   response and diagnostic persistence. The generation node may keep a
   candidate in `temp_quiz` but must not publish it through `Event.output`;
   only the validated success terminal may emit quiz JSON to the browser.
7. **Presentation and continuation.** The user completes the quiz, sees the
   result, and may continue with adaptive difficulty.

```gherkin
Feature: Quiz solving and result

  Scenario: Questions are presented one at a time
    Given a validated quiz of 10 questions exists
    When the user starts the quiz
    Then exactly one question is shown at a time
    And each question has between 3 and 5 options
    And only one option is correct

  Scenario: Good result
    Given the user has answered all 10 questions
    When the score is high
    Then a friendly congratulation message is shown
    And the user can navigate all questions and view the solutions

  Scenario: Weak result
    Given the user has answered all 10 questions
    When the score is low
    Then an encouraging "keep learning" message is shown
    And the user can navigate all questions and view the solutions

  Scenario: Adjust difficulty
    Given the user has finished a quiz
    When the chat asks for the next difficulty
    And the user chooses "harder"
    Then a new quiz starts at higher difficulty from step 1
```

### 6.1 Asymptotic Progress Loader Overlay

To make longer generation times understandable, a rich progressive visual loader targets approximately 95% after **30 seconds** and remains below completion until the API returns:

1. **Visual Elements**:
   - **Central Mascot**: The chosen learning buddy mascot (*Felix, Olivia, or Dino*) remains static and non-rotating in the center.
   - **Outer Indication**: An infinite rotating outer dashed ring conveys ongoing active background operations.
   - **Circular Overlay**: An SVG progress ring overlay that fills progressively from 0 degrees (0% progress) to 360 degrees (100% progress) using the mascot's brand color.

2. **Asymptotic Progression Formula**:
   - The progress percentage updates every 200ms and approaches a 98% ceiling.
     Its growth factor is calibrated so the display reaches about 95% at the
     30-second target:
     $$g = 1 - \left(\frac{98 - 95}{98}\right)^{200/30000}$$
     $$\text{NewProgress} = \text{CurrentProgress} + (98 - \text{CurrentProgress}) \times g$$
   - The indicator never reports completion while the backend is still working.

3. **API Success Snapping**:
   - Immediately upon receiving the fully generated quiz payload, the progress bar snaps to 100% (360 degrees complete).
   - A visual buffer delay of 400ms is applied to let the full transition render smoothly before transitioning the user to the active quiz screen.

### 6.2 Adaptive Quiz Learning Progression & Dynamic Difficulty Localization

When the user finishes a quiz, they can choose to continue learning the same topic by selecting the primary action "Let's go for more questions" (or its localized equivalent). Rather than keeping a static difficulty level, the system dynamically adapts the quiz based on the user's previous score to deliver customized pedagogical pacing:

#### 6.2.1 Progression Modes (Score-Based Pacing)

1. **Reinforcement Mode (Score $\le$ 3 / 10)**:
   - **Pedagogical Goal**: Help the student master the content they struggled with.
   - **Behavior**: The agent shuffles the previous 10 questions and their options, repeating them so the student can focus on correcting their mistakes. Traditional duplicate-prevention filters are bypassed.
   - **Difficulty Rating**: `🌱 Easy` (mapped dynamically to user's language).

2. **Practice Mode (Score 4 - 7 / 10)**:
   - **Pedagogical Goal**: Consolidate understanding at the current level.
   - **Behavior**: The agent generates a new set of 10 standard-difficulty questions on the same topic. Duplication-prevention is recommended but not strictly enforced.
   - **Difficulty Rating**: `⭐ Medium` (mapped dynamically to user's language).

3. **User-Choice Progression Mode (Score $\ge$ 8 / 10) [Tester Feedback Integration]**:
   - **Pedagogical Goal**: Empower high-achieving students to steer their own academic progression and choose between consolidating standard-level content or tackling advanced, high-order challenges.
   - **Behavior**:
     - When a student finishes a quiz with a score of **$\ge 8/10$** and clicks **"Let's go for more questions"**, the app halts the standard request.
     - An interactive, beautifully styled **Difficulty Choice Modal** is presented to the user.
     - The user is prompted to choose between:
       - **Medium (Standard)**: Generates 10 completely fresh standard-difficulty questions (`⭐ Medium`).
       - **Difficult (Advanced)**: Generates 10 advanced-level questions introducing trickier distractors, deeper cognitive questions, and high-achiever curriculum concepts (`🚀 Hard`).
     - **Duplication-Prevention**: For both options, duplication-prevention is strictly enforced (absolutely zero questions from the previous run are repeated).

#### 6.2.2 Dynamic Difficulty Localization

To prevent leakage of English terminology on non-English user interfaces, raw difficulty indicators received from the backend are mapped to localized labels before rendering on the screen (both in `#quiz-difficulty` on the quiz interface and `#summary-difficulty` on the final summary screen). To ensure the UI is fully self-explained, a language-specific prefix description is prepended before the difficulty label:

- **Deutsch (DE)** (# USER-FACING — DO NOT TRANSLATE):
  - Prefix description: `"Stufe: "`
  - `🌱 Easy` $\to$ `"Stufe: 🌱 Einfach"`
  - `⭐ Medium` $\to$ `"Stufe: ⭐ Mittel"`
  - `🚀 Hard` $\to$ `"Stufe: 🚀 Schwer"`
  - Choice Modal Title: `"Hervorragende Leistung! Möchtest du mit der mittleren oder der schwierigen Stufe fortfahren?"`
  - Choice Button Medium: `"Mittel (Standard)"`
  - Choice Button Difficult: `"Schwer (Fortgeschritten)"`

- **Português (PT)** (# USER-FACING — DO NOT TRANSLATE):
  - Prefix description: `"Nível: "`
  - `🌱 Easy` $\to$ `"Nível: 🌱 Fácil"`
  - `⭐ Medium` $\to$ `"Nível: ⭐ Médio"`
  - `🚀 Hard` $\to$ `"Nível: 🚀 Difícil"`
  - Choice Modal Title: `"Excelente resultado! Você gostaria de continuar no nível médio ou no nível difícil?"`
  - Choice Button Medium: `"Médio (Padrão)"`
  - Choice Button Difficult: `"Difícil (Avançado)"`

- **English (EN / Fallback)** (# USER-FACING — DO NOT TRANSLATE):
  - Prefix description: `"Level: "`
  - `🌱 Easy` $\to$ `"Level: 🌱 Easy"`
  - `⭐ Medium` $\to$ `"Level: ⭐ Medium"`
  - `🚀 Hard` $\to$ `"Level: 🚀 Hard"`
  - Choice Modal Title: `"Great job! Do you want to proceed with the Medium or Difficult level?"`
  - Choice Button Medium: `"Medium (Standard)"`
  - Choice Button Difficult: `"Difficult (Advanced)"`

#### 6.2.3 Interactive Difficulty Tooltips

To make the adaptive pacing transparent and self-explained, both the `#quiz-difficulty` and `#summary-difficulty` badges feature an interactive hover effect. When a user hovers their cursor over a difficulty badge, a localized tooltip displays explaining how the current level was determined:

- **Deutsch (DE)** (# USER-FACING — DO NOT TRANSLATE):
  - `🌱 Einfach` explanation: `"Einfach: Automatisch aktiv nach weniger als 4 richtigen Antworten, um Grundlagen zu festigen."`
  - `⭐ Mittel` explanation: `"Mittel: Standardstufe für diese Klasse. Aktiv bei 4 bis 7 Punkten, oder per Benutzerauswahl."`
  - `🚀 Schwer` explanation: `"Schwer: Meisterstufe! Wird freigeschaltet bei 8 oder mehr Punkten per Benutzerauswahl."`

- **Português (PT)** (# USER-FACING — DO NOT TRANSLATE):
  - `🌱 Fácil` explanation: `"Fácil: Ativado automaticamente após menos de 4 respostas corretas para reforçar os fundamentos."`
  - `⭐ Médio` explanation: `"Médio: Nível padrão para esta série. Ativo com 4 a 7 respostas corretas, ou por escolha do usuário."`
  - `🚀 Difícil` explanation: `"Difícil: Nível mestre! Desbloqueado com 8 ou mais respostas corretas por escolha do usuário."`

- **English (EN / Fallback)** (# USER-FACING — DO NOT TRANSLATE):
  - `🌱 Easy` explanation: `"Easy: Activated automatically after scoring less than 4 correct answers to reinforce the fundamentals."`
  - `⭐ Medium` explanation: `"Medium: Standard level for this grade. Active with 4 to 7 correct answers, or via user preference."`
  - `🚀 Hard` explanation: `"Hard: Master level! Unlocked with 8 or more correct answers via user preference."`

The tooltip must render as a modern CSS-driven popover styling over the difficulty badges, ensuring smooth transitions and absolute positioning. Its content is dynamically populated on language switch and when updating difficulty badges.


```gherkin
Feature: Adaptive learning progression and localized difficulty indicators

  Scenario: Reinforcement mode for low score
    Given the user finished a quiz with a score of 3 out of 10
    When they select "Let's go for more questions"
    Then the system triggers reinforcement mode
    And the generated quiz shuffles and repeats the previous questions
    And the difficulty is shown as localized "🌱 Easy" (e.g. "🌱 Einfach" in German)

  Scenario: Practice mode for medium score
    Given the user finished a quiz with a score of 5 out of 10
    When they select "Let's go for more questions"
    Then the system triggers practice mode
    And the generated quiz contains a set of standard-difficulty questions
    And the difficulty is shown as localized "⭐ Medium" (e.g. "⭐ Mittel" in German)

  Scenario: High score triggers choice modal and selecting Medium difficulty
    Given the user finished a quiz with a score of 9 out of 10
    When they select "Let's go for more questions"
    Then the system displays the Difficulty Choice Modal
    When the user selects "Medium (Standard)"
    Then the system triggers progression mode with Medium difficulty
    And the generated quiz contains a completely fresh set of questions
    And none of the previous questions are duplicated
    And the difficulty is shown as localized "⭐ Medium" (e.g. "⭐ Mittel" in German)

  Scenario: High score triggers choice modal and selecting Difficult difficulty
    Given the user finished a quiz with a score of 10 out of 10
    When they select "Let's go for more questions"
    Then the system displays the Difficulty Choice Modal
    When the user selects "Difficult (Advanced)"
    Then the system triggers progression mode with Difficult difficulty
    And the generated quiz has significantly harder questions
    And none of the previous questions are duplicated
    And the difficulty is shown as localized "🚀 Hard" (e.g. "🚀 Schwer" in German)
```

### 6.3 Upfront Curriculum Validation and Scope Guidance

The application validates `grade`, `subject`, and `topic` immediately after all
three fields are available and **before** Wikipedia lookup, quiz generation, or
the academic judge. This reduces latency and cost for requests that cannot yet
produce a trustworthy grade-aligned quiz.

A deterministic `gemini-2.5-flash` call uses temperature 0.0 and the following
structured result:

```python
class CurriculumCompatibility(BaseModel):
    status: Literal["compatible", "needs_clarification", "incompatible"]
    explanation: str
    difficulty_guidance: str = ""
    clarification_question: str = ""
    suggested_topics: list[str] = []
```

Routing requirements:

- `compatible`: continue only when the exact request has a clear grade-level
  interpretation. Persist concrete concepts and exclusions as
  `curriculum_guidance` for both generator and judge.
- `needs_clarification`: use this for a valid subject/topic combination that is
  broad, elementary, ambiguous, or level-dependent. Clear the current topic,
  ask the localized clarification question, and show two or three possible
  grade-appropriate scopes without running later quiz nodes.
- `incompatible`: clear the topic and return a short, encouraging,
  mascot-guided explanation plus two or three suitable alternatives.
- Evaluator exception, empty response, or invalid schema: fail closed, show a
  localized temporary-unavailability message, and do not generate a quiz.

The evaluator must never silently reinterpret a topic to make it fit. For
example, Grade 12 Mathematics plus "Multiplication" needs clarification between
matrix, polynomial, complex-number, or another advanced interpretation; it must
not produce elementary multiplication questions. A legitimate educational
topic such as financial education for a graduating class may be compatible
when the evaluator provides an appropriate scope.

```gherkin
Feature: Upfront curriculum validation

  Scenario: Compatible request supplies authoritative guidance
    Given the user requests Grade "5", Subject "Math", Topic "Fractions"
    When curriculum preflight succeeds
    Then the status is "compatible"
    And concrete grade-level guidance is passed to generation and judging

  Scenario: Ambiguous level-dependent topic needs clarification
    Given the user requests Grade "12", Subject "Math", Topic "Multiplication"
    When curriculum preflight runs
    Then the status is "needs_clarification"
    And no grounding, generation, or judging call occurs
    And the user is asked to choose an advanced scope

  Scenario: Incompatible subject/topic combination
    Given the request is not a suitable school-learning combination
    When curriculum preflight runs
    Then the status is "incompatible"
    And the topic is cleared
    And the user receives localized alternatives

  Scenario: Curriculum evaluator is unavailable
    Given the curriculum model returns an error or invalid response
    When preflight cannot establish compatibility
    Then quiz generation is blocked
    And the user receives a localized retry message
```

---

### 6.4 Quality Failure Diagnostics

A quiz that cannot pass review is never released to the browser. The terminal
`quality_failure_node` removes the temporary quiz, resets the attempt counter,
returns a localized retry message, and writes a best-effort diagnostic to
`quiz_quality_failures`.

Each diagnostic contains:

- nested `quiz_context` with grade, subject, topic, and preferred language;
- `failure_type` (`judge_rejected` or `judge_exception`);
- the number of judge attempts and every judge reason;
- accepted Wikipedia title, if any, and whether grounding was discarded;
- a UTC timestamp.

Persistence failure is logged but must not replace the user-facing quality
message. No automatic Firestore Time To Live (TTL) policy is currently defined
for this diagnostic collection; its retention must be governed operationally.

---

## 7. Localized Wikipedia Grounding and Relevance Filtering

After a compatible preflight, the workflow searches the Wikipedia edition that
matches the preferred language. It evaluates at most five search results and
accepts an article only when every meaningful topic word matches the title
exactly, partially, or above the configured similarity threshold. Common stop
words are ignored. This prevents a loosely related result from replacing the
requested subject or topic (for example, an unrelated legal-informatics article
for an economics request).

Accepted content is stored as `search_context` together with
`grounding_title`. If no relevant result exists, the result is discarded,
`grounding_discarded` is set, and generation continues from internal model
knowledge without changing the requested subject/topic. Network calls use a
five-second timeout and the context is reused within the same session to avoid
duplicate lookups.

External curriculum MCP servers and other search providers remain extension
points, but any future provider must meet the same relevance, timeout, privacy,
and authoritative-topic requirements.

```gherkin
Feature: Grounding relevance

  Scenario: Relevant localized article is used
    Given curriculum preflight accepted the request
    And Wikipedia returns a title matching every meaningful topic term
    When grounding completes
    Then the article extract is supplied to quiz generation
    And the requested subject and topic remain authoritative

  Scenario: Unrelated search result is discarded
    Given Wikipedia returns an article whose title does not match the topic
    When relevance filtering runs
    Then no article context is supplied
    And the quiz may use internal knowledge without changing the topic

  Scenario: Wikipedia is unavailable
    Given the request times out or returns no useful result
    When grounding completes
    Then the user flow continues without external context
```

---

## 8. Feedback (thumbs up / down)

```gherkin
Feature: Quiz feedback

  Scenario: Positive feedback increments a satisfaction counter
    Given the user has finished a quiz
    When the user selects "thumbs up"
    Then the aggregated satisfaction count in the database is incremented by 1
    And no individual quiz or user logs are stored for positive feedback

  Scenario: Negative feedback is stored for review
    Given the user has finished a quiz
    When the user selects "thumbs down"
    Then the quiz, its questions, and answers are written to `feedback_logs`
    And grade, subject, topic, and preferred language are stored as queryable fields
    And the aggregated feedback counts are updated

  Scenario: Feedback storage is unavailable
    Given a feedback write fails in Firestore
    When the API returns a service-unavailable response
    Then the success message is not shown
    And the localized error toast asks the user to try again
    And the feedback controls are enabled for a retry
```

The frontend keeps a typed `QuizContext` while the quiz is created. Negative
feedback sends this context as a nested object; the repository flattens
`grade`, `subject`, `topic`, and `preferred_language` into the Firestore
document for querying. Positive feedback remains aggregate-only.

### 8.1 Anti-Spam Protection & Localized Toast Feedback

To maintain clean and un-spammed feedback logs, the rating system implements a strict rate protection mechanism along with premium dynamic toast localization.

#### 8.1.1 Anti-Spam Click-Locking Heuristics
1. **Interactive Session Lock**: Exactly **one feedback submission** is allowed per quiz run.
2. **Double-Gated Protection**:
   - A client-side global boolean state flag `hasSubmittedFeedback` is set to `true` instantly upon clicking either thumbs up or thumbs down.
   - The UI modifies the CSS properties of the feedback button elements to `pointer-events: none` to physically block subsequent click triggers and visual interactions.
3. **Session Reset**: The rate lock resets back to `false` only when the user finishes a new quiz or starts a completely new session.

#### 8.1.2 Dynamic Toast Feedback Copy
Instead of static responses, toast notifications are dynamically translated at runtime based on the selected language:

- **Thumbs Up Positive Feedback Toast Copy**:
  - **Deutsch (DE)**: `"Vielen Dank für deine Bewertung! ❤️"`
  - **Português (PT)**: `"Muito obrigado pela sua avaliação! ❤️"`
  - **English (ENFallback)**: `"Thank you for your rating! ❤️"`

- **Thumbs Down Negative Feedback Toast Copy**:
  - **Deutsch (DE)**: `"Vielen Dank für deine Bewertung, wir werden das Quiz prüfen und versuchen FoxQuiz zu verbessern!"`
  - **Português (PT)**: `"Obrigado pela sua avaliação, nós vamos analisar o quiz e tentar melhorar o FoxQuiz!"`
  - **English (ENFallback)**: `"Thank you for your rating, we will check the quiz and try to improve FoxQuiz!"`

---

## 9. Save, share & freeze

```yaml
# sharing-config.yaml
export:
  local_html:
    enabled: true
    button: true
    description: "Save the quiz as a local .HTML file"
  share_link:
    enabled: true
    button: true
    freeze: true            # current state is frozen
    cloud_storage: true     # second instance stored in Firestore
    direct_start: true      # recipient starts immediately, no chat questions
    expires_after_days: 30
    ttl_field: "expires_at"
```

```gherkin
Feature: Share and freeze a quiz

  Scenario: Local HTML export
    Given a quiz has been created
    When the user clicks "Save as HTML"
    Then the quiz is downloaded as a local .HTML file

  Scenario: Create a share link
    Given a quiz has been created
    When the user clicks "Share"
    Then the quiz is frozen in its current state
    And a second instance is stored in Firestore with a 30-day expiration
    And a link is generated

  Scenario: Recipient opens the share link
    Given a shared quiz link exists
    When a third party clicks the link
    Then they are taken directly to the quiz page
    And can start the quiz immediately
    And do not have to answer the assistant's questions
```

### 9.1 Fully Localized Local HTML Export

The offline HTML export must be fully localized to prevent any mixed-language experiences (e.g. German words in English exports or English words in Portuguese exports).

1. **Age Group Label Localization**:
   - The field formerly static `"Altersgruppe"` must be dynamic:
     - **Deutsch (DE)**: `"Altersgruppe"`
     - **Português (PT)**: `"Faixa Etária"`
     - **English (EN)**: `"Age Group"`
2. **Footer Attribution Localization**:
   - The footer attribution formerly displaying static English/German text must dynamically resolve `"Created with FoxQuiz"` based on the active export language:
     - **Deutsch (DE)**: `"Erstellt mit FoxQuiz"`
     - **Português (PT)**: `"Criado com FoxQuiz"`
     - **English (EN)**: `"Created with FoxQuiz"`

---

### 9.2 Shared-link Expiration and Social Preview

Every shared quiz receives `created_at` and `expires_at` timestamps. The API
logically rejects an expired link even if Firestore's asynchronous Time To Live
(TTL) deletion has not yet removed the document. A Firestore Time To Live (TTL)
policy on `quizzes.expires_at` performs eventual physical deletion.

The root page must return localized Open Graph and Twitter metadata for normal
and `?quiz_id=...` URLs so WhatsApp and other crawlers receive HTTP 200 instead
of 404. Metadata uses the 1280×640 FoxQuiz JPEG preview, an absolute
`https://foxquiz.app/...` image URL, and an English canonical URL by default.

---

## 10. Security checkpoint & Dynamic Security Configuration

Every user prompt is screened **before** it reaches the quiz workflow. The
cross-cutting implementation is a `FoxQuizSecurityPlugin(BasePlugin)`
registered in `App.plugins`. Its `before_run_callback` performs the security
and budget checks; `after_run_callback` and `on_run_error_callback` flush token
usage after successful and failed invocations. Unsupported callback fields must
not be passed to the `Workflow` model.

To support making the GitHub repository **public** without exposing defensive configurations (heuristics, classification prompts, system instructions, regexes, and sensitive keyword list), the application uses a **Private Firestore Security Configuration**.

### 10.1 Firestore Configuration Schema

All security rules, prompts, regexes, and keywords are stored in a private Firestore document: `system_config/security`. The codebase loads this document dynamically at runtime, keeping the public code clean and safe from reverse-engineering by potential attackers.

```yaml
# Firestore Document: system_config/security
# (Schema reference only. All actual classification prompts, regexes, and sensitive blocklist keywords are stored exclusively inside the private Firestore database)
classification_prompt: |
  <SYSTEM_CLASSIFICATION_PROMPT_TEMPLATE>
  # Private system instructions directing a fast classifier model to categorize input as SAFE, OFF_TOPIC, MALICIOUS, or PII.

blocklist_keywords:
  - "<SENSITIVE_KEYWORD_A>"
  - "<SENSITIVE_KEYWORD_B>"
  - "<SENSITIVE_KEYWORD_C>"
  - "..." # Real keywords are stored safely in Firestore and loaded at runtime.

injection_regexes:
  - "<SECURE_REGEX_PATTERN_A>"
  - "<SECURE_REGEX_PATTERN_B>"
  - "..." # Real regex patterns are stored safely in Firestore and loaded at runtime.

responses:
  off_topic_de: "Dieser Assistent kann dir leider nur bei der Vorbereitung auf Prüfungen helfen!"
  off_topic_pt: "Este assistente infelizmente só pode ajudar na preparação para exames!"
  off_topic_en: "This assistant can only help you prepare for exams!"
  injection_de: "Dieser Assistent kann dich nur bei der Vorbereitung auf deine Prüfungen unterstützen."
  injection_pt: "Este assistente só pode apoiar você na preparação para seus exames."
  injection_en: "This assistant can only support you in preparing for your exams."
  pii_de: "<LOCALIZED_PRIVACY_MESSAGE>"
  pii_pt: "<LOCALIZED_PRIVACY_MESSAGE>"
  pii_en: "<LOCALIZED_PRIVACY_MESSAGE>"
```

### 10.2 Guardrail Execution Workflow

The security checkpoint executes in a multi-stage fashion inside the
application plugin's `before_run_callback`:

1. **Lazy Loading & Caching**: The callback fetches `system_config/security` from Firestore. To avoid sub-second latency overhead on every user message, it caches the configuration in memory with a short TTL (e.g., 5 minutes) or simple in-memory session lifetime.
2. **Stage 1: Local Regex & Keyword Scanning (Fast Filter)**:
   - Perform case-insensitive checks of the user's prompt against `blocklist_keywords`.
   - Evaluate the prompt against `injection_regexes`.
   - If a match is found, immediately classify as `MALICIOUS` and short-circuit.
3. **Stage 2: LLM Classification (Semantic Filter)**:
   - If Stage 1 passes, use `gemini-2.5-flash` with temperature 0.0,
     `max_output_tokens=512`, and a small `thinking_budget=256`. Limited
     thinking improves semantic verification while keeping latency and cost
     bounded.
   - Accept only the exact decisions `SAFE`, `MALICIOUS`, `OFF_TOPIC`, or
     `PII`.
   - Use the LLM's semantic reasoning to recognize actual personal data and
     requests to find or investigate a named person across languages,
     countries, and document types. Do not maintain a fixed country-specific
     list of document-number patterns.
   - If the result is empty, invalid, or the classifier raises an exception,
     fail closed with a localized temporary-unavailability message.
   - If the classifier returns `MALICIOUS`, `OFF_TOPIC`, or `PII`, block and
     short-circuit.
4. **Action on Violation**:
   - **Block Prompt**: The prompt is not sent to the main Quiz Generator.
   - **Log Security Event**: If classified as `MALICIOUS`, write a log entry to the `security_events` Firestore collection (storing timestamp, blocked input, violation type, e.g. `RegexMatch`, `KeywordMatch`, `ClassifierBlock`, and anonymous ID).
   - **Protect PII**: Inputs classified as `PII` are not written to
     `security_events` and do not count toward a Sheriff ban.
   - **Friendly Blocked Response**: Store a structured block envelope in
     invocation-local state. The workflow's first node routes blocked requests
     directly to a terminal localized response before any quiz-processing node
     runs. This avoids exceptions from expected blocks in the ADK SSE stream.

### 10.3 Firestore Availability and Operational Visibility

Every Firestore operation required before quiz generation fails closed. Client
initialization, security-configuration loading, ban lookup, personal/global
budget lookup, malicious-event persistence, Sheriff counting, and ban writes
must all stop the workflow with a localized `SECURITY_UNAVAILABLE` response.
The security checkpoint's allowed route must carry the original user input
unchanged through invocation-local `temp:` state to `gather_and_route`, consume it
once, and never emit it as an intermediate client-visible workflow output.

The repository emits exactly one privacy-safe structured
`firestore_operation_failed` event per failed operation. It may contain only
the fixed phase and operation name, exception class and safe provider code,
service version, and deployed commit. It must never contain prompts, grade,
subject, topic, IP addresses, hashed signatures, defensive rules, or exception
messages. A logs-based counter tracks these events over time.

Post-generation token-budget persistence is best-effort: a failure remains
observable through the same structured event, but it must not replace an
already validated quiz with an error. Firestore feedback, sharing, and other
explicit API persistence failures continue to return HTTP 503 rather than false
success.

The Sheriff query requires a Firestore composite index on
`security_events(hashed_ip ASC, timestamp ASC)`. The index must be
`READY` before release testing.

```gherkin
Scenario: Firestore is unavailable during the security checkpoint
  Given a required pre-generation Firestore operation fails
  When the user requests a quiz
  Then no quiz-processing or generation node runs
  And the user receives a localized security-unavailable response
  And exactly one privacy-safe operational failure event is emitted

Scenario: Token persistence fails after a valid quiz
  Given the quiz passed academic review
  When the post-run token-budget write fails
  Then the valid quiz remains visible to the user
  And the failure is counted through the structured operational event
```

### 10.4 The Sheriff Guard (Automated Rate-Limiting & Auto-Banning)

To prevent malicious actors from repeatedly attempting prompt injections and wasting our precious LLM token budget, the security checkpoint includes an automated defense subsystem code-named **The Sheriff Guard** 🤠.

The Sheriff operates inside the plugin's pre-run security check and uses secure, GDPR/LGPD-compliant hashed IP signatures:

1. **Secure Client Fingerprinting**:
   - For every incoming request, the server extracts the user's IP address and runs it through a one-way secure hash function with a secret salt fetched from Firestore (`hashed_ip = SHA-256(IP + salt)`).
   - This creates a completely randomized, unique signature (e.g. `8f12a3bc...`) which cannot be reverse-engineered back to the original IP address, protecting children's privacy.
2. **Zero-Token Fast Block (Active Ban Check)**:
   - At the absolute start of *every* request, the server checks a local in-memory active ban cache.
   - If the user's `hashed_ip` signature is found in the cache, the request is instantly rejected before running any regexes, keyword searches, or LLM classification.
   - This consumes **exactly 0 LLM tokens**, defending the application budget against automated spammers and bots.
3. **The Gavel (Automatic Ban Trigger)**:
   - When a user's prompt violates our safety checkpoint and is logged to `security_events`, the Sheriff checks the database for other violations by the same `hashed_ip` signature within the past hour.
   - If a signature accumulates **3 or more safety violations** within a 1-hour window, the Sheriff automatically issues a ban:
     * A ban document is written to the private `banned_signatures` collection in Firestore, specifying the `hashed_ip`, `banned_at` timestamp, and `expires_at` timestamp (default: 24 hours).
     * The local in-memory active ban cache is instantly updated.
   - The user receives a friendly localized message indicating they have been blocked due to multiple safety violations.

```gherkin
Feature: Security checkpoint & Malicious prompt detection

  Scenario: Off-topic question (weather)
    Given the user has opened the chat
    When the user asks about the weather
    Then the prompt is not forwarded to the LLM
    And the user receives the friendly off-topic response

  Scenario: Personal data is protected
    Given the user enters a credit card number or personal data
    When the prompt is screened
    Then this data reaches the security classifier but not the quiz-generation LLM
    And this data is not written to logs

  Scenario: Prompt injection is blocked
    Given the user has opened the chat
    When the user enters an instruction meant to divert the LLM from exam preparation
    Then the prompt is not forwarded to the LLM
    And the user receives the friendly injection response
    And a security entry is written to the log

  Scenario: Administrative deletion prompts are blocked and logged
    Given the user has opened the chat
    When the user enters a blocked administrative command or destructive prompt
    Then the system checks the dynamic Firestore blocklist and regexes
    And the prompt is identified as malicious and blocked
    And the user receives the friendly injection response
    And a security entry is written to the security_events collection in Firestore

  Scenario: First safety violation does not trigger a ban
    Given the user has opened the chat
    When the user enters their first malicious prompt
    Then the security guard blocks the prompt
    And logs a security event tagged with their hashed IP signature
    But "The Sheriff" does not ban the user
    And the user can still attempt to enter a valid quiz prompt on their next turn

  Scenario: Third safety violation triggers automatic ban by "The Sheriff"
    Given the user has already committed 2 safety violations within the last hour
    When the user enters a third malicious prompt
    Then the security guard blocks the prompt
    And logs the third security event tagged with their hashed IP signature
    And "The Sheriff" automatically issues an active ban document in the banned_signatures collection
    And updates the in-memory ban cache

  Scenario: Banned user is blocked instantly with zero-token cost
    Given the user has been banned by "The Sheriff"
    When the user submits any prompt (valid or invalid)
    Then the server matches their hashed IP signature in the active ban list
    And blocks the request instantly at the front gate
    And does not invoke the LLM or run any classification checks
    And the user receives a localized safety block message
```


---

## 11. External Knowledge Extension Points

The current implementation uses localized Wikipedia grounding directly. MCP
curriculum servers, web search, and private question banks are optional future
providers rather than active prerequisites. Any provider added later must run
after curriculum preflight, preserve the requested grade/subject/topic as
authoritative, enforce bounded timeouts, and expose provenance suitable for
quality diagnostics.

```yaml
# knowledge-extension-config.yaml
providers:
  - name: "wikipedia"
    status: "active"
    relevance_filter: "required"
  - name: "curriculum_mcp"
    status: "optional_future"
  - name: "web_search"
    status: "optional_future"
  - name: "private_question_bank"
    status: "optional_future"
```

---

## 12. Per-user token budget (usage control)

A **Python script / agent** monitors token consumption. When a user reaches
the configurable daily limit, they are informed in a friendly way and can
create a new quiz only **the next day**. Counters reset daily.

**Important — the app runs without sign-in.** There is no stable identity,
so a per-user limit is only **partially enforceable**: a client-side id
(cookie / LocalStorage) can be reset via incognito mode or clearing browser
data. This is acceptable because the limit is for **gentle usage pacing**,
not abuse prevention. A **global daily limit for the whole application** is
added as a reliable **cost-protection net**, independent of recognizing
individual users. IP-based counting is **not** recommended as the primary
mechanism: schools and families share IPs (false blocks), and IP addresses
are sensitive for minors (see Section 14).

**ADK 2.0 implementation:** token usage is accumulated for every model call
and flushed by the application plugin after a successful invocation and also
from its run-error callback. The same plugin checks both personal and global
budgets before the workflow starts. A post-run Firestore failure is logged and
counted operationally but does not replace an already validated quiz; its token
usage may remain uncounted until storage is available again.

```yaml
# token-budget-config.yaml
token_budget:
  enabled: true

  # --- Personal limit (gentle pacing, no sign-in) ---
  per_user:
    max_tokens_per_day: 100000          # CONFIGURABLE
    identification:
      method: "client_anonymous_id"     # cookie / LocalStorage
      fallback: "best_effort"           # bypassable (incognito) — tolerated
      note: "For pacing only, not abuse prevention. No IP-based identity."

  # --- Global limit (reliable cost protection) ---
  global:
    max_tokens_per_day: 5000000         # CONFIGURABLE; protects total cost
    note: "Independent of per-user recognition; bounds total LLM cost."

  counting:
    scope: "input_and_output"
    source: "adk_native_usage"

  reset:
    schedule: "daily"
    reset_time: "00:00"
    timezone: "user_local"              # personal: user's local midnight
    global_timezone: "UTC"              # global: fixed reset point

  on_user_limit_reached:
    block_new_quiz: true                # an in-progress quiz may finish
    # USER-FACING — DO NOT TRANSLATE.
    message_de: >
      Du hast heute schon fleißig gelernt und dein Tageslimit erreicht! 🌙
      Komm morgen gerne wieder, dann kannst du ein neues Quiz starten.

  on_global_limit_reached:
    block_new_quiz: true
    # USER-FACING — DO NOT TRANSLATE.
    message_de: >
      Heute waren besonders viele fleißige Lernende unterwegs!
      Bitte versuch es morgen noch einmal – dann geht es weiter.

  storage:
    persistence: "firestore"
    transient_budget_ttl_days: 7
    ttl_field: "expires_at"
    global_budget_expires: false
    note: >
      Only budget_transient_* documents receive the seven-day expiration.
      Firestore Time To Live (TTL) performs eventual physical deletion.
```

```gherkin
Feature: Daily token budget

  Scenario: Consumption is counted continuously
    Given a user interacts with the assistant
    When an LLM call occurs
    Then the consumed input and output tokens are added to the user's daily counter

  Scenario: Limit not yet reached
    Given a user is below their configured daily limit
    When the user wants to create a new quiz
    Then the quiz is created as usual

  Scenario: Personal limit reached
    Given a user has reached their configured daily limit
    When the user wants to create a new quiz
    Then quiz creation is blocked
    And the user receives a friendly message to come back the next day

  Scenario: Daily reset
    Given a user reached their daily limit yesterday
    When a new day has begun (local midnight)
    Then their token counter resets to zero
    And the user can create a new quiz again

  Scenario: In-progress quiz is not interrupted
    Given a user reaches the limit during an in-progress quiz
    When the user keeps working on the current quiz
    Then they can finish the current quiz
    And only the creation of a new quiz is blocked

  Scenario: Global daily limit reached
    Given the global daily token limit of the application is reached
    When any user wants to create a new quiz
    Then quiz creation is blocked
    And the user receives a friendly message to try again the next day

  Scenario: Anonymous id is bypassable (intentionally tolerated)
    Given a user has reached their personal daily limit
    When the user clears browser data or uses incognito mode
    Then they receive a new anonymous id and a reset limit
    But the global daily limit remains in effect and still protects total cost
```

---

## 13. Deployment, Build Identity, and Observability

FoxQuiz supports Python 3.10+ and is packaged in a Python 3.12 image with
`uv==0.12.2`, then deployed to Google Cloud Run with `agents-cli deploy`.
The project uses manual infrastructure configuration documented in `CONTRIBUTING.md`; the
optional `agents-cli infra single-project` Terraform stack is not used.

A production deployment must start from a clean worktree and inject:

- `COMMIT_SHA`: the exact full Git commit being deployed;
- `AGENT_VERSION`: the application/package version;
- `BUILD_TIME`: the UTC build timestamp.

These values are public release-identification metadata, not secrets. They are
available from `/version`, the `X-FoxQuiz-Version` response header, and the page
footer. When the SHA is valid, the footer links directly to the corresponding
GitHub commit so users can identify code deployed from any branch or tag.

Required post-deployment configuration:

- Cloud Run service `foxquiz` in `us-east1` with zero minimum instances,
  startup CPU boost, and the Gen1 execution environment;
- public invocation through `roles/run.invoker` for `allUsers`;
- the project's default Compute service account as the runtime identity;
- one-time runtime roles `roles/monitoring.metricWriter`,
  `roles/telemetry.tracesWriter`, and
  `roles/serviceusage.serviceUsageConsumer`;
- Firestore Time To Live (TTL) policies on `budgets.expires_at` and
  `quizzes.expires_at`.

OpenTelemetry prompt-response export is enabled only when a logs bucket and
capture setting are configured. Capture is forced to `NO_CONTENT` so exported
records contain metadata rather than prompts or responses. Telemetry resource
attributes include the application version and commit SHA. Missing runtime
roles cause repeated exporter HTTP 403 errors and must be fixed at IAM level,
not hidden by deleting log entries.

```gherkin
Feature: Deployed source identification

  Scenario: User identifies the running build
    Given FoxQuiz was deployed with release metadata
    When the user opens the page or requests /version
    Then the application version and commit are visible
    And a valid commit links to the exact public GitHub revision

  Scenario: Telemetry is enabled without message content
    Given a telemetry bucket is configured
    When the application starts
    Then the capture mode is NO_CONTENT
    And version and commit metadata identify the emitting build
```

---

## 14. Data protection (brief note)

The target group is minors, so Germany's **GDPR** and Brazil's **LGPD**
apply, especially because logs are stored. The app runs **without sign-in**,
which simplifies data protection (no accounts, no persistent profiles) but
still requires:

- Data minimization: store only what is needed (grade, subject, topic — no
  identifying data).
- Since there is no login, the anonymous token id (cookie / LocalStorage)
  is kept minimal: a random id with no personal reference and a short
  lifetime.
- **IP addresses** are personal data under GDPR and especially sensitive
  for minors. Do not use raw IPs for token-budget identity. Geolocation uses
  them only briefly; security rate-limiting uses a salted one-way hash and
  stores only that signature (Sections 4 and 10).
- The security checkpoint already prevents personal data from reaching the
  LLM or logs (Section 10).
- Shared quiz documents expire logically after 30 days and are physically
  removed by Firestore Time To Live (TTL).
- Transient anonymous budget documents expire after seven days; the global
  budget document does not expire.
- Negative feedback and quiz-quality/security diagnostics are anonymized.
  Their retention is an explicit operational policy because no automatic Time
  To Live (TTL) is currently configured for those collections.
- A cookie/storage notice may be required once client-side ids are set.
- Depending on the design, parental consent may be legally required for
  minors.

> This is a technical note, not legal advice. Before going live, have data
> protection compliance reviewed by a qualified person.
