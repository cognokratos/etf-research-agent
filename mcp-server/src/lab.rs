//! Inspection helper for the applied curriculum in `docs/applied/`.
//!
//! One ignored test, run only on request through `make rules-explain`. It prints
//! what the engine returns for one listing — through the same `rules::evaluate`
//! the MCP tools call, the same `component_evidence` regrouping, and the same
//! `annotate_rates` output boundary — so the labs can be done with no cluster, no
//! database and no model.
//!
//! It asserts nothing and writes nothing. In particular it does not regenerate
//! `evaluation/results/deterministic-etf-baseline.json`, which `make rules-test`
//! does: an experiment that edits `data/` can be inspected here without touching
//! the published evidence.
//!
//! `FACTS` overrides fields of the scored view, never the fixture on disk, so a
//! missing-data experiment needs no edit to `data/etfs.json`:
//!
//! ```text
//! make rules-explain ETF=VWCE-XETRA FACTS='{"top_10_concentration": null}'
//! ```

use serde_json::{json, Value};

use crate::domain::{annotate_rates, component_evidence};
use crate::fixtures::{load_etfs, load_profile, load_rules};
use crate::rules::{self, EtfFacts};

#[test]
#[ignore = "lab helper; run with `make rules-explain ETF=<etf_id>`"]
fn explain() {
    let etf_id = std::env::var("ETF").unwrap_or_else(|_| "IEAC-LSE".to_string());
    // Through the validating parser, exactly as at boot: a policy edit that the
    // server would refuse to start with panics here with the same message.
    let spec = load_rules();
    let profile = load_profile();
    let seed = load_etfs()
        .into_iter()
        .find(|etf| etf.etf_id == etf_id)
        .unwrap_or_else(|| panic!("{etf_id} is not an etf_id in data/etfs.json"));

    let mut facts = seed.facts();
    let mut overridden = Vec::new();
    if let Ok(raw) = std::env::var("FACTS")
        && !raw.trim().is_empty()
    {
        let overrides: serde_json::Map<String, Value> =
            serde_json::from_str(&raw).unwrap_or_else(|error| panic!("FACTS is not a JSON object: {error}"));
        let mut merged = serde_json::to_value(&facts).expect("facts serialise");
        for (field, value) in overrides {
            if field == "etf_id" || merged.get(&field).is_none() {
                panic!("FACTS names {field:?}, which is not a scored field of EtfFacts");
            }
            merged[&field] = value;
            overridden.push(field);
        }
        facts = serde_json::from_value::<EtfFacts>(merged).unwrap_or_else(|error| {
            panic!("FACTS has a value of the wrong type ({error}); text fields take \"\" for absent")
        });
    }

    let evaluation = rules::evaluate(&spec, &profile, &facts);
    let report = json!({
        "etf_id": etf_id,
        "inputs": {
            "rules_version": spec.version,
            "profile_id": profile.profile_id,
            "profile_version": profile.version,
            "facts_overridden": overridden,
            "facts": facts
        },
        "evaluation": evaluation,
        "component_evidence": component_evidence(&evaluation)
    });
    println!("{}", serde_json::to_string_pretty(&annotate_rates(report)).expect("serialise"));
}
