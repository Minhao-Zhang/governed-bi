# External design sources

The design docs reference these sources. They live outside this repo (design
vault / upstream projects); listed here so the in-text references resolve.

| Source | What it grounds |
|---|---|
| **[BIRD-Obfuscation](https://github.com/Minhao-Zhang/BIRD-Obfuscation)** | The near-term eval dataset: 4 DB versions, ~10k verified Q&A, decoy manifest, rename map. A *separate upstream repo* that produces validated data and manifests. It explicitly scopes out "the downstream agent that exercises the traps": that downstream agent is *this* system. |
| **BIRD Bench Obfuscation Methodology** | How the obfuscation dimensions (decoy / rename / FK-withheld / rewrite) are constructed. |
| **Data Agent Memory Design Overview** (2026-07-05) | Memory policy, reusable numbers (TTLs, thresholds), the "curation beats accumulation" law, and the SQL semantic-cache design. |
| **How Anthropic enables self-service data analytics with Claude** | Corpus rot (~95%→65%/month untended); skills as the highest-value lever (<21% → 95%+); the raw-corpus-grep null result. |
| **《从数据到智能》** (*From Data to Intelligence*) | Ch.3's 9-asset-type semantic layer, adapted (with the authoring model inverted) into the corpus contract (D9). |
| **Private enterprise fork** | A private parallel fork (phase 2) that reuses this engine at enterprise scale; faces the same no-owner / no-manpower situation. Out of scope for this repo. |

## Repo boundary

BIRD-Obfuscation (upstream) produces the data + manifests. **This repo is the
downstream agent** that consumes them: it builds the semantic layer (curator),
answers questions (server), and is graded on execution accuracy.
