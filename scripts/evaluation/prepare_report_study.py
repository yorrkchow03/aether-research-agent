#!/usr/bin/env python3
"""Freeze the predeclared 2-pilot/10-confirmation report study; no network calls."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from research_agent.report_study_dataset import freeze_dataset, saved_source, text_sha
from research_data.library import ResearchLibrary


CUTOFF = "2026-10-28T23:59:59Z"
LIBRARY = ROOT / "data/library-halfyear-20260426-20261026"
OUTPUT = ROOT / "local/report-flow-study/v1/dataset"


def construct(library):
    sources, cases = {}, []

    def add(rid, selection=None):
        if rid in sources:
            return rid
        row = library.evidence(rid)
        if row.get("library_exclusion_reasons"):
            raise ValueError("Excluded library source selected: " + rid)
        text = row["text"]
        if selection == "bounty":
            ranges = [(0, 4700), (text.index("Review Period\n"), len(text))]
        elif selection == "quarter":
            ranges = [(0, text.index("Explore the full list")), (text.index("Total awarded in"), len(text))]
        elif selection == "sec_newsletter":
            ranges = [(0, text.index("🌎 Macro Crypto and Markets"))]
        else:
            ranges = None
        sources[rid] = saved_source(row, ranges)
        return rid

    def point(pid, statement, *evidence):
        return {"id": pid, "statement": statement,
                "evidence": [{"source_id": rid, "quote": quote, "sha256": text_sha(quote)}
                             for rid, quote in evidence]}

    def case(cid, split, family, question, ids, facts, counter, forbidden, gaps, terms):
        cases.append({"case_id": cid, "split": split, "event_family": family,
                      "question": question, "cutoff": CUTOFF, "language": "English", "source_ids": ids,
                      "research_context": {"source_pack_id": cid + "-sources", "query_terms": terms,
                          "availability_mode": "observed", "scope": "Saved retrospective source pack only; publication time is not historical system availability.",
                          "output_contract": "Write a short English research note with 2-3 source observations, a qualified inference or questions to watch, explicit source-ID citations, and material unknowns. No price prediction or mandatory investment recommendation."},
                      "expected": {"required_facts": facts, "counter_evidence": counter,
                                   "forbidden_inferences": forbidden, "allowed_gaps": gaps},
                      "rubric_status": "AI authored before provider runs; not independently verified human gold"})

    # Development pilots: never reused as confirmation families.
    a = add("page-343bb462694405b5b6722f7c")
    b = add("page-64faf9bdf1a781e9c8aabf69")
    case("report-dev-01", "development", "ef_organization_and_board_202606_07",
         "What organizational and board changes did the Ethereum Foundation announce in June and July 2026, and what do these announcements establish about its role?", [a, b], [
         point("staffing", "The June announcement says the reorganization left 54 fewer colleagues, roughly 20% of EF.",
               (a, "54 fewer colleagues, roughly 20% of the EF")),
         point("board", "The July announcement adds pcaversaccio to the board for an initial one-year voluntary term.",
               (b, "pcaversaccio (pc) has joined the Ethereum Foundation Board."),
               (b, "pc joins the Board for an initial one-year term and on a voluntary basis")),
         point("clusters", "The June post describes five domain clusters plus operations and management/support clusters.",
               (a, "five clusters with different domains of work – protocol layer, access layer, user layer, community layer, and institutional layer – as well as a cluster focused on operations and one comprising management and teams directly supporting management work."))], [
         point("mandate", "The stated board role concerns EF vision and management alignment, not unilateral control of Ethereum consensus.",
               (b, "the EF board sets the vision, and oversees that the high-level strategies and decisions by management are aligned with the Foundation’s values."))],
         ["Staff reduction proves Ethereum network participation fell 20%.", "A board appointment guarantees ETH returns or changes on-chain consensus rules."],
         ["These excerpts do not quantify effects on protocol delivery, network decentralization or ETH prices."],
         ["EF", "structure", "board", "pcaversaccio"])

    a = add("page-9a7817eedf2d5eadfea02a36", "bounty")
    b = add("page-b21e78195905430559cec3e5")
    c = add("page-f5316ec065c016c1029abe17")
    case("report-dev-02", "development", "aave_bounty_restructure_202604_05",
         "Summarize the proposed Aave bug-bounty restructuring and the material funding or governance questions raised by reviewers. What is still conditional?", [a, b, c], [
         point("split", "The proposal splits bounties by subsystem and uses Immunefi, Sherlock and Cantina rather than immediate consolidation.",
               (a, "Core Aave V3: Immunefi\nCore Aave V2: Immunefi\nGHO: Immunefi\nNon-liquidity protocol infrastructure: Immunefi\nAave V4: Sherlock\nAave V3 on Aptos: Cantina\nAave App Stack: Sherlock")),
         point("review", "The initial multiple-platform observation period is proposed as 6-12 months, supported as prudent by LlamaRisk.",
               (a, "This structure should remain in place for an initial observation period of 6 to 12 months."),
               (c, "The proposed 6-12 month assessment window for the different platforms is prudent.")),
         point("funding", "A community commenter asks for the Aptos funding transfer to be evaluated separately and an annual ceiling to be stated.",
               (b, "The community should evaluate the Aptos funding transfer on its own merits"),
               (b, "The DAO should publish the expected annual budget ceiling across all seven programs combined."))], [
         point("approval", "Implementation is conditional on consensus and Snapshot; supportive comments are not a passed vote or spending proof.",
               (a, "If consensus is reached on this ARFC, move the proposal to Snapshot.\nIf Snapshot passes, implement the revised multi-program structure")),
         point("estimate", "The commenter's back-of-envelope annual exposure is an estimate, not an approved budget or realized payout.",
               (b, "A back-of-envelope calculation:"))],
         ["The DAO has already voted to transfer Aptos funding or disbursed the estimated annual budget.", "A participant's $8-10M estimate is an approved or observed annual payout."],
         ["No voting outcome, implementation transaction or audited annual payout series is included."],
         ["Aave", "bug bounty", "restructure", "funding"])

    a = add("page-770fdd954bbd46ded1e7a2cf")
    b = add("page-4b1d80899a105ce751d9be32")
    c = add("page-2d66a38fdc734a4bc7413c34")
    case("report-confirm-01", "confirmation", "native_transaction_assertions_202606_10",
         "How are native transaction assertions intended to improve transaction safety, and what unresolved design or trust boundaries should a wallet user understand?", [a, b, c], [
         point("mechanism", "Native assertions would check starting/final state against signed rules after transaction actions and revert actions on failure.",
               (a, "An assertion uses read-only logic to compare starting and final values against a rule. If the rule fails, the actions revert.")),
         point("trusted_rule", "The rule must originate from independent intent or policy; a compromised frontend can supply a permissive assertion.",
               (a, "A compromised frontend can write an assertion that permits the attack.")),
         point("design", "A forum update proposes per-address views and account flags while requiring query costs not to scale with attacker-controlled padding.",
               (c, "assertion gas cost must not scale with attacker-controlled padding."))], [
         point("proposal", "EIP-7906 is presented as one possible approach, not evidence of mainnet activation.",
               (a, "with EIP-7906 (Transaction Assertions via State Diff Opcode) as one possible approach.")),
         point("feedback", "The supportive wallet comment explicitly does not evaluate opcode or gas implementation details.",
               (b, "I didn’t go deep on the opcode or gas details so I can’t speak to the implementation side"))],
         ["Ethereum mainnet already enforces EIP-7906 for all users.", "Any signed assertion guarantees safe economic outcomes regardless of its author.", "The forum discussion proves universal implementation performance."],
         ["No activation transaction, consensus adoption decision, implementation benchmark or wallet deployment measurement is included."],
         ["EIP-7906", "transaction assertions", "TXDIFF", "state"])

    a = add("page-91ab60605295babe0177769f")
    b = add("page-f2693c004e59b192adec7086")
    c = add("page-7e526ec0ca61e79b25b38da5")
    case("report-confirm-02", "confirmation", "webcat_clear_signing_integrity_202605_08",
         "What protection is the WEBCAT grant intended to add alongside Clear Signing, and which adoption or mutable-state limitations remain?", [a, b, c], [
         point("integrity", "WEBCAT checks enrolled-site resources against a signed manifest; the current alpha Firefox extension blocks a failed verification.",
               (a, "lets a browser verify that the resources served by an enrolled site match a signed manifest. If verification fails, the current alpha Firefox extension prevents the page from loading and displays a warning.")),
         point("funded_work", "The grant funds an integrable verification library and research/audit/standard work, not proof of rollout across all wallets.",
               (a, "The grant funds the development of a WEBCAT verification library that wallets can integrate.")),
         point("complement", "Clear Signing helps explain approvals; front-end code-integrity verification addresses a different boundary.",
               (a, "Clear Signing helps users understand what they’re approving, while WEBCAT integration would help wallets verify that an enrolled app’s front end matches its signed manifest."))], [
         point("adoption", "Protection needs wallet integration and app enrollment/signed releases, rather than automatically covering arbitrary sites.",
               (a, "Wallet extensions must integrate the library, and app teams must enroll their domains and serve a signed manifest with each release.")),
         point("mutability", "A forum participant proposes an optional intentMutability field and warns about state-dependent intent; that comment is not adoption of a finalized standard.",
               (c, "A new optional top-level intentMutability field"),
               (c, "Absence of intentMutability is treated as “unknown,” not “stable”"))],
         ["The grant allocation is one trillion dollars.", "All wallets and web apps already use WEBCAT.", "Clear Signing or signed manifests guarantee all transaction outcomes.", "The proposed intentMutability field is already mandatory."],
         ["The monetary grant amount, integration penetration and audit results are not supplied.", "The saved Clear Signing announcement is a short excerpt, not the full normative specification."],
         ["WEBCAT", "Clear Signing", "signed manifest", "intentMutability"])

    a = add("page-94006b268a497cea511c3579")
    b = add("page-f3070ddd1aa05010289bc3f1")
    case("report-confirm-03", "confirmation", "plataberget_gas_repricing_testing_202608",
         "What do the Platåberget announcement and repricing analysis establish about current testing, affected applications and a developer's next checks? Separate test results from mainnet readiness.", [a, b], [
         point("testnet", "Platåberget is a short-term public early testing ground intended to run for a few months, preceding longer-lived testnets and mainnet.",
               (a, "Platåberget is a short-term testnet aimed for testing changes by the community.")),
         point("impact", "Historical replay indicates a small affected contract set, with most flagged issues fixed by higher limits and a large majority unaffected.",
               (b, "Most flagged issues are fixed with a gas-limit increase, and the large majority of smart contracts are unaffected.")),
         point("check", "Maintainers should check hardcoded gas assumptions and test fixes; even unlisted contracts should review hardcoded gas or remaining-gas branches.",
               (b, "Even if your contract is not listed, review any code that hardcodes gas values or branches on remaining gas."))], [
         point("stage", "The repricing post says devnets now, then public testnets ahead of mainnet; this is not mainnet activation evidence.",
               (b, "The repricing schedule is live on devnets now and will roll out to public testnets ahead of mainnet activation.")),
         point("transfer", "A plain ETH transfer is not always flat 21,000 gas under the test design, especially to a new account.",
               (a, "a plain ETH transfer is no longer always flat 21,000 gas"))],
         ["Glamsterdam has already activated on mainnet.", "All smart contracts will break.", "The throughput target guarantees realized capacity or an ETH return.", "Every transfer costs exactly 21,000 gas in this test design."],
         ["No later activation record, complete affected-contract inventory or application-specific test result is included."],
         ["Platåberget", "Glamsterdam", "repricing", "gas"])

    a = add("page-24af6c8bc2abeb5d0dbc3d1c", "quarter")
    b = add("page-c0db8986c9fd66fa7d7e0551", "quarter")
    case("report-confirm-04", "confirmation", "ef_quarterly_awards_2026q1_q2",
         "Compare the EF's reported Q1 and Q2 2026 total awards. Explain the units, quarter labels, publication timing, and what this comparison cannot tell an ETH investor.", [a, b], [
         point("q1", "Q1 reports total awards of 9,856,014.14 USD.",
               (a, "Total awarded in Q1 2026: 9,856,014.14 USD")),
         point("q2", "Q2 reports total awards of 5,502,930.20 USD, lower than Q1 in the two published summaries.",
               (b, "Total awarded in Q2 2026: 5,502,930.20 USD"))], [
         point("scope", "These are project/ecosystem awards for stated quarters, not daily ETH flow or the entire EF balance sheet.",
               (a, "See the list below of the projects and ecosystem efforts we supported this quarter"),
               (b, "See the list below of the projects and ecosystem efforts supported this quarter"))],
         ["USD amounts are ETH quantities.", "Publication dates are quarter-end cash settlement dates.", "Award totals measure net ETF flow or all EF expenses.", "Lower reported awards prove declining Ethereum adoption or future price weakness."],
         ["The snippets do not supply cash-disbursement dates, full treasury accounts, or a causal price test.", "All original project lists remain readable, but the common starting pack includes only introductions and exact total lines."],
         ["Allocation Update", "Q1 2026", "Q2 2026", "Total awarded"])

    a = add("page-b272c67de01dec1f91e5fc82")
    b = add("page-2172fbc08fb4e8294efc4cf7")
    c = add("page-1d216003d433992f1172da6c", "sec_newsletter")
    case("report-confirm-05", "confirmation", "sec_tokenized_stocks_innovation_exemption_202605_09",
         "Do the May delay report and September innovation-exemption coverage describe a contradiction or a change over time? Summarize what can be attributed to these captures and which legal details still need original-text verification.", [a, b, c], [
         point("may", "The May news report attributes a postponement to Bloomberg and describes ongoing feedback, not a permanently abandoned initiative.",
               (a, "The SEC postponed its anticipated exemption for tokenized assets following concerns about third-party issuers, per Bloomberg.")),
         point("september", "The September opinion/newsletter reports approval for qualifying tokenized-equity venues, with scope limitations, after the earlier delay.",
               (c, "the SEC approved an Innovation Exemption letting qualifying venues trade tokenized US stocks on public blockchains"),
               (c, "The exemption covers only real tokenized stocks carrying full rights including dividends and voting")),
         point("genre", "The September detailed interpretation is an attributed personal analysis rather than the operative official text.",
               (c, "The analysis and opinions expressed are his own and do not necessarily reflect those of Decrypt."))], [
         point("missing_primary", "The saved official page body contains navigation, so the title supports discovery but does not verify the secondary article's exact operative conditions.",
               (b, "More in this Section\nNewsroom\nPress Releases")),
         point("sequence", "The May account says the timeline shifted, consistent with a later development rather than two same-time mutually exclusive facts.",
               (a, "But the timeline has shifted as the SEC absorbs feedback"))],
         ["The saved SEC body independently verifies every legal condition, five-year duration or immediate-effect claim in the newsletter.", "The May and September accounts necessarily contradict each other.", "A dynamic embedded market-price box is a verified September observation.", "All tokenized assets are approved or ETH must rise because of the action."],
         ["The primary operative release/order text was not captured; exact legal applicability must remain provisional.", "No causal market-impact study or authenticated time-specific price feed is in this pack."],
         ["SEC", "innovation exemption", "tokenized stocks", "May", "September"])

    a = add("page-c57beff1f7ebb96ce435c1d1")
    b = add("page-4d905b8b94f7a2d7ba9e4872")
    case("report-confirm-06", "confirmation", "lido_post_quantum_readiness_preproposal_202610",
         "What parts of the Lido post-quantum-readiness idea have contributors begun exploring, and which commitments remain premature? Reconcile the pre-proposal's timetable with the reply rather than treating either as a completed migration.", [a, b], [
         point("proposal", "The pre-proposal requests an exposure inventory, migration playbook and ecosystem coordination, with a suggested four-month deliverable sequence.",
               (a, "• Month 1–2: exposure inventory report (public);\n• Month 2–4: draft playbook + testnet rehearsal results;\n• Month 4: final report to the DAO with recommendations for any required budget.")),
         point("early_work", "The reply says early internal inventory scoping and informal ecosystem coordination are underway, explicitly exploratory rather than a finished report.",
               (b, "we’ve actually already started some early internal scoping along these lines. Still early"),
               (b, "(1) and (3) are already moving, in an exploratory way")),
         point("budget", "The pre-proposal requests no budget at this stage and would return to the DAO for any budget needs.",
               (a, "None requested at this stage. Contributions on a volunteer/grant basis; any budget needs would return to the DAO with the final report."))], [
         point("playbook", "The contributor reply says it is too early to commit to a concrete migration playbook while canonical protocol primitives and sequencing remain unsettled.",
               (b, "Migration playbook: this is where we think it’s too early to commit to anything concrete, deliberately."),
               (b, "Writing a Lido-specific playbook ahead of that risks optimizing for a process that gets significantly revised once the canonical path firms up."))],
         ["The working group, four-month timetable or budget has been approved by a DAO vote.", "A final migration playbook or completed quantum-resistant deployment exists.", "The commenter's exploratory work proves that current validator keys are already quantum resistant.", "The pre-proposal's roadmap and exposure estimates are independently verified facts."],
         ["No formal working-group charter, accepted timetable, inventory report or consensus migration specification is supplied.", "The EF/roadmap and exposure claims in the pre-proposal are attributed background, not independently checked primary measurements in this pack."],
         ["Lido", "post-quantum", "inventory", "playbook", "pre-proposal"])

    a = add("page-7ba606e5f3e74eab1d256169")
    b = add("page-f206344bd817ff83da168665")
    case("report-confirm-07", "confirmation", "aave_v3_v4_cap_irm_recommendations_20261023",
         "Compare the October 23 V3 Arbitrum USDC and V4 Plus Hub rate recommendations and explain the matured-PT cap restrictions. Preserve instance, rate/unit and proposed-versus-executed distinctions.", [a, b], [
         point("v3", "V3 Arbitrum USDC Slope1 is recommended from 4.00% to 4.10%, a 10-basis-point increase, with optimal-point borrow APR distinct from observed borrow APR.",
               (a, "The recommendation raises Slope1 by 10 BPS, from 4.00% to 4.10%.")),
         point("v4", "V4 Ethereum Plus Hub USDC/USDT Slope1 is recommended from 4.60% to 4.70%; projected supply-APY changes are a different rate measure.",
               (b, "We recommend increasing slope1 for USDC and USDT on the Plus Hub from 4.60% to 4.70%"),
               (b, "raises the Plus Hub supply APY by 0.07 percentage points for USDC and 0.08 percentage points for USDT")),
         point("matured", "The V3 matured-PT cap of 1 closes new deposits while preserving withdrawal/redemption; V4 zero draw caps restrict new borrowing without canceling existing debt.",
               (a, "The proposed supply cap of 1 closes new deposits while existing holders retain the ability to withdraw or redeem."),
               (b, "Existing debt is unaffected and can be repaid."))], [
         point("future", "Both posts describe future Risk Steward implementation; a separate execution record is needed to establish the new live parameters.",
               (a, "We will move forward and implement these updates via the Risk Steward process."),
               (b, "We will move forward and implement these updates via the Risk Steward process."))],
         ["A 10-basis-point increase is a 10-percentage-point rise.", "V3 Arbitrum and V4 Ethereum Plus Hub rates can be merged into one live USDC yield.", "Supply APY equals borrow APR or slope1.", "A zero draw cap deletes existing debts or freezes all withdrawals.", "The recommendation is an independently verified execution record."],
         ["No dashboard snapshot or on-chain execution proof is included; rates depend on utilization and market scope."],
         ["Aave V3", "Aave V4", "Arbitrum", "Plus Hub", "Slope1", "cap"])

    a = add("page-3c5c5d21e48b32da8033bc84")
    b = add("page-0fd62bb9ab6cb07e7eea8d92")
    c = add("page-a236103ae341a1bf7d64826b")
    d = add("page-71aee14ddccea324deda69c6")
    case("report-confirm-08", "confirmation", "metamask_staking_target_limit_202609_10",
         "Trace the MetaMask Staking target-limit change from precautionary notice to the latest captured execution update. What changed for node-operator deposits, and what does this not establish about MetaMask wallets?", [a, b, c, d], [
         point("notice", "The initial user update describes an infrastructure incident and says no immediate threat to MetaMask wallets was identified.",
               (a, "We have identified no immediate threat to MetaMask wallets.")),
         point("effect", "The proposed soft target limit of zero stops new deposits being allocated to the named MetaMask Staking operators in CMv1 and CMv2.",
               (b, "These parameters mean that no new deposits will be allocated to MetaMask Staking (ex Consensys Staking) Node Operators in both CMv1 and CMv2.")),
         point("execution", "The October 19 update distinguishes enacted vote #206 from pending Dual Governance execution; the October 23 update reports Proposal #15 enacted and limits set.",
               (c, "The target validators limit change will take effect once Dual Governance Proposal #15 is executed"),
               (d, "The Dual Governance Proposal #15 has been enacted"))], [
         point("soft", "The latest update still specifies soft mode in both curated modules, not elimination of all MetaMask wallet activity.",
               (d, "target validators limit has been set to 0 in CMv1 and CMv2 (soft mode)."))],
         ["The October 19 vote enactment alone proves the limits had already taken effect then.", "All MetaMask wallets lost funds or all withdrawals were blocked.", "A node-operator new-deposit restriction applies to every MetaMask product."],
         ["Execution is reported by the saved forum update; no independent RPC transaction receipt or wallet-forensic analysis is included."],
         ["MetaMask Staking", "target limit", "Dual Governance", "CMv1", "CMv2"])

    a = add("page-0bca15e3db06d27a403b9b56")
    b = add("page-39339896168fac9235c3cd9d")
    case("report-confirm-09", "confirmation", "lido_earn_treasury_q2_coverage_reporting_202608",
         "How should an investor read the Lido Earn Q2 treasury report's first-loss coverage, units, control thresholds and reward treatment? Include the accountability questions raised in the reply without turning a question into a proven allegation.", [a, b], [
         point("q2_coverage", "Q2 reports 144.77 ETH equivalent first-loss coverage, 143.98 earnETH shares burned and a USD equivalent of $320,651.11; these are distinct units for the reported coverage.",
               (a, "ETH-equivalent coverage: 144.77 ETH\nUSD-equivalent impact: $320,651.11\nearnETH shares burned: 143.98 earnETH")),
         point("exception", "The report says the 1% loss-pause trigger was not reached, while a separately authorized Kelp-specific below-threshold coverage action occurred.",
               (a, "1% loss-pause control (per vault): not triggered"),
               (a, "This was authorized separately and is not the standard ≥1% first-loss trigger.")),
         point("retained", "Rewards were retained and compounded in vault share prices rather than remitted to Treasury in this reporting period.",
               (a, "Net rewards remitted to the Treasury this period: none (retained and compounded in vault share price per the mandate)"))], [
         point("accountability", "The reply asks about curator selection, criteria and accountability; it does not establish either the presence or absence of those mechanisms.",
               (b, "Why was Mellow chosen in the first place?"),
               (b, "Do these mechanisms actually exist?")),
         point("period", "The report covers Q2 and includes initial deployment history, not an audited forward return promise.",
               (a, "It covers Q2 2026 (April 1 – June 30, 2026) and includes the initial deployment"))],
         ["No loss occurred because the 1% pause trigger was not reached.", "The Treasury received cash payments equal to vault-reward entries.", "earnETH shares, ETH/WETH quantities and USD valuations are interchangeable.", "The questioning reply proves wrongdoing or confirms a completed investigation.", "This informational report authorizes a new allocation or independently audits the figures."],
         ["Values are attributed forum reports; the source pack does not independently reprice holdings or verify linked transactions.", "No response settling the accountability questions or prospective investment-return distribution is supplied."],
         ["Lido Earn", "Q2 2026", "first-loss", "Treasury", "ETH", "accountability"])

    a = add("page-3301ab0624ac2ac890c70e79")
    b = add("page-2de266bffd6d9cd73e314da4")
    c = add("page-f617ca5e2ea58a1c911b5286")
    case("report-confirm-10", "confirmation", "simple_dvt_regular_cluster_winddown_202605_09",
         "What was the scope of the Simple DVT regular-cluster wind-down, what does the later completion update report, and which operator compensation or migration details cannot safely be generalized?", [a, b, c], [
         point("scope", "The proposal concerns 72 regular clusters from July 1, 2026 and explicitly excludes 10 Super Clusters.",
               (a, "wind down the 72 regular clusters in the Simple DVT Module (SDVTM) starting on July 1, 2026"),
               (a, "This proposal does not affect the 10 Super Clusters")),
         point("completion", "The September update reports completion and says a first tranche of 0.175 stETH per eligible participant was sent.",
               (c, "the winding down process was completed."),
               (c, "the first tranche of the grant of 0.175 stETH per participant was sent out to the eligible node operators")),
         point("eligibility", "A participant asks about mixed Super/regular-cluster eligibility and per-operator versus per-position compensation; the question is not itself an answer or entitlement.",
               (b, "For an operator who holds multiple regular-cluster positions being wound down, is the 0.175 ETH grant paid once or once per cluster position?"))], [
         point("self_directed", "The original proposal says cluster formation, operations and liabilities under the continuation path are participant-directed, not a guaranteed seamless migration.",
               (a, "cluster formation, operations, and liabilities will be self-directed by participants."))],
         ["All Lido or all DVT validators were shut down.", "The first-tranche update proves every operator qualified or both tranches were paid.", "The participant's ETH-denominated question overrides the later update's stETH unit.", "Every cluster position guarantees another 0.175-unit payment.", "The saved forum report independently proves transaction settlement."],
         ["The full eligibility reconciliation and transaction receipts are outside this pack; linked transactions were not independently queried.", "No later performance data for operators who migrated to CSM is supplied."],
         ["Simple DVT", "regular clusters", "Super Clusters", "wind down", "grant"])

    protocol = {
        "design": "Small predeclared complete-report comparison on saved real source packs, not a population accuracy or trading-return benchmark.",
        "library_path": "data/library-halfyear-20260426-20261026",
        "library_window": {"start": "2026-04-26T00:00:00Z", "end_exclusive": "2026-10-26T00:00:00Z"},
        "observed_cutoff": CUTOFF,
        "source_policy": "Only saved public material from the existing half-year library. No fetching, invented observations, or provider calls during preparation. Original IDs, saved body, raw-response hash, exact spans and both clocks retained.",
        "split_policy": "2 development pilots and 10 confirmation cases isolated by original source and event family; not a chronological train/test claim. Common publishers/protocols can occur in both splits.",
        "selection_disclosure": "Convenience sample of 12 previously untested event families selected by the coding agent after catalog/source inspection; not random, not independently human labeled. Prior Jev48 and memory probes are excluded by family; generic infrastructure/protocol background may recur.",
        "excluded_previous_families": ["Soldogn Interop May recap", "Bitcoin script-interpreter crash", "Fed reserve-operation standardization", "Aave USDe MegaETH cap", "Hegota tier list", "Protocol Reddit AMA", "SEC crypto custody proposal", "Coinbase BTC versus gold", "Aave USDG Arbitrum onboarding", "PERCH mump2p Hoodi tests", "Lido audit demand", "FETH staking amendment", "Hoodi upgrade scheduling", "zkAPI", "Bitcoin Core private broadcast and patch releases", "Aave V4 risk-stewards activation", "Fed stablecoin identity/reserve proposal", "Lido Anchor audit", "Aave V3.8 proposal and flash-loan guard", "CSM 0x02 prover-bot budget"],
        "input_policy": "Same question, source IDs, source snippets, complete-original read access, observed cutoff, output contract, model and harness settings in both arms. Actual model/tool/context traces must be frozen per run. Do not pass expected rubrics or split to either report writer or Jev.",
        "original_access": "Both arms may autonomously plan and read/retrieve within the case pack. Common starting snippets are exact slices, never summaries. Complete saved originals are equally readable; full reads and Jev decisions count in total time/use. This is bounded source-pack research, not an unrestricted web-search comparison.",
        "report_contract": {"language": "English", "source_observations": "2-3", "closing": "one qualified inference or questions to watch", "citations": "saved source IDs; actual linked URLs retained", "mandatory_trading_advice": False, "price_prediction": False},
        "arms": ["original_off: original local Deep Agents + configured Doubao Pro, semantic checks off", "pro_checks: same author/harness with Doubao Pro evidence and final checks", "jev_routed: same author/harness with Jev-assisted evidence classification/context checks/final checks"],
        "run_policy": ["Start with one development-case triad, then finish both development pilots before confirmation; record any changes before opening confirmation results.", "Freeze harness/provider configuration and Jev thresholds before confirmation. Do not tune on confirmation outcomes.", "Run matched case triads under the same author model, source inventory and context allowance. Record run order and environment; one triad is not a latency distribution.", "Pilot author limit: at most 12 author calls and 2 mandatory review passes per report; the parent runner records all checker calls and enforces the actual request bound.", "Keep all planned cases in denominators, including timeout, failed publication, provider error, empty report or abstention. Do not silently replace failed cases.", "Independent reviewer sees anonymized report arms and actual evidence without draft rubric or provider/arm identity. Separately reconcile predeclared rubric; AI review is not human ground truth.", "The parent runner controls bounded actual provider requests; this preparation module makes none."],
        "predeclared_review_dimensions": ["correct source observations", "supported source-ID citations and attributable source genre", "material counterevidence and uncertainty", "proposal/schedule/execution distinctions", "units/rates/entity scope", "unsupported financial or causal claims", "observed-time integrity", "useful qualified inference or watch questions", "legitimate abstention/remaining gaps"],
        "predeclared_resource_dimensions": ["end-to-end wall time including Jev and audits", "all LLM calls and input/output/reasoning/cache tokens when reported", "all Jev calls, text sizes, usage/credits when reported", "tool calls and source/original reads", "failures, retries and cancellations", "measured provider cost separately from estimated list-price equivalent and plan quota"],
        "scoring_limits": ["Document hashes and exact quotes establish saved-source traceability, not independent truth of publisher claims.", "Automatic contract/citation/span checks are separate from semantic report review.", "Support comments, proposals, future schedules, third-party opinions and incomplete primary captures require attribution.", "No single profitability outcome, self-grade by the writer model or artificial context omission can establish usefulness.", "A 10-case confirmation set has wide uncertainty; report per-case errors and paired differences rather than claiming general superiority.", "Capture clocks are in late October. A May publication date does not permit a claim that the system knew it in May.", "Any post-freeze correction belongs outside this directory, with a revision ID and affected-case disclosure; do not silently update inputs or rubrics."]}
    return {"sources": sources, "cases": cases, "protocol": protocol}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, default=LIBRARY)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    built = construct(ResearchLibrary(args.library, create=False))
    manifest = freeze_dataset(args.output, **built)
    print("Frozen", manifest["case_count"], "cases and", manifest["source_count"], "saved sources at", args.output)


if __name__ == "__main__":
    main()
