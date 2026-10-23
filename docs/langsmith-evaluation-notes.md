# LangSmith Offline Evaluation: Research Notes

Researched 2026-10-22 from docs.langchain.com through the LangChain docs MCP (29 lookups).
Items marked **[not in docs]** aren't on docs.langchain.com and must be checked in the openevals /
agentevals GitHub READMEs or the SDK reference.

---

## 1. Datasets

### Data model
- An example has `inputs`, `outputs` (reference outputs) and `metadata`, plus `id`, `name`,
  `dataset_id`, `source_run_id`, timestamps. (langsmith/example-data-format)
- **Inputs** go to the target. **Reference outputs** go only to evaluators, never to the app.
  **Metadata** is for filtered views and grouping. (langsmith/evaluation-concepts)
- Reference outputs can hold free-form **assertions**, read as `reference_outputs["assertions"]`.

### Creating datasets and examples (langsmith/manage-datasets-programmatically)
- `client.create_dataset(dataset_name=..., description=...)`
- `client.create_examples(dataset_id=..., examples=[{"inputs": {...}, "outputs": {...}, "metadata": {...}}])`.
  This form needs `langsmith>=0.3.13`. `dataset_name=` also works.
- `create_example` for one example; older column form `create_examples(inputs=[...], outputs=[...])`.
- Other sources: `upload_csv`, `upload_dataframe`, building from `list_runs(...)` traces,
  `clone_public_dataset`.

### Idempotency
- Documented pattern: `if not client.has_dataset(dataset_name=...): create_dataset(...); create_examples(...)`.
  (langsmith/evaluate-complex-agent)
- Creating the same dataset name twice returns a 409 conflict; it does not upsert.
- **[not in docs]** No upsert for examples. Calling `create_examples` again duplicates them; to change
  existing examples use `list_examples` + `update_examples`.

### Reading and filtering
- `list_datasets(dataset_name=...)`, `list_datasets(dataset_name_contains=...)`.
- `list_examples(dataset_name=...)`, `list_examples(example_ids=[...])`.
- `list_examples(dataset_name=..., metadata={"category": "faq"})`: containment match.
- Structured metadata filter (SDK ≥0.1.83): `filter='and(has(metadata, ...), exists(metadata, "x"))'`.
- By split: `list_examples(dataset_name=..., splits=["test"])`.
- By version: `list_examples(dataset_name=..., as_of="latest" | "<tag>" | <datetime>)`. (langsmith/manage-datasets)

### Updating
- `client.update_example(example_id=..., inputs=..., outputs=..., metadata=..., split=...)`
- `client.update_examples(example_ids=[...], inputs=[...], outputs=[...], metadata=[...], splits=[...])`

### Versions, tags, splits
- Every add/update/delete creates a new dataset version (by timestamp).
- Tag a version: `client.update_dataset_tag(dataset_name=..., as_of=<datetime>, tag="prod")`.
- Evaluate on a tagged version with `data=client.list_examples(dataset_name=..., as_of="prod")`.
  The docs recommend pinning a version in CI so dataset edits don't break the pipeline.
- Splits: high-level groups (for example one per category); an example can be in several splits.
  "Use splits for high-level organizational grouping, and metadata for per-example information."
  (langsmith/evaluation-concepts)

### Schemas
- Optional JSON Schema on the dataset, defined in the UI, with prebuilt message/tool types and
  transformations (`remove_system_messages`, `convert_to_openai_message`, ...).
  (langsmith/dataset-json-types, langsmith/dataset-transformations)

### Building good datasets (langsmith/evaluation-concepts)
- Start manually curated: **10–20 high-quality examples** covering common cases and edge cases.
- Then add real traces (bad feedback, errors, slow runs), then synthetic examples.
- Failing traces can be added to the dataset from the UI (**Add to Dataset**).

---

## 2. Running evaluations with the SDK

### `evaluate` / `aevaluate` (langsmith/evaluate-llm-application)
- `from langsmith import evaluate, aevaluate` (or `Client().evaluate` / `Client().aevaluate`, same behaviour).
- `aevaluate` has the same interface and is recommended for larger jobs.
- Parameters:
  - `target`: a function `(inputs: dict) -> dict`, a Runnable (for example `example_to_state | graph`),
    an existing experiment name/ID (re-score only), or a tuple of two experiments (pairwise).
  - `data`: dataset name or ID, dataset object, or an iterator such as `client.list_examples(...)`.
  - `evaluators`: row-level evaluators. `summary_evaluators`: experiment-level evaluators.
  - `experiment_prefix`, `description`.
  - `metadata`: dict on the experiment. Reserved keys **`models`, `prompts`, `tools`** fill dedicated
    columns in the experiments table.
  - `max_concurrency`: threads in `evaluate`; in `aevaluate`, the number of examples processed at once.
    (langsmith/experiment-configuration)
  - `num_repetitions`: reruns the target **and** all evaluators (5 examples × 5 = 25 runs). (langsmith/repetition)
  - `blocking` (default True); `upload_results=False` records nothing (`>=0.2.0`). (langsmith/local)
  - `error_handling='ignore'` and `experiment=<existing name>` to retry failed examples only.
    (langsmith/evaluate-with-retry)
- Version gates: `inputs/outputs/reference_outputs` evaluator arguments need `langsmith>=0.3.13`;
  openevals in `evaluate` needs `>=0.3.11`; SDK progress tracking `>=0.8.16`.

### Target function
- Receives the example's `inputs`, returns a dict. A bare string return is seen as `outputs["output"]`;
  return a dict with explicit keys instead.
- Decorate with `@traceable` so inner steps show in the trace.

### Evaluating a LangGraph graph (langsmith/evaluate-graph)
- `target = example_to_state | app`, where `example_to_state` turns inputs into graph state.
- **If any node is async, use `aevaluate`.**
- Passing the graph directly makes `outputs` the final state, so evaluators can read `outputs["messages"]`.
- For data not kept in state, use a `run: Run` argument and walk `run.child_runs`.
  (The docs sample references an undefined `root_run`; use `run.child_runs`.)
- Single node: `example_to_state | app.nodes["agent"]`.
- Capturing a trajectory: stream with `subgraphs=True, stream_mode="debug"` and record node names
  and tool-call names. (langsmith/evaluate-complex-agent)
- The same page passes `config={"env": "test"}` so side effects are mocked during evals, and
  recommends separate datasets per evaluation type.

### Human-in-the-loop interrupts
- Not covered on the LangSmith eval pages. From the LangGraph HITL docs: `invoke` returns at the pause
  with `__interrupt__` in the result. The target should use a checkpointer and a unique `thread_id`
  per example and return the partial state plus the interrupt payload. The pause is a valid end state.

### Subsets, re-scoring, retries
- Subset: `data=client.list_examples(dataset_name=..., splits=[...] | metadata={...} | as_of=...)`.
- Re-run evaluators on an existing experiment without re-running the app (Python only):
  `evaluate("<experiment name or id>", evaluators=[...])`. (langsmith/evaluate-existing-experiment)
- Retry failures: run with `error_handling='ignore'`, find missing examples via
  `list_runs(project_name=experiment_name)`, rerun them with `experiment=experiment_name`.

---

## 3. Evaluator techniques

### Code evaluators (langsmith/code-evaluator-sdk)
- Arguments are matched **by name**, any subset of: `run: Run`, `example: Example`, `inputs`,
  `outputs`, `reference_outputs`. Import `from langsmith.schemas import Run, Example`.
- Return: `{"key": ..., "score": ...}` (numeric) or `{"key": ..., "value": ...}` (categorical), with
  optional `"comment"`; in Python also a bare `bool | int | float` (name = function name), `str`, or a
  `list[dict]` for several metrics.
- Can be sync or async.

### Skipping examples an evaluator doesn't apply to
- **[not in docs]** No documented "skip" (returning `None` isn't documented). Options:
  1. read `example.metadata["category"]` inside the evaluator and branch;
  2. run separate `evaluate()` calls per category subset with only the matching evaluators;
  3. use the pytest plugin, which the docs recommend when "each example requires different evaluation logic".
- Returning a fake score for "not applicable" distorts averages.

### Summary evaluators (langsmith/summary)
- `summary_evaluators=[...]`; arguments are lists (`inputs`, `outputs`, `reference_outputs`, `runs`, `examples`).
- Return `{"score": ..., "name": ...}` (note `name`, not `key`) or a number/bool.

### LLM-as-judge with the SDK (langsmith/llm-as-judge-sdk)
- Write an evaluator that calls an LLM with structured output and returns a score.
- Adding `reference_outputs` makes it reference-based.
- Documented pattern with any LangChain chat model:
  `grader = model.with_structured_output(Grade, method="json_schema", strict=True)`.
  (langsmith/evaluate-on-intermediate-steps). So a `ChatOpenRouter` model works as the judge.
- Groundedness example: judge the answer against the concatenated search-tool `ToolMessage` contents
  with a `Grade{score: bool}` schema. (langsmith/test-react-agent-pytest)
- Reference-free: safety, format, length, tone. Reference-based: correctness, exact match (offline only).
- Judges need careful review and prompt tuning; few-shot examples improve them. Built-in few-shot
  from corrections is a UI/workspace evaluator feature; in SDK judges, put examples in the prompt yourself.
- The SDK does not support decision-model (Jev) evaluators; those are UI-only.

### openevals (langsmith/openevals)
- `from openevals.llm import create_llm_as_judge`; `from openevals.prompts import CORRECTNESS_PROMPT, CONCISENESS_PROMPT`.
- `create_llm_as_judge(prompt=..., feedback_key="correctness", model="openai:o3-mini")`; can be passed
  straight into `evaluators=[...]` or wrapped in a function.
- `model` is a `"provider:model"` string; OpenRouter isn't a provider string there.
- **[not in docs]** Passing a model instance (`judge=`), `continuous`, `choices`, `use_reasoning`,
  `few_shot_examples`, `system`, `output_schema` and a RAG groundedness prompt are only in the README.

### Trajectory evaluation with agentevals (langsmith/trajectory-evals, oss/python/langchain/test/evals)
- `from agentevals.trajectory.match import create_trajectory_match_evaluator`.
- `trajectory_match_mode`:
  - `strict`: same tool calls in the same order;
  - `unordered`: same tool calls, any order;
  - `subset`: no tools beyond the reference;
  - `superset`: at least the reference tools.
- Call: `evaluator(outputs=messages, reference_outputs=reference_messages)` →
  `{"key": "trajectory_<mode>_match", "score": bool}`.
- The reference is a list of `HumanMessage` / `AIMessage(tool_calls=[...])` / `ToolMessage`.
- By default a tool call matches only with the same arguments; `tool_args_match_mode` /
  `tool_args_match_overrides` relax this.
- LLM trajectory judge: `create_trajectory_llm_as_judge(model=..., prompt=TRAJECTORY_ACCURACY_PROMPT)`;
  async variants exist.
- **[not in docs]** Graph trajectory helpers (`extract_langgraph_trajectory_from_thread`, ...) are README-only.
- Hand-rolled alternative: an ordered-subsequence score (fraction of expected steps seen in order).
  (langsmith/evaluate-complex-agent)

### Composite scores (langsmith/composite-evaluators-sdk)
- Read results with `client.get_experiment_results(name=...)`, then write a combined score with
  `client.create_feedback(run_id=..., key=..., score=..., session_id=...)`.

---

## 4. Analyzing experiments

### In code (langsmith/read-local-experiment-results)
- Iterate `ExperimentResults`: each row has `run`, `example` (with `.metadata`) and
  `evaluation_results["results"]` (each with `.key`, `.score`, `.comment`).
- `results.experiment_name`; `results.to_pandas()` (needs pandas).
- CI gate pattern: average a score and `sys.exit(1)` below a threshold.
- `client.read_project(project_name=experiment_name, include_stats=True)` gives latency, tokens, cost,
  `feedback_stats`, error rate. (langsmith/fetch-perf-metrics-experiment)

### In the UI (langsmith/analyze-an-experiment)
- Sort/filter by feedback; Compact, Full and Diff (output vs reference) views.
- Open the trace and the evaluator's own trace (to see why a judge scored something).
- **Group by metadata**: average feedback, latency, tokens and cost per category.
- **Set baseline** on one experiment to see deltas on the others.
- Filter/group by models, prompts, tools (needs the reserved metadata keys).
- CSV download (up to 5,000 rows). Repetitions show averages and standard deviation.

### Comparing (langsmith/compare-experiment-results)
- Select 2+ experiments → **Compare**. Regressions are red, improvements green.
- Per feedback key, set whether higher is better (set "lower is better" for violation counts).
- Diff view for exactly 2 experiments.

### Pairwise (langsmith/evaluate-pairwise)
- `evaluate(("exp-1", "exp-2"), evaluators=[...], randomize_order=True)`; returns per-run scores.

---

## 5. Pytest and CI

### LangSmith pytest plugin (langsmith/pytest), `langsmith>=0.3.4`
- Install `langsmith[pytest]`. Mark tests with `@pytest.mark.langsmith`.
- One dataset per test file, updated each run; each run is an experiment; pass/fail logged as `pass`.
- Log data with `from langsmith import testing as t`: `t.log_inputs`, `t.log_reference_outputs`,
  `t.log_outputs`, `t.log_feedback(key=..., score=...)`. Wrap judge calls in `with t.trace_feedback():`.
- Name suites and experiments with `LANGSMITH_TEST_SUITE` and `LANGSMITH_EXPERIMENT`.
  Experiment metadata via a `langsmith_experiment_metadata` fixture (`>=0.7.13`).
- HTTP caching with `LANGSMITH_TEST_CACHE=tests/cassettes` (per-host from `>=0.4.10`).
- `--langsmith-output` for rich tables; `LANGSMITH_TEST_TRACKING=false` for a dry run.
- Expect API: `expect(x).to_contain(...)`, `expect.edit_distance(...)`, `expect.embedding_distance(...)`.
- Recommended when each example needs different evaluation logic, or for binary CI asserts.

### CI (langsmith/cicd-pipeline-example)
- Offline evals run in GitHub Actions next to unit/integration tests; keys in repository secrets.
- Gate with a threshold on `ExperimentResults` or with pytest asserts; pin the dataset version with `as_of`.

### Rate limits and cost (langsmith/handle-model-rate-limiting)
- `InMemoryRateLimiter(requests_per_second=..., ...)` passed as `rate_limiter=` to the model.
- `.with_retry(stop_after_attempt=6)`, lower `max_concurrency`, retry only failed examples,
  `upload_results=False` for smoke tests.

---

## Recommendations for our setup

1. **Seed script** guarded by `has_dataset`. Each example's metadata holds `category`, `expect_review`
   and `expect_tools`; also put the category in a split. Tag the version (for example `baseline-v1`).
2. **Async target with `aevaluate`.** Fresh `thread_id` and a throwaway `company_id` per example. Return
   the draft (subject, body, `requires_review`, sources), the messages, the ordered tool calls (including
   subagent `task` calls) and the interrupt payload. Ending at the review pause is the expected result.
3. **Trajectory from subagents.** Search and draft tools run inside subagents, so record tool calls by
   streaming with `subgraphs=True`, or walk `run.child_runs` in the evaluator.
4. **Code evaluators** with explicit keys: `requires_review_correct`, `has_citations`, `word_count`,
   `no_send_without_approval`.
5. **Category-specific evaluators** read `example.metadata["category"]`; don't rely on undocumented
   skip behaviour, or run separate `aevaluate` calls per category.
6. **Trajectory checks:** an ordered check for "search before save draft"; agentevals `subset` mode for
   "only allowed tools", with relaxed argument matching for free-text queries.
7. **Groundedness judge** written by hand with our `ChatOpenRouter` model and
   `.with_structured_output(Grade)`, fed the retrieved chunks and the draft, `temperature=0`, with 2–3
   graded examples in the prompt.
8. **Injection examples** are judged by code checks only (review forced, nothing sent, no leaked prompt).
9. **Summary evaluators:** review pass rate per category and a zero-tolerance send-violation count.
10. **Runs:** `max_concurrency` 2–4, `num_repetitions=3` once stable, rate limiter and retries on the
    agent and judge models, `metadata={"models": [...], "prompts": [...], "tools": [...]}`, a baseline
    experiment, "lower is better" for violation metrics.
11. **CI later:** an `aevaluate` script with a threshold and `sys.exit(1)`, or the pytest plugin.
12. **Iterating:** `upload_results=False` for local smoke tests; `evaluate("<experiment>", evaluators=[new])`
    to add a metric without re-running the agent; `error_handling='ignore'` + `experiment=` to retry failures.
