# Implementation Plan: Quiz Buddy (Prüfungsvorbereitung für Schüler)

We have completed the `/grill-me` alignment interview! Based on your selections and requirements, here is the finalized architecture and step-by-step implementation blueprint.

---

## 🏛️ System Architecture

```mermaid
graph TD
    %% Presentation Layer
    subgraph Presentation ["Presentation Layer (FastAPI served SPA)"]
        UI["Child-Friendly Web UI<br>(HTML5 / Tailwind CSS / Vanilla JS)"]
        UI_Theme["Theme Controller<br>(Playful vs. Cool Modes)"]
        UI_Quiz["Quiz Engine<br>(Interactive 10-Question Wizard)"]
        UI_Share["Share Handler<br>(Social & Freeze-state links)"]
    end

    %% Agent Layer
    subgraph Agent ["Agent Layer (Google ADK 2.0)"]
        FastAPI["FastAPI Backend App<br>(fast_api_app.py)"]
        BeforeCB["BeforeAgentCallback<br>(Security Guardrail Check)"]
        AfterCB["AfterAgentCallback<br>(Token Budget Logging)"]
        Workflow["ADK 2.0 Workflow Graph<br>(agent.py)"]
        
        %% Workflow sub-graph
        Workflow_Start["START Node<br>(Inputs: Grade, Subject, Topic, Locale)"]
        Workflow_Gather["Knowledge Gathering Node<br>(LLM + Search / Wikipedia tools)"]
        Workflow_Gen["Quiz Generator Node<br>(LlmAgent with Output Schema)"]
        Workflow_Judge["LLM-as-a-Judge Node<br>(Strict Validation Agent)"]
    end

    %% Persistence Layer
    subgraph Cloud ["Persistence Layer (Google Cloud)"]
        Firestore["Google Cloud Firestore<br>(Native Mode)"]
        FS_Quizzes["Quizzes Collection<br>(Shared frozen quizzes)"]
        FS_Budgets["Token Budgets Collection<br>(Daily client token use)"]
        FS_Feedback["Feedback Collection<br>(Thumbs-down logs + aggregated thumbs-up counter)"]
        FS_Config["system_config Collection<br>(Private security configurations)"]
        FS_Events["security_events Collection<br>(Logged malicious prompt violations)"]
    end

    %% Connections
    UI <-->|HTTP API / JSON| FastAPI
    FastAPI <--> BeforeCB
    FastAPI <--> AfterCB
    FastAPI <--> Workflow
    
    BeforeCB -.->|1. Verify Token Budget| FS_Budgets
    BeforeCB -.->|2. Fetch dynamic rules| FS_Config
    BeforeCB -.->|3. Log security violations| FS_Events
    AfterCB -.->|Log Session Token Usage| FS_Budgets
    
    Workflow_Start --> Workflow_Gather --> Workflow_Gen --> Workflow_Judge
    Workflow_Judge -.->|On Fail: Loop up to 5x| Workflow_Gen
    
    FastAPI <-->|Save/Fetch Quizzes| FS_Quizzes
    FastAPI <-->|Log Feedback| FS_Feedback
```

---

## 🛠️ Design Decisions Summary

| Dimension | Selected Design | Details |
| :--- | :--- | :--- |
| **Database** | **Google Cloud Firestore (Native Mode)** | Fully serverless document store to house daily token budgets (by client anonymous ID), feedback logs (thumbs-down log and atomic thumbs-up count), frozen shared quizzes, dynamic security configurations (`system_config`), and logged security violations (`security_events`). |
| **Curriculum Source** | **Autonomous Curriculum Search Skill** | The agent dynamically decides if external search/Wikipedia grounding is needed, then aligns the quiz with Germany, Brazil, or USA's school curriculum standards using its internal LLM knowledge. |
| **Quiz Structure** | **Entire Quiz JSON Generated at Once** | The ADK Agent outputs a complete structured JSON containing all 10 questions, choices, explanations, and answers. The frontend handles the step-by-step interactive navigation, keeping the API super clean. |
| **Guardrails & Budgets** | **Callback Hooks (`Before` / `After`)** | Security Checkpoint runs inside `BeforeAgentCallback`. Token budget tracking and Firestore logging run inside `AfterAgentCallback`. |
| **Web Frontend** | **FastAPI-Served Premium SPA** | A stunning, responsive Single-Page Application served directly by our FastAPI app. It features modern Rounded layouts, Glassmorphism, animations, and active mascots (*Felix*, *Olivia*, *Dino*). Run everything with one command! |

---

## 📅 Implementation Roadmap

### Phase 1: Database Setup & Callbacks (Backend Foundation)
- [ ] Install `google-cloud-firestore` dependency.
- [ ] Implement Firestore repository classes with explicit schema mappings for:
  - **Shared Quizzes**: Storing and retrieving frozen JSON quiz objects at path `quizzes/{quiz_id}`.
  - **Token Budgets**: Managing daily reading/writing of token counters at paths `budgets/{anonymous_id}` and `budgets/global`.
  - **Feedback Logs**: Storing detailed logs of thumbs-down responses under `feedback_logs/{log_id}` and atomically incrementing the global positive feedback count at `feedback_metrics/satisfaction`.
  - **Dynamic Security Configuration**: Fetching dynamic parameters from the private configuration document `system_config/security`. Document schema must map to:
    * `classification_prompt` (string): Private system instructions for the lightweight LLM safety classifier.
    * `blocklist_keywords` (array of strings): High-priority terms to catch via local scanning.
    * `injection_regexes` (array of strings): Compiled regex expressions for injection patterns.
    * `responses` (map of localized strings): Friendly block messages per language (e.g. `injection_de`, `off_topic_de`).
  - **Security Events**: Writing audit records to the private collection `security_events/{event_id}` upon violation:
    * `anonymous_id` (string): The anonymous visitor session identifier.
    * `blocked_input` (string): The raw blocked user prompt (completely isolated from the main LLM pipelines).
    * `timestamp` (timestamp): Precise timestamp of the violation.
    * `violation_type` (string): The classification category (e.g., `RegexMatch`, `KeywordMatch`, `ClassifierBlock`).
- [ ] Implement `BeforeAgentCallback` to perform the dynamic **Security Checkpoint** and **Token Budget Verification**:
  - Lazily load and cache the `system_config/security` document in memory with a short TTL (e.g. 5 minutes) to protect against DB query latency.
  - Apply multi-stage validation: first run fast local keyword/regex scans, then secondary LLM guardrail classification using the dynamic system prompt.
  - If a prompt is malicious (e.g. administrative command override or database deletion attempt), block execution, log the violation to the private `security_events` Firestore collection, and return a localized friendly error.
  - Verify that the daily token budgets (both user-level and global) are within bounds before allowing the session to invoke the LLM.
- [ ] Implement `AfterAgentCallback` for **Token Budget Accumulation** (extracting raw ADK session token usage and incrementing the client/global Firestore counters).

### Phase 2: Core ADK 2.0 Workflow Graph (Agent Logic)
- [ ] Define robust Pydantic models for the Quiz format:
  - `QuizQuestion`: Question text, 3–5 options, index of the correct option, explanation.
  - `Quiz`: Array of 10 `QuizQuestion`s.
- [ ] Implement the **Workflow Graph** in `app/agent.py`:
  - **Knowledge Gathering Node**: Dynamically decides whether to use internal LLM knowledge or execute the "Curriculum Search Skill" (web search or Wikipedia tools).
  - **Quiz Generation Node**: `LlmAgent` with `Quiz` output schema.
  - **LLM-as-a-Judge Node**: Evaluates the generated quiz. If checks fail, loops back to the generator (up to 5 iterations).

### Phase 3: Premium Single-Page Application (Frontend)
- [ ] Create `app/static/` and write a highly polished `index.html` featuring:
  - Google Fonts (`Baloo 2` and `Nunito`).
  - Playful vs. Cool theme toggles.
  - Geolocation detection & Language flag dropdown (Deutsch / Português / English).
  - Clean animated quiz navigation cards (glassmorphic styling, progress bars).
  - Interactive mascots (*Felix der Fuchs*, *Olivia die Eule*, *Dino der Drache*).
  - Local HTML Export & "Share Link" creation.
- [ ] Update `app/fast_api_app.py` to serve the static frontend, handle custom routes (`/feedback`, `/quiz/{quiz_id}`, `/share`), and mount the ADK app.

---

> [!TIP]
> This structure ensures maximum performance and a beautiful visual presentation while remaining 100% compliant with standard ADK 2.0 practices.
