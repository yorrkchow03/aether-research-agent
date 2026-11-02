# Ark Agent Plan and optional Jev

Copy `.env.example` to private `.env` in the repository root. Set `ARK_API_KEY` to your authorized Agent Plan credential, retain `ARK_MODEL_ID=doubao-seed-2.1-pro`, and keep `ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/plan/v3`. Do not substitute the ordinary extra-billing `/api/v3` endpoint. No console subscription/overage setting is changed by this application.

Model tools use the compatible Chat Completions interface. `search_web` uses the same plan host's Responses API with `web_search` and Doubao sources. Returned provider annotations, publication/retrieval times and response hashes are saved; generated answer URLs are not automatically evidence. `fetch_webpage` separately captures accessible original text. A search time hint is not a strict publication-date filter.

Start with `python scripts/start_agent.py`, then open http://127.0.0.1:8765/. The configured model runs in local Deep Agents / LangGraph. Historical sessions do not use current web knowledge. Without a configured key, API research is unavailable; the old conversation bridge is retained for offline harness tests/debugging, not as a production LLM substitute.

Optional TypeSafe Jev uses `TYPESAFE_API_KEY`, `JEV_MODEL=jev-latest` and `JEV_MIN_CONFIDENCE=0.8`. Ark credentials cannot authenticate it. There is no implicit subscription or paid fallback. Restart the server after private configuration changes, when no research is active. The UI displays credential status, never keys.

Official references: [Agent Plan search](https://docs.volcengine.com/docs/ark/agent-plan-personal-search?lang=zh), [TypeSafe API](https://docs.typesafe.ai/api).
