# WP13 Codex Instructions — IBKR Adapter / Paper-Shadow Integration v1

Canonical mirror of the root-level WP13 implementation brief.

- Use IBKR TWS API / IB Gateway as the only broker boundary in WP13.
- Support `shadow_only` and `paper_submit`.
- `shadow_only` must never call `placeOrder`.
- `paper_submit` must require `CPDSHADOW_ENABLE_PAPER_SUBMIT=1` and a paper-account allowlist match.
- Persist normalized broker state:
  - `broker_positions_snapshot`
  - `broker_open_orders_snapshot`
  - account summary subset artifact
- Translate WP12 `order_intents` into deterministic IBKR futures `LMT/DAY` requests.
- Derive paper limit prices from local `contracts_daily` references only.
- Reconcile broker callbacks into normalized project statuses.
- Keep tests offline by default and provide a mock integration path.
- Do not implement live submission, Client Portal trading, combo/algo/bracket orders, or market-data-driven pricing.

See also:
- [broker_ibkr_v1.md](/C:/Users/shiro/OneDrive/ドキュメント/Spyder/MISOCP/cpd_lstm/docs/broker_ibkr_v1.md)
- [wp13_ibkr_paper_runbook_v1.md](/C:/Users/shiro/OneDrive/ドキュメント/Spyder/MISOCP/cpd_lstm/docs/runbooks/wp13_ibkr_paper_runbook_v1.md)
- [0015-ibkr-paper-shadow-integration-v1.md](/C:/Users/shiro/OneDrive/ドキュメント/Spyder/MISOCP/cpd_lstm/docs/decisions/0015-ibkr-paper-shadow-integration-v1.md)
