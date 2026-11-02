# Third-party components and data

Deep Agents is installed as the pinned upstream Python package; its repository is [langchain-ai/deepagents](https://github.com/langchain-ai/deepagents). LangGraph/LangChain, scikit-learn, NumPy, FastAPI, HTTPX and other distributions retain their own licenses. Package metadata records dependency versions. The optional historical upstream checkout is kept under ignored `local/vendor/` and is not redistributed as project source.

Market data and news belong to their respective providers/publishers, including Coinbase, DefiLlama, OKX, CoinMetrics, Ethereum Foundation and the other connectors in the source catalog. Local snapshots are research receipts, not a license to republish a feed or publisher article. The source export excludes captured data, articles, models trained locally, reports and private ledger records.

Jev is an optional external TypeSafe service. Its name and model/API documentation are used to describe integration, not affiliation or endorsement. Model/search usage remains subject to the configured providers' terms.

No open-source license has been selected for this project's original code yet. Preparing a GitHub directory does not grant a new license; choose one deliberately before public release.


## Optional local memory embeddings

Model2Vec (MIT): https://github.com/MinishLab/model2vec. Optional package pins are
in requirements.memory.lock.txt. Public POTION pretrained weights (MIT) are
prepared separately from https://huggingface.co/minishlab/potion-multilingual-128M;
weights, caches and private indexes are not included in source exports. Loading
requires a completed local hash manifest; no remote Python code is executed.
