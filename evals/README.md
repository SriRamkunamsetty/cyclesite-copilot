# Evaluating real Gemini behaviour

The unit tests prove the code. These cases check how the real model follows the rules in
`cycle_agent/prompts.py`: asking before using expired data, quoting the cautious case, refusing to
call demand revenue, refusing destructive requests.

```bash
export CYCLESITE_BACKEND=synthetic GOOGLE_CLOUD_PROJECT=your-project
adk eval cycle_agent evals/cyclesite.evalset.json --config_file_path evals/test_config.json
```

Needs Gemini access, so it is **not** part of CI. Re-run it whenever you change the prompt, the
tools or the model (`CYCLESITE_MODEL`). Tool order is checked with `IN_ORDER` matching (extra
calls are allowed, missing or reordered ones are not); the answer wording is graded against the
rubrics in each case. The file format was checked against the installed ADK data models by
`tests/test_evalset_format.py`, but it has not been run against a live model here.
