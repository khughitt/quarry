---
id: quarry-dd5180
title: Detect drift between a domain's beliefs_contract_version and the beliefs contract
status: idea
priority: 2
created: 2026-10-11T09:06:01Z
updated: 2026-10-11T09:06:01Z
depends: []
tags: []
agent: claude-code/claude-opus-5-5
---

biology.toml declares beliefs_contract = biology, version 1, but quarry never reads beliefs (spec §11), so a beliefs contract bump goes unnoticed until a facet or namespace mismatch shows up downstream. Options: an opt-in quarry check --beliefs <checkout> that compares against the contract's DOMAIN.yaml, or a check on the beliefs side that lists the quarry domains naming its contracts. Decide which side owns it.
