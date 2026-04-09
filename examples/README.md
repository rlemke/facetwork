# Facetwork Examples

Each subdirectory contains a complete working example with FFL source and a Python agent implementation.

| Example | Description |
|---------|-------------|
| [hello-agent](hello-agent/) | Minimal end-to-end example demonstrating the Facetwork execution model |
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
| [multi-round-debate](multi-round-debate/) | Multi-round debate — composed facets as primary pattern (DebateRound encapsulates 12 steps, cross-round state, convergence) |
| [tool-use-agent](tool-use-agent/) | Tool-use agent — tool-as-event-facet pattern (6 tools as event facets, planning facet, `++` and `%`/`/` arithmetic) |
| [data-quality-pipeline](data-quality-pipeline/) | Data quality pipeline — schema instantiation as steps, array type annotations `[Type]`, parenthesized expression grouping `(a+b)*c` |
| [sensor-monitoring](sensor-monitoring/) | Sensor monitoring — unary negation, null literals, computed map indexing, mixin alias, RegistryRunner-first |
| [site-selection-debate](site-selection-debate/) | Site-selection debate — spatial + research + debate combined (12 prompt-block event facets, composed facet, cross-round state) |
| [event-driven-etl](event-driven-etl/) | Event-driven ETL — extract/transform/load pipeline (3 schemas, 6 event facets, 2 workflows, foreach, schema instantiation) |
| [devops-deploy](devops-deploy/) | DevOps deployment pipeline — first `andThen when` showcase (nested when, foreach, prompt/script blocks, mixins+implicits) |
| [hiv-drug-resistance](hiv-drug-resistance/) | HIV drug resistance genotyping — bioinformatics pipeline (`andThen when` QC, `catch` error recovery, `foreach` batch, prompt+script blocks) |
| [noaa-weather](noaa-weather/) | NOAA GHCN-Daily climate analysis — catalog-first real-data pipeline (AWS S3, linear regression trends, OSM geocoding, `catch`/`foreach`, 25+ workflows incl. international and cache warmup) |

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
| census-us | [USER_GUIDE.md](census-us/USER_GUIDE.md) |
| continental-lz | [USER_GUIDE.md](continental-lz/USER_GUIDE.md) |
| site-selection | [USER_GUIDE.md](site-selection/USER_GUIDE.md) |
| monte-carlo-risk | [USER_GUIDE.md](monte-carlo-risk/USER_GUIDE.md) |
| ml-hyperparam-sweep | [USER_GUIDE.md](ml-hyperparam-sweep/USER_GUIDE.md) |
| research-agent | [USER_GUIDE.md](research-agent/USER_GUIDE.md) |
| multi-agent-debate | [USER_GUIDE.md](multi-agent-debate/USER_GUIDE.md) |
| multi-round-debate | [USER_GUIDE.md](multi-round-debate/USER_GUIDE.md) |
| tool-use-agent | [USER_GUIDE.md](tool-use-agent/USER_GUIDE.md) |
| data-quality-pipeline | [USER_GUIDE.md](data-quality-pipeline/USER_GUIDE.md) |
| sensor-monitoring | [USER_GUIDE.md](sensor-monitoring/USER_GUIDE.md) |
| site-selection-debate | [USER_GUIDE.md](site-selection-debate/USER_GUIDE.md) |
| event-driven-etl | *(coming soon)* |
| devops-deploy | *(coming soon)* |
| hiv-drug-resistance | *(coming soon)* |
| noaa-weather | [USER_GUIDE.md](noaa-weather/USER_GUIDE.md) |

## Running an Example

Each example has its own `README.md` with setup and run instructions. The general pattern is:

```bash
# From the repo root
source .venv/bin/activate

# Install example-specific dependencies
pip install -r examples/<name>/requirements.txt

# Compile the FFL source (syntax check)
python -m afl.cli examples/<name>/ffl/<file>.ffl --check

# Run the agent (see each example's README for details)
PYTHONPATH=. python examples/<name>/agent.py
```

## Writing a New Example

1. Create a directory under `examples/` with a descriptive name
2. Include at minimum:
   - `README.md` — what it does, prerequisites, how to run, expected output
   - `USER_GUIDE.md` — step-by-step walkthrough, key concepts, adaptation tips
   - `afl/*.ffl` — FFL workflow source files
   - `agent.py` — Python agent using `AgentPoller`
   - `requirements.txt` — any extra pip dependencies
3. Add an entry to this README's tables
4. Add an entry to the [Examples Guide](doc/GUIDE.md)
