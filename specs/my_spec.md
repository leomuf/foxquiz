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

**Goal:** A web application that helps school students (ages 10–16) prepare
for exams by generating an interactive multiple-choice quiz based on grade,
subject, and topic.

**Technology base:** Google ADK 2.0 (Agent Development Kit) for agent
orchestration; deployment to Google Cloud (Vertex AI Agent Engine / Agent
Runtime).

**Guiding principles:**

- Child-friendly, colorful, lively, with animal mascots and themes children
  enjoy.
- Multilingual; automatic language selection by IP, manually overridable.
- **No sign-in required** — no accounts, no login, data-minimal.
- Security first: every user prompt is screened before it reaches the LLM.
- Deterministic, quality-gated quiz creation (LLM-as-a-judge).
- Curriculum source is switchable: LLM knowledge by default, optional
  country-specific curriculum via MCP.

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
│  - Security checkpoint (guard)                           │
│  - Token-budget guard                                    │
│  - Conversation agent (collect info)                     │
│  - Quiz-creation workflow (graph, 6 steps)               │
│  - Tools: web search, MCP servers, knowledge sources     │
└───────────────────────────┬─────────────────────────────┘
                            │
┌───────────────────────────┴─────────────────────────────┐
│  PERSISTENCE / CLOUD                                      │
│  - Quiz store (frozen quizzes for share links)           │
│  - Admin log (thumbs-down, thumbs-up counter, security)  │
│  - Usage counters (per-anonymous-user + global)          │
└─────────────────────────────────────────────────────────┘
```

**Note on quiz creation as a graph:** The six steps are described
logically (see Section 6). The concrete realization as ADK workflow nodes,
loops, and human-in-the-loop pauses is left to the building AI. ADK 2.0
natively supports routing, loops, retry, state management, and
human-in-the-loop.

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
      emoji_density: high
    cool:           # older students (14-16); feels more grown-up
      mascots_visible: false
      emoji_density: low

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
    # Mascot display names are USER-FACING — DO NOT TRANSLATE the names.
    - id: "fox"
      emoji: "🦊"
      name: "Felix der Fuchs"
    - id: "owl"
      emoji: "🦉"
      name: "Olivia die Eule"
    - id: "dragon"
      emoji: "🐉"
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
- **Felix der Fuchs (Fox)**: `"🦊 Fantástico! Está absolutamente correto!"`
- **Olivia die Eule (Owl)**: `"🦉 Fantástico! Você entendeu perfeitamente!"`
- **Dino der Drache (Dragon)**: `"🐉 Força de Dragão! Você é genial!"`

The Portuguese incorrectness phrases are:
- **Felix der Fuchs (Fox)**: `"🦊 Quase lá! Erros nos ajudam a aprender!"`
- **Olivia die Eule (Owl)**: `"🦉 Cabeça erguida! Vamos aprender isso juntos!"`
- **Dino der Drache (Dragon)**: `"🐉 Não desanime! Na próxima você consegue!"`

**Free template sources** (no need to build from scratch):

- Fonts: Google Fonts (Baloo 2, Nunito, Fredoka) — free.
- Illustrations/mascots: unDraw, Open Peeps — free to use.
- Components: shadcn/ui or DaisyUI as a base.

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

## 6. Quiz creation: the 6-step workflow

Logical description. The realization as an ADK 2.0 workflow graph (nodes,
loops, HITL pauses) is chosen by the building AI.

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
  # Recommendation: same model type, separate judge role, strict review
  # prompt, lower temperature. Model kept configurable.
  enabled: true
  model: "configurable"               # e.g. a different Gemini variant
  temperature: 0.1
  checks:
    - "Questions fit grade, subject, and topic"
    - "Exactly one answer is correct"
    - "The correct answer is factually right"
    - "Difficulty matches the grade level"
  on_fail: "regenerate_until_valid"   # loop back to generation
  max_iterations: 5                   # guard against infinite loops

knowledge_sources:
  # Agent dynamically decides if external knowledge is required (Section 7).
  default_llm_only:
    - "llm_internal"
  when_search_skill_invoked:
    - "mcp_curriculum"
    - "web_search"
    - "wikipedia"
    - "llm_internal"
```

**The six steps:**

1. **Knowledge gathering.** The agent dynamically decides whether to use
   only its internal LLM knowledge or to invoke the "Curriculum Search Skill"
   (web search / Wikipedia / MCP servers) to gather country-specific and
   curriculum-appropriate facts.
2. **Quiz generation.** Ten multiple-choice questions, 3–5 options each,
   exactly one correct.
3. **Quality check (LLM-as-a-judge).** A second agent in a judge role
   verifies questions and answers for correctness and fit. On failure: loop
   back to step 2 until the quiz passes (bounded by `max_iterations`).
4. **Presentation.** The validated quiz is rendered; the user answers
   question by question.
5. **Result.** The score is shown. Depending on the grade, a friendly
   congratulation or an encouraging message. The user can navigate all
   questions and see the correct answers.
6. **Continue dialog.** The chat asks whether to continue and what
   difficulty to use next (same / easier / harder). Then back to step 1.

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

To address long generation times (10 to 20 seconds) and enhance user experience, a rich progressive visual loader is displayed during the quiz-creation state:

1. **Visual Elements**:
   - **Central Mascot**: The chosen learning buddy mascot (*Felix, Olivia, or Dino*) remains static and non-rotating in the center.
   - **Outer Indication**: An infinite rotating outer dashed ring conveys ongoing active background operations.
   - **Circular Overlay**: An SVG progress ring overlay that fills progressively from 0 degrees (0% progress) to 360 degrees (100% progress) using the mascot's brand color.

2. **Asymptotic Progression Formula**:
   - The progress percentage increments client-side at fixed 200ms intervals using a smooth mathematical asymptotic function to simulate activity without hitting a hard ceiling before the API responds:
     $$\text{NewProgress} = \text{CurrentProgress} + (98 - \text{CurrentProgress}) \times 0.035$$
   - This ensures the bar starts fast, then tapers off near 98% during very long waits, maintaining user patience.

3. **API Success Snapping**:
   - Immediately upon receiving the fully generated quiz payload, the progress bar snaps to 100% (360 degrees complete).
   - A visual buffer delay of 400ms is applied to let the full transition render smoothly before transitioning the user to the active quiz screen.

### 6.2 Adaptive Quiz Learning Progression & Dynamic Difficulty Localization

When the user finishes a quiz, they can choose to continue learning the same topic by selecting the primary action "Let's go for more questions" (or its localized equivalent). Rather than keeping a static difficulty level, the system dynamically adapts the quiz based on the user's previous score to deliver customized pedagogical pacing:

#### 6.2.1 Progression Modes (Score-Based Pacing)

1. **Reinforcement Mode (Score $\le$ 4 / 10)**:
   - **Pedagogical Goal**: Help the student master the content they struggled with.
   - **Behavior**: The agent shuffles the previous 10 questions and their options, repeating them so the student can focus on correcting their mistakes. Traditional duplicate-prevention filters are bypassed.
   - **Difficulty Rating**: `🌱 Easy` (mapped dynamically to user's language).

2. **Practice Mode (Score 5 - 7 / 10)**:
   - **Pedagogical Goal**: Consolidate understanding at the current level.
   - **Behavior**: The agent generates a new set of 10 standard-difficulty questions on the same topic. Duplication-prevention is recommended but not strictly enforced.
   - **Difficulty Rating**: `⭐ Medium` (mapped dynamically to user's language).

3. **Progression Mode (Score $\ge$ 8 / 10)**:
   - **Pedagogical Goal**: Push the student's boundaries and prevent repeating already mastered questions.
   - **Behavior**: The agent strictly enforces duplication-prevention, ensuring absolutely zero questions from the previous quiz are repeated.
     - **High Score (8 - 9 / 10)**: Generates 10 completely fresh questions at standard difficulty.
     - **Perfect Score (10 / 10)**: Significantly scales up the difficulty of the next quiz, introducing advanced concepts, trickier distractors, and deeper questions suitable for high-achieving students.
   - **Difficulty Rating**: 
     - 8 - 9 / 10: `⭐ Medium` (mapped dynamically).
     - 10 / 10: `🚀 Hard` (mapped dynamically).

#### 6.2.2 Dynamic Difficulty Localization

To prevent leakage of English terminology on non-English user interfaces, raw difficulty indicators received from the backend are mapped to localized labels before rendering on the screen (both in `#quiz-difficulty` on the quiz interface and `#summary-difficulty` on the final summary screen):

- **Deutsch (DE)** (# USER-FACING — DO NOT TRANSLATE):
  - `🌱 Easy` $\to$ `"🌱 Einfach"`
  - `⭐ Medium` $\to$ `"⭐ Mittel"`
  - `🚀 Hard` $\to$ `"🚀 Schwer"`

- **Português (PT)** (# USER-FACING — DO NOT TRANSLATE):
  - `🌱 Easy` $\to$ `"🌱 Fácil"`
  - `⭐ Medium` $\to$ `"⭐ Médio"`
  - `🚀 Hard` $\to$ `"🚀 Difícil"`

- **English (EN / Fallback)** (# USER-FACING — DO NOT TRANSLATE):
  - `🌱 Easy` $\to$ `"🌱 Easy"`
  - `⭐ Medium` $\to$ `"⭐ Medium"`
  - `🚀 Hard` $\to$ `"🚀 Hard"`

```gherkin
Feature: Adaptive learning progression and localized difficulty indicators

  Scenario: Reinforcement mode for low score
    Given the user finished a quiz with a score of 3 out of 10
    When they select "Let's go for more questions"
    Then the system triggers reinforcement mode
    And the generated quiz shuffles and repeats the previous questions
    And the difficulty is shown as localized "🌱 Easy" (e.g. "🌱 Einfach" in German)

  Scenario: High score triggers duplication prevention
    Given the user finished a quiz with a score of 9 out of 10
    When they select "Let's go for more questions"
    Then the system triggers progression mode
    And the generated quiz contains a completely fresh set of questions
    And none of the previous questions are duplicated
    And the difficulty is shown as localized "⭐ Medium" (e.g. "⭐ Mittel" in German)

  Scenario: Perfect score triggers hard difficulty escalation
    Given the user finished a quiz with a score of 10 out of 10
    When they select "Let's go for more questions"
    Then the system triggers progression mode with maximum escalation
    And the generated quiz has significantly harder questions
    And the difficulty is shown as localized "🚀 Hard" (e.g. "🚀 Schwer" in German)
```

### 6.3 Upfront Curriculum Validation & Mascot Age-Appropriateness Guidance

To prevent long loading delays, resource waste, and slow retry loops inside the quality checking (judge) phase, the agent incorporates an **Upfront Curriculum Validation** checkpoint.

#### 6.3.1 Early Compatibility Verification
Before transitioning from information gathering (`gather_and_route`) to quiz generation (`quiz_generation`), the system performs a lightweight validation check on the extracted variables: `grade`, `subject`, and `topic`.

1. **Execution**:
   - The validation runs as soon as all three fields are successfully parsed.
   - It executes a fast `gemini-2.5-flash` model call with `temperature=0.0` and a strict schema mapping.
   
2. **Schema Reference**:
   ```python
   class CurriculumCompatibility(BaseModel):
       is_compatible: bool
       explanation: str
       suggested_topics: list[str]
   ```

3. **Check Criteria**:
   - Is the chosen `topic` cognitively, pedagogically, and curriculum-wise appropriate for the requested school `grade` and `subject`?
   - Mismatches like *Grade 5 Math -> Differential Equations* or *Grade 2 Science -> Quantum Field Theory* are flagged as incompatible.

4. **Failure Resolution (Mascot Dialogue)**:
   - If `is_compatible` is `False`, quiz generation is aborted immediately.
   - The agent routes to `ask_more` rather than `generate_quiz`.
   - The system state for the incompatible `topic` is cleared so the user can provide a new one.
   - The chosen mascot (*Felix*, *Olivia*, or *Dino*) delivers a friendly, encouraging explanation in the user's selected language, noting that the topic is usually learned by older students.
   - The mascot presents the `suggested_topics` (e.g. *Fractions*, *Long Division* for Grade 5 Math) as kid-friendly alternative choices.

5. **Success Routing**:
   - If `is_compatible` is `True`, the graph proceeds directly to quiz generation with zero extra user interaction.

```gherkin
Feature: Upfront Curriculum Validation

  Scenario: Incompatible grade and topic mismatch
    Given the user has selected Grade "5"
    And Subject "Math"
    And entered "Differential Equations" as the Topic
    When the upfront curriculum validation check is performed
    Then the compatibility check flags the inputs as incompatible (is_compatible = false)
    And quiz generation is aborted before starting
    And the mascot explains the mismatch in the user's language
    And provides 2 to 3 age-appropriate topic alternatives (e.g. "Fractions", "Decimals")
    And the state's topic is reset to allow a new choice

  Scenario: Compatible grade and topic
    Given the user has selected Grade "5"
    And Subject "Math"
    And entered "Fractions" as the Topic
    When the upfront curriculum validation check is performed
    Then the compatibility check flags the inputs as compatible (is_compatible = true)
    And the system transitions seamlessly to quiz generation
```

---


## 7. Dynamic Curriculum-Gathering Skill

There is **no manual curriculum toggle on the UI**. Instead, the ADK Agent is equipped with a specialized **"Curriculum Search Skill"** tool. The agent dynamically decides whether to generate the quiz using purely its internal LLM knowledge or to invoke the search skill to query external resources (MCP servers, web search, Wikipedia) for localized curriculum facts.

```yaml
# curriculum-skill-config.yaml
curriculum_skill:
  decision_logic:
    criteria: "Is the internal knowledge sufficient and up-to-date for country-specific (DE/BR/US) school curriculum?"
    threshold_on_uncertainty: "invoke_search_skill"
  behavior:
    internal_only:
      knowledge_sources: ["llm_internal"]
    skill_invoked:
      country_source: "selected_or_detected_locale"  # see Section 4
      knowledge_sources: ["mcp_curriculum", "web_search", "wikipedia"]
  fallback:
    # If search skill fails or returns no relevant data:
    on_search_failed: "fall_back_to_llm_internal"
    inform_user: false  # Silent fallback, user-experience remains seamless
```

```gherkin
Feature: Autonomous Curriculum Search Skill Decision

  Scenario: Agent uses internal LLM knowledge
    Given the user requests a quiz on a standard school topic (e.g., Grade 5, Math, Fractions)
    When the workflow starts
    Then the agent decides that its internal knowledge is highly accurate and sufficient
    And the agent does not call the external curriculum search skill
    And the quiz is generated instantly using internal knowledge

  Scenario: Agent dynamically invokes the search skill
    Given the user requests a quiz with country-specific or complex topics (e.g., Grade 9, History, Weimar Republic in Germany)
    When the workflow starts
    Then the agent decides it needs to verify or gather localized curriculum guidelines
    And the agent invokes the "Curriculum Search Skill"
    And the workflow queries the external search/MCP tools for Germany (DE)
    And the quiz is generated using the retrieved curriculum data

  Scenario: Agent dynamically invokes the search skill for US curriculum
    Given the user requests a quiz with US-specific topics (e.g., Grade 8, History, American Civil War in the US)
    When the workflow starts
    Then the agent decides it needs to verify or gather localized US curriculum standards (Common Core or State standards)
    And the agent invokes the "Curriculum Search Skill"
    And the workflow queries the external search/MCP tools for the United States (US)
    And the quiz is generated using the retrieved US curriculum data

  Scenario: Agent dynamically invokes the search skill for Brazilian curriculum (Sambaquis)
    Given the user requests a quiz with Brazilian-specific archaeological topics (e.g., Grade 6, History, Sambaquis no Brasil)
    When the workflow starts
    Then the agent decides it needs to verify or gather localized Brazilian BNCC curriculum standards
    And the agent invokes the "Curriculum Search Skill"
    And the workflow queries the external search/MCP tools (including pt.wikipedia.org/wiki/Sambaquis_no_Brasil) for Brazil (BR)
    And the quiz is generated using the retrieved Brazilian historical curriculum data

  Scenario: Search skill fallback
    Given the agent decides to invoke the search skill
    When the search or MCP tools return no relevant data or fail
    Then the workflow seamlessly falls back to the agent's internal LLM knowledge
    And the quiz is generated without interrupting the user's flow
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
    Then the quiz, its questions, and answers are written to a log for admin review
    And the aggregated feedback counts are updated
```

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
  - **Deutsch (DE)**: `"Vielen Dank für deine Bewertung, wir werden das Quiz prüfen und versuchen Quiz Buddy zu verbessern! 🦊"`
  - **Português (PT)**: `"Obrigado pela sua avaliação, nós vamos analisar o quiz e tentar melhorar o Quiz Buddy! 🦊"`
  - **English (ENFallback)**: `"Thank you for your rating, we will check the quiz and try to improve Quiz Buddy! 🦊"`

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
    cloud_storage: true     # second instance stored in the cloud
    direct_start: true      # recipient starts immediately, no chat questions
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
    And a second instance is stored in the cloud
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
   - The footer attribution formerly displaying static English/German text must dynamically resolve `"Created with Quiz Buddy"` based on the active export language:
     - **Deutsch (DE)**: `"Erstellt mit Quiz Buddy"`
     - **Português (PT)**: `"Criado com Quiz Buddy"`
     - **English (EN)**: `"Created with Quiz Buddy"`

---

## 10. Security checkpoint & Dynamic Security Configuration

Every user prompt is screened **before** it reaches the LLM. In ADK 2.0, this is implemented as an upstream guard node and/or via the `BeforeAgentCallback` interface — not by overriding internal execution methods.

To support making the GitHub repository **public** without exposing defensive configurations (heuristics, classification prompts, system instructions, regexes, and sensitive keyword list), the application uses a **Private Firestore Security Configuration**.

### 10.1 Firestore Configuration Schema

All security rules, prompts, regexes, and keywords are stored in a private Firestore document: `system_config/security`. The codebase loads this document dynamically at runtime, keeping the public code clean and safe from reverse-engineering by potential attackers.

```yaml
# Firestore Document: system_config/security
# (Schema reference only. All actual classification prompts, regexes, and sensitive blocklist keywords are stored exclusively inside the private Firestore database)
classification_prompt: |
  <SYSTEM_CLASSIFICATION_PROMPT_TEMPLATE>
  # Private system instructions directing a fast classifier model to categorize input as SAFE, OFF_TOPIC, or MALICIOUS.

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
```

### 10.2 Guardrail Execution Workflow

The security checkpoint executes in a multi-stage fashion within `BeforeAgentCallback`:

1. **Lazy Loading & Caching**: The callback fetches `system_config/security` from Firestore. To avoid sub-second latency overhead on every user message, it caches the configuration in memory with a short TTL (e.g., 5 minutes) or simple in-memory session lifetime.
2. **Stage 1: Local Regex & Keyword Scanning (Fast Filter)**:
   - Perform case-insensitive checks of the user's prompt against `blocklist_keywords`.
   - Evaluate the prompt against `injection_regexes`.
   - If a match is found, immediately classify as `MALICIOUS` and short-circuit.
3. **Stage 2: LLM Classification (Semantic Filter)**:
   - If Stage 1 passes, send the prompt to a fast, cost-effective classifier model using the `classification_prompt` template fetched from Firestore.
   - If the classifier returns `MALICIOUS` or `OFF_TOPIC`, block and short-circuit.
4. **Action on Violation**:
   - **Block Prompt**: The prompt is not sent to the main Quiz Generator.
   - **Log Security Event**: If classified as `MALICIOUS`, write a log entry to the `security_events` Firestore collection (storing timestamp, blocked input, violation type, e.g. `RegexMatch`, `KeywordMatch`, `ClassifierBlock`, and anonymous ID).
   - **Friendly Blocked Response**: Return the corresponding localized message from the dynamic `responses` config.

### 10.3 The Sheriff Guard (Automated Rate-Limiting & Auto-Banning)

To prevent malicious actors from repeatedly attempting prompt injections and wasting our precious LLM token budget, the security checkpoint includes an automated defense subsystem code-named **The Sheriff Guard** 🤠.

The Sheriff operates directly inside `BeforeAgentCallback` and uses secure, GDPR/LGPD-compliant hashed IP signatures:

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
    Then this data reaches neither the LLM model nor the logs

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

## 11. MCP servers

MCP servers serve step 1 (knowledge gathering). The **curriculum** server
and other search tools are part of the "Curriculum Search Skill" and are
queried dynamically when the agent decides external grounding is required.

```yaml
# mcp-config.yaml
mcp_servers:
  - name: "curriculum"
    required_when: "agent_invokes_curriculum_search_skill == true"
    purpose: "Country-specific curriculum content (e.g. DE vs. BR)"
    note: >
      Select the concrete MCP server/provider that exposes curriculum data
      for the supported countries. Country derived from selected/detected
      locale.
  - name: "web_search"
    optional: true
    purpose: "Current facts; supports curriculum lookups"
  - name: "wikipedia"
    optional: true
    purpose: "Solid, free knowledge source for school topics"
  - name: "filesystem"
    optional: true
    purpose: "Bring in your own curriculum docs / question banks"
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
are sensitive for minors (see Section 13).

**ADK 2.0 implementation hint:** fits well as an `AfterAgentCallback`
(accumulates tokens after each LLM call) combined with an upstream check in
the guard node that verifies both the personal and global budget **before**
quiz creation. ADK 2.0 tracks token usage natively; read and aggregate
those values.

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
      Heute waren besonders viele fleißige Lernende unterwegs! 🦉
      Bitte versuch es morgen noch einmal – dann geht es weiter.

  storage:
    persistence: "required"             # e.g. Firestore / Redis / Cloud SQL
    note: "Store anonymized, with a short retention limit."
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

## 13. Deployment

```yaml
# deployment-config.yaml
deployment:
  framework: "Google ADK 2.0"        # e.g. 2.3.x
  python: "3.10+"
  target: "Vertex AI Agent Engine"   # Agent Runtime
  alternatives: ["Cloud Run", "GKE"]
  note: >
    Verify the exact CLI syntax against the current ADK docs, as 2.x
    commands are still evolving. Deployment target is one-command
    deployment to Vertex AI Agent Engine.
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
  for minors — do **not** use them to identify users for the token limit;
  use them only briefly and non-persistently for language geolocation
  (Section 4).
- The security checkpoint already prevents personal data from reaching the
  LLM or logs (Section 10).
- Admin logs (thumbs-down, security events) should be anonymized with a
  retention limit.
- A cookie/storage notice may be required once client-side ids are set.
- Depending on the design, parental consent may be legally required for
  minors.

> This is a technical note, not legal advice. Before going live, have data
> protection compliance reviewed by a qualified person.
