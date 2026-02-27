# AgentFlow Examples

Each subdirectory contains a complete working example with AFL source and a Python agent implementation.

| Example | Description |
|---------|-------------|
| [hello-agent](hello-agent/) | Minimal end-to-end example demonstrating the AgentFlow execution model |
| [volcano-query](volcano-query/) | Cross-namespace composition using existing OSM event facets |
| [osm-geocoder](osm-geocoder/) | Geocoding agent that resolves addresses to coordinates using the OpenStreetMap Nominatim API |
| [genomics](genomics/) | Bioinformatics cohort analysis with foreach fan-out and linear fan-in workflows |
| [jenkins](jenkins/) | CI/CD pipelines showcasing mixin composition (Retry, Timeout, Credentials, etc.) |
| [aws-lambda](aws-lambda/) | AWS Lambda + Step Functions with real boto3 calls against LocalStack |
| [census-us](census-us/) | US Census ACS + TIGER county demographics with dashboard map visualization |
| [continental-lz](continental-lz/) | Continental-scale road infrastructure and GTFS transit analysis with Docker |
| [site-selection](site-selection/) | Restaurant site selection with OSM + Census scoring pipeline |
| [monte-carlo-risk](monte-carlo-risk/) | Monte Carlo portfolio risk analysis (GBM simulation, VaR/CVaR, Greeks, stress testing) |
| [ml-hyperparam-sweep](ml-hyperparam-sweep/) | ML hyperparameter sweep (statement-level andThen, prompt blocks, map literals, andThen foreach) |
| [research-agent](research-agent/) | AI research agent — first LLM integration showcase (prompt blocks, ClaudeAgentRunner) |
| [multi-agent-debate](multi-agent-debate/) | Multi-agent debate — first multi-agent interaction example (3 debate agents, scoring/voting) |

## User Documentation

For a guided introduction to the examples, including a learning path and pattern reference, see the **[Examples Guide](doc/GUIDE.md)**.

Each example also has a **USER_GUIDE.md** with step-by-step walkthroughs, key concepts, and adaptation tips:

| Example | User Guide |
|---------|-----------|
| hello-agent | [USER_GUIDE.md](hello-agent/USER_GUIDE.md) |
| volcano-query | [USER_GUIDE.md](volcano-query/USER_GUIDE.md) |
| genomics | [USER_GUIDE.md](genomics/USER_GUIDE.md) |
| jenkins | [USER_GUIDE.md](jenkins/USER_GUIDE.md) |
| aws-lambda | [USER_GUIDE.md](aws-lambda/USER_GUIDE.md) |
| osm-geocoder | [USER_GUIDE.md](osm-geocoder/USER_GUIDE.md) |
| census-us | *(no user guide yet)* |
| continental-lz | [USER_GUIDE.md](continental-lz/USER_GUIDE.md) |
| site-selection | *(no user guide yet)* |
| monte-carlo-risk | *(no user guide yet)* |
| ml-hyperparam-sweep | *(no user guide yet)* |
| research-agent | *(no user guide yet)* |
| multi-agent-debate | [USER_GUIDE.md](multi-agent-debate/USER_GUIDE.md) |

## Running an Example

Each example has its own `README.md` with setup and run instructions. The general pattern is:

```bash
# From the repo root
source .venv/bin/activate

# Install example-specific dependencies
pip install -r examples/<name>/requirements.txt

# Compile the AFL source (syntax check)
python -m afl.cli examples/<name>/afl/<file>.afl --check

# Run the agent (see each example's README for details)
PYTHONPATH=. python examples/<name>/agent.py
```

## Writing a New Example

1. Create a directory under `examples/` with a descriptive name
2. Include at minimum:
   - `README.md` — what it does, prerequisites, how to run, expected output
   - `USER_GUIDE.md` — step-by-step walkthrough, key concepts, adaptation tips
   - `afl/*.afl` — AFL workflow source files
   - `agent.py` — Python agent using `AgentPoller`
   - `requirements.txt` — any extra pip dependencies
3. Add an entry to this README's tables
4. Add an entry to the [Examples Guide](doc/GUIDE.md)
