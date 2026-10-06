# Memory Retrieval Quantitative Evaluation Report

- Date: 2026-09-30T05:12:21.492763+00:00
- Total Scenarios: 10 (9 evaluable, 1 boundary)
- Total Evaluated Queries: 27

## 1. Executive Summary & Aggregate Metrics

| Metric                         | Value   | Denominator / Formula                         | Interpretation  |
| :----------------------------- | :------ | :-------------------------------------------- | :-------------- |
| **True Positives (TP)**        | 18      | Relevant items retrieved                      | Correct matches |
| **False Positives (FP)**       | 0       | Irrelevant/excluded retrieved                 | False context   |
| **False Negatives (FN)**       | 3       | Expected items not retrieved                  | Missed memories |
| **Precision**                  | 100.00% | TP / (TP + FP) = 18/18                        | Purity          |
| **Recall**                     | 85.71%  | TP / (TP + FN) = 18/21                        | Completeness    |
| **False Retrieval Rate**       | 0.00%   | FP / (TP + FP) = 0/18                         | False rate      |
| **Miss Rate**                  | 14.29%  | FN / (TP + FN) = 3/21                         | Miss rate       |
| **Query False Retrieval Rate** | 0.00%   | Queries with FP>0 / Total Queries = 0/27      | Queries with FP |
| **Query Miss Rate**            | 6.67%   | Queries with FN>0 / Queries with Exp>0 = 1/15 | Queries with FN |

## 2. Condition Coverage Matrix (All 10 Q06 Target Conditions)

| Condition             | Evaluable |   Status    | TP  | FP  | FN  | Precision | Recall | FRR  | Miss Rate | Notes                                                                                                                                                                                                                        |
| :-------------------- | :-------: | :---------: | :-: | :-: | :-: | :-------: | :----: | :--: | :-------: | :--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| explicit fact         |    Yes    |    PASS     |  2  |  0  |  0  |  100.0%   | 100.0% | 0.0% |   0.0%    | Observed FP=0, FN=0                                                                                                                                                                                                          |
| pending implicit fact |    Yes    |    PASS     |  1  |  0  |  0  |  100.0%   | 100.0% | 0.0% |   0.0%    | Observed FP=0, FN=0                                                                                                                                                                                                          |
| sensitive fact        |    Yes    |    PASS     |  1  |  0  |  0  |  100.0%   | 100.0% | 0.0% |   0.0%    | Observed FP=0, FN=0                                                                                                                                                                                                          |
| stale/superseded fact |    Yes    |    PASS     |  2  |  0  |  0  |  100.0%   | 100.0% | 0.0% |   0.0%    | Observed FP=0, FN=0                                                                                                                                                                                                          |
| shared-joke relevance |    Yes    |    PASS     |  2  |  0  |  0  |  100.0%   | 100.0% | 0.0% |   0.0%    | Observed FP=0, FN=0                                                                                                                                                                                                          |
| absent source         |    No     |  BOUNDARY   |  -  |  -  |  -  |     -     |   -    |  -   |     -     | MemoryRetriever assumes valid database records have sources; source presence is enforced by SQLiteMemoryRepository and MemoryService write contracts. Records cannot be created without sources via repository/service APIs. |
| token-budget pressure |    Yes    | OBSERVED_FN |  5  |  0  |  3  |  100.0%   | 62.5%  | 0.0% |   37.5%   | Observed FP=0, FN=3                                                                                                                                                                                                          |
| correction            |    Yes    |    PASS     |  3  |  0  |  0  |  100.0%   | 100.0% | 0.0% |   0.0%    | Observed FP=0, FN=0                                                                                                                                                                                                          |
| forget/delete         |    Yes    |    PASS     |  1  |  0  |  0  |  100.0%   | 100.0% | 0.0% |   0.0%    | Observed FP=0, FN=0                                                                                                                                                                                                          |
| restart               |    Yes    |    PASS     |  1  |  0  |  0  |  100.0%   | 100.0% | 0.0% |   0.0%    | Observed FP=0, FN=0                                                                                                                                                                                                          |

## 3. Budget Pressure

- `q_budget_generous` (700 budget): TP=4, FP=0, FN=0; miss rate 0.00%.
- `q_budget_constrained` (25 budget): TP=1, FP=0, FN=3; miss rate 75.00%.

## 4. Policy, Deletion, and Privacy Exclusions

- **Sensitive fact**: MemoryPolicy filters PrivacyLevel.SENSITIVE at retrieval (FP=0).
- **Pending implicit fact**: Unconfirmed proposals stay in memory_proposals (FP=0).
- **Stale/superseded fact**: Superseded records are purged from FTS5 index (FP=0).
- **Forget/tombstone**: Tombstoned records trigger FTS deletion (FP=0).
- **Restart**: Preserves active facts and excludes superseded/tombstoned identically.

## 5. Per-Scenario Query Breakdown

### Explicit Fact Retrieval (`explicit fact`)

- Evaluated Queries: 3
- Scenario Aggregate: TP=2, FP=0, FN=0, Precision=100.0%, Recall=100.0%, Miss Rate=0.0%

| Query ID                | Label                                       | Expected Keys         | Retrieved Keys        | TP  | FP  | FN  |  P   |  R   | FRR | MR  |
| :---------------------- | :------------------------------------------ | :-------------------- | :-------------------- | :-: | :-: | :-: | :--: | :--: | :-: | :-: |
| `q_explicit_positive`   | Direct keyword query for explicit name      | rec_user_name         | rec_user_name         |  1  |  0  |  0  | 100% | 100% | 0%  | 0%  |
| `q_explicit_negative`   | Completely unrelated culinary query         | (empty)               | (empty)               |  0  |  0  |  0  | 100% | 100% | 0%  | 0%  |
| `q_explicit_distractor` | Query targeting distractor drink preference | rec_distractor_coffee | rec_distractor_coffee |  1  |  0  |  0  | 100% | 100% | 0%  | 0%  |

### Pending Implicit Fact Exclusion (`pending implicit fact`)

- Evaluated Queries: 3
- Scenario Aggregate: TP=1, FP=0, FN=0, Precision=100.0%, Recall=100.0%, Miss Rate=0.0%

| Query ID             | Label                                                               | Expected Keys         | Retrieved Keys        | TP  | FP  | FN  |  P   |  R   | FRR | MR  |
| :------------------- | :------------------------------------------------------------------ | :-------------------- | :-------------------- | :-: | :-: | :-: | :--: | :--: | :-: | :-: |
| `q_pending_probe`    | Query attempting to retrieve unconfirmed pending reading preference | (empty)               | (empty)               |  0  |  0  |  0  | 100% | 100% | 0%  | 0%  |
| `q_pending_negative` | Unrelated weather query with no matching memories                   | (empty)               | (empty)               |  0  |  0  |  0  | 100% | 100% | 0%  | 0%  |
| `q_pending_active`   | Query for confirmed active OS preference                            | rec_active_distractor | rec_active_distractor |  1  |  0  |  0  | 100% | 100% | 0%  | 0%  |

### Sensitive Fact Policy Exclusion (`sensitive fact`)

- Evaluated Queries: 3
- Scenario Aggregate: TP=1, FP=0, FN=0, Precision=100.0%, Recall=100.0%, Miss Rate=0.0%

| Query ID                 | Label                                              | Expected Keys    | Retrieved Keys   | TP  | FP  | FN  |  P   |  R   | FRR | MR  |
| :----------------------- | :------------------------------------------------- | :--------------- | :--------------- | :-: | :-: | :-: | :--: | :--: | :-: | :-: |
| `q_sensitive_probe`      | Sensitive phone number probe (redacted in results) | (empty)          | (empty)          |  0  |  0  |  0  | 100% | 100% | 0%  | 0%  |
| `q_sensitive_distractor` | Query for non-sensitive private city fact          | rec_private_city | rec_private_city |  1  |  0  |  0  | 100% | 100% | 0%  | 0%  |
| `q_sensitive_negative`   | Unrelated hiking query                             | (empty)          | (empty)          |  0  |  0  |  0  | 100% | 100% | 0%  | 0%  |

### Stale/Superseded Fact Exclusion (`stale/superseded fact`)

- Evaluated Queries: 3
- Scenario Aggregate: TP=2, FP=0, FN=0, Precision=100.0%, Recall=100.0%, Miss Rate=0.0%

| Query ID                  | Label                                             | Expected Keys        | Retrieved Keys       | TP  | FP  | FN  |  P   |  R   | FRR | MR  |
| :------------------------ | :------------------------------------------------ | :------------------- | :------------------- | :-: | :-: | :-: | :--: | :--: | :-: | :-: |
| `q_superseded_new`        | Query for updated residence in Shanghai           | rec_city_new         | rec_city_new         |  1  |  0  |  0  | 100% | 100% | 0%  | 0%  |
| `q_superseded_old_probe`  | Probe query targeting outdated Hangzhou residence | (empty)              | (empty)              |  0  |  0  |  0  | 100% | 100% | 0%  | 0%  |
| `q_superseded_distractor` | Distractor hobby query                            | rec_distractor_hobby | rec_distractor_hobby |  1  |  0  |  0  | 100% | 100% | 0%  | 0%  |

### Shared Joke Relevance and Attribution (`shared-joke relevance`)

- Evaluated Queries: 3
- Scenario Aggregate: TP=2, FP=0, FN=0, Precision=100.0%, Recall=100.0%, Miss Rate=0.0%

| Query ID            | Label                                                  | Expected Keys     | Retrieved Keys    | TP  | FP  | FN  |  P   |  R   | FRR | MR  |
| :------------------ | :----------------------------------------------------- | :---------------- | :---------------- | :-: | :-: | :-: | :--: | :--: | :-: | :-: |
| `q_joke_positive`   | Query triggering shared joke by keyword                | rec_joke_popsicle | rec_joke_popsicle |  1  |  0  |  0  | 100% | 100% | 0%  | 0%  |
| `q_joke_negative`   | Wakeup alarm query unrelated to jokes                  | (empty)           | (empty)           |  0  |  0  |  0  | 100% | 100% | 0%  | 0%  |
| `q_joke_distractor` | Dessert query matching food preference instead of joke | rec_ice_cream     | rec_ice_cream     |  1  |  0  |  0  | 100% | 100% | 0%  | 0%  |

### Absent Source Provenance Invariant (`absent source`)

> [!NOTE] Boundary Limitation: MemoryRetriever assumes valid database records have sources; source presence is enforced by SQLiteMemoryRepository and MemoryService write contracts. Records cannot be created without sources via repository/service APIs.

### Token-Budget Pressure and Nonzero Miss Rate (`token-budget pressure`)

- Evaluated Queries: 3
- Scenario Aggregate: TP=5, FP=0, FN=3, Precision=100.0%, Recall=62.5%, Miss Rate=37.5%

| Query ID               | Label                                                          | Expected Keys                                                            | Retrieved Keys                                                           | TP  | FP  | FN  |  P   |  R   | FRR | MR  |
| :--------------------- | :------------------------------------------------------------- | :----------------------------------------------------------------------- | :----------------------------------------------------------------------- | :-: | :-: | :-: | :--: | :--: | :-: | :-: |
| `q_budget_generous`    | Habits query with generous token budget (700 tokens)           | rec_habit_coffee, rec_habit_rest, rec_habit_keyboard, rec_habit_pomodoro | rec_habit_coffee, rec_habit_keyboard, rec_habit_pomodoro, rec_habit_rest |  4  |  0  |  0  | 100% | 100% | 0%  | 0%  |
| `q_budget_constrained` | Habits query with tightly constrained token budget (25 tokens) | rec_habit_coffee, rec_habit_rest, rec_habit_keyboard, rec_habit_pomodoro | rec_habit_keyboard                                                       |  1  |  0  |  3  | 100% | 25%  | 0%  | 75% |
| `q_budget_negative`    | Physics query completely unrelated to user habits              | (empty)                                                                  | (empty)                                                                  |  0  |  0  |  0  | 100% | 100% | 0%  | 0%  |

### Memory Correction Superseding (`correction`)

- Evaluated Queries: 3
- Scenario Aggregate: TP=3, FP=0, FN=0, Precision=100.0%, Recall=100.0%, Miss Rate=0.0%

| Query ID                  | Label                                                  | Expected Keys     | Retrieved Keys    | TP  | FP  | FN  |  P   |  R   | FRR | MR  |
| :------------------------ | :----------------------------------------------------- | :---------------- | :---------------- | :-: | :-: | :-: | :--: | :--: | :-: | :-: |
| `q_correction_query`      | Query for corrected programming language preference    | rec_lang_python   | rec_lang_python   |  1  |  0  |  0  | 100% | 100% | 0%  | 0%  |
| `q_correction_old_probe`  | Probe query specifically targeting old Rust preference | rec_lang_python   | rec_lang_python   |  1  |  0  |  0  | 100% | 100% | 0%  | 0%  |
| `q_correction_distractor` | Query for code editor distractor                       | rec_editor_vscode | rec_editor_vscode |  1  |  0  |  0  | 100% | 100% | 0%  | 0%  |

### Forget/Tombstone Deletion Exclusion (`forget/delete`)

- Evaluated Queries: 3
- Scenario Aggregate: TP=1, FP=0, FN=0, Precision=100.0%, Recall=100.0%, Miss Rate=0.0%

| Query ID             | Label                                               | Expected Keys   | Retrieved Keys  | TP  | FP  | FN  |  P   |  R   | FRR | MR  |
| :------------------- | :-------------------------------------------------- | :-------------- | :-------------- | :-: | :-: | :-: | :--: | :--: | :-: | :-: |
| `q_forget_probe`     | Query targeting forgotten milk sleep aid            | (empty)         | (empty)         |  0  |  0  |  0  | 100% | 100% | 0%  | 0%  |
| `q_forget_surviving` | Query targeting un-forgotten piano music preference | rec_music_sleep | rec_music_sleep |  1  |  0  |  0  | 100% | 100% | 0%  | 0%  |
| `q_forget_negative`  | Completely unrelated automotive maintenance query   | (empty)         | (empty)         |  0  |  0  |  0  | 100% | 100% | 0%  | 0%  |

### Database Restart and Durability (`restart`)

- Evaluated Queries: 3
- Scenario Aggregate: TP=1, FP=0, FN=0, Precision=100.0%, Recall=100.0%, Miss Rate=0.0%

| Query ID              | Label                                              | Expected Keys         | Retrieved Keys        | TP  | FP  | FN  |  P   |  R   | FRR | MR  |
| :-------------------- | :------------------------------------------------- | :-------------------- | :-------------------- | :-: | :-: | :-: | :--: | :--: | :-: | :-: |
| `q_restart_active`    | Post-restart query for active sunflower preference | rec_restart_sunflower | rec_restart_sunflower |  1  |  0  |  0  | 100% | 100% | 0%  | 0%  |
| `q_restart_stale`     | Post-restart query for superseded tulip fact       | (empty)               | (empty)               |  0  |  0  |  0  | 100% | 100% | 0%  | 0%  |
| `q_restart_forgotten` | Post-restart query for tombstoned peony fact       | (empty)               | (empty)               |  0  |  0  |  0  | 100% | 100% | 0%  | 0%  |
