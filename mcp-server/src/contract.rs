//! The frozen NAT 1.9 contract, asserted from the MCP side.
//!
//! `contracts/nat-1.9/` records what the NAT agent actually did before the agent
//! runtime is replaced. Three of those records are about this crate, and they are
//! checked here against the real code rather than against a copy of it:
//!
//! * `mcp-tools.json` — the tool list this server publishes over MCP, names,
//!   descriptions and input schemas exactly. The model sees only six of the nine;
//!   the agent's allow-list is what hides the three token-taking mutations.
//! * `model-tools.json` — what NAT actually sent the model. Its MCP tools must be a
//!   subset of this server's, with the same properties, and must never include a
//!   tool that accepts an `approval_token`.
//! * `approval-vectors.json` — tokens minted by the Python agent. This verifier
//!   must accept each signature and reproduce every byte the minter signed.
//!
//! Set `UPDATE_CONTRACTS=1` to rewrite `mcp-tools.json` after an intended change
//! to the tool surface; the diff is then the review.

use std::collections::BTreeSet;
use std::fs;
use std::path::PathBuf;

use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use hmac::{Hmac, Mac};
use serde_json::{json, Value};
use sha2::Sha256;

use crate::approval::{find_action, payload_hash, testing, ApprovalClaims, ACTIONS};
use crate::fixtures::repo_root;
use crate::server::EtfMcpServer;

fn contract_path(name: &str) -> PathBuf {
    repo_root().join("contracts/nat-1.9").join(name)
}

fn read_contract(name: &str) -> Value {
    let path = contract_path(name);
    let text = fs::read_to_string(&path).unwrap_or_else(|error| panic!("read {}: {error}", path.display()));
    serde_json::from_str(&text).unwrap_or_else(|error| panic!("parse {}: {error}", path.display()))
}

/// The tool list as the MCP `tools/list` response carries it, ordered by name.
fn published_tools() -> Value {
    let mut tools: Vec<Value> = EtfMcpServer::published_tool_list()
        .into_iter()
        .map(|tool| serde_json::to_value(tool).expect("a tool serialises"))
        .collect();
    tools.sort_by(|left, right| left["name"].as_str().cmp(&right["name"].as_str()));
    Value::Array(tools)
}

const MODEL_VISIBLE_MCP_TOOLS: [&str; 6] = [
    "evaluate_etf",
    "get_etf",
    "get_etf_history",
    "get_research_context",
    "get_research_summary",
    "search_etfs",
];

const TOKEN_TAKING_MCP_TOOLS: [&str; 3] = ["assign_etf", "commit_evaluation", "shortlist_etf"];

#[test]
fn the_published_mcp_tool_list_matches_the_frozen_snapshot() {
    let published = published_tools();
    let path = contract_path("mcp-tools.json");
    if std::env::var("UPDATE_CONTRACTS").as_deref() == Ok("1") {
        let document = json!({
            "_comment": "tools/list as published by mcp-server (EtfMcpServer::tool_router). Asserted by contract::the_published_mcp_tool_list_matches_the_frozen_snapshot; regenerate with UPDATE_CONTRACTS=1 cargo test contract:: only for an intended change.",
            "tools": published,
        });
        fs::write(&path, serde_json::to_string_pretty(&document).expect("serialise") + "\n")
            .expect("write mcp-tools.json");
        return;
    }
    let frozen = read_contract("mcp-tools.json");
    assert_eq!(
        published, frozen["tools"],
        "the MCP tool surface differs from contracts/nat-1.9/mcp-tools.json; if intended, regenerate with UPDATE_CONTRACTS=1 and review the diff"
    );
}

#[test]
fn the_server_publishes_six_read_tools_and_three_token_taking_mutations() {
    let names: BTreeSet<String> = published_tools()
        .as_array()
        .expect("array")
        .iter()
        .map(|tool| tool["name"].as_str().expect("name").to_string())
        .collect();
    let expected: BTreeSet<String> = MODEL_VISIBLE_MCP_TOOLS
        .iter()
        .chain(TOKEN_TAKING_MCP_TOOLS.iter())
        .map(|name| name.to_string())
        .collect();
    assert_eq!(names, expected);

    for tool in published_tools().as_array().expect("array") {
        let name = tool["name"].as_str().expect("name");
        let properties = tool["inputSchema"]["properties"].as_object().cloned().unwrap_or_default();
        let takes_token = properties.contains_key("approval_token");
        assert_eq!(
            takes_token,
            TOKEN_TAKING_MCP_TOOLS.contains(&name),
            "{name}: only the three mutations may accept an approval token"
        );
        // No tool lets the model name who is acting.
        for identity in ["actor_id", "user_id", "actor"] {
            assert!(!properties.contains_key(identity), "{name} accepts {identity}");
        }
    }
}

/// What NAT sent the model must be this server's read tools, unchanged in shape,
/// plus the three agent-local approval functions — and never a token-taking tool.
#[test]
fn the_model_saw_only_the_read_tools_with_this_servers_properties() {
    let model = read_contract("model-tools.json");
    let published = published_tools();
    let mut seen_mcp = BTreeSet::new();
    let mut seen_local = BTreeSet::new();

    for tool in model["tools"].as_array().expect("tools") {
        let function = &tool["function"];
        let name = function["name"].as_str().expect("name");
        let parameters = &function["parameters"];
        match name.strip_prefix("etf_mcp__") {
            Some(mcp_name) => {
                seen_mcp.insert(mcp_name.to_string());
                let source = published
                    .as_array()
                    .expect("array")
                    .iter()
                    .find(|candidate| candidate["name"] == mcp_name)
                    .unwrap_or_else(|| panic!("model saw {name}, which this server does not publish"));
                let keys = |schema: &Value| -> BTreeSet<String> {
                    schema["properties"].as_object().map(|map| map.keys().cloned().collect()).unwrap_or_default()
                };
                assert_eq!(keys(parameters), keys(&source["inputSchema"]), "{name}: property set");
                let required = |schema: &Value| -> BTreeSet<String> {
                    schema["required"]
                        .as_array()
                        .map(|list| list.iter().filter_map(Value::as_str).map(str::to_string).collect())
                        .unwrap_or_default()
                };
                assert_eq!(required(parameters), required(&source["inputSchema"]), "{name}: required set");
            }
            None => {
                seen_local.insert(name.to_string());
                assert!(
                    !parameters["properties"].as_object().expect("properties").contains_key("approval_token"),
                    "{name}: an agent-local approval function must never take a token from the model"
                );
            }
        }
    }

    let expected_mcp: BTreeSet<String> = MODEL_VISIBLE_MCP_TOOLS.iter().map(|n| n.to_string()).collect();
    assert_eq!(seen_mcp, expected_mcp, "the model-visible MCP tools are the six read tools");
    let expected_local: BTreeSet<String> =
        ["assign_etf", "commit_evaluation", "shortlist_etf"].iter().map(|n| n.to_string()).collect();
    assert_eq!(seen_local, expected_local, "the three approval-gated functions are agent-local");
}

#[test]
fn the_action_registry_is_the_frozen_one() {
    let registry: Vec<(&str, bool, Vec<&str>)> = ACTIONS
        .iter()
        .map(|action| (action.name, action.carries_choice, action.allowed_choices.to_vec()))
        .collect();
    assert_eq!(
        registry,
        vec![
            ("commit", true, vec!["reject", "research", "shortlist"]),
            ("shortlist", true, vec!["shortlist"]),
            ("assign", false, vec![]),
        ]
    );
    assert_eq!(crate::approval::MAX_APPROVAL_LIFETIME_SECONDS, 1_800);
    assert_eq!(crate::approval::MIN_SECRET_LENGTH, 24);
}

/// Every Python-minted vector verifies here and re-encodes to the same bytes.
#[test]
fn python_minted_vectors_verify_and_canonicalise_byte_for_byte() {
    let document = read_contract("approval-vectors.json");
    let secret = document["secret"].as_str().expect("secret").as_bytes();
    assert_eq!(secret, testing::TEST_SECRET, "vectors are minted with the Rust tests' secret");
    let vectors = document["vectors"].as_array().expect("vectors");
    assert!(vectors.len() >= 6, "commit, shortlist and assign vectors are all present");

    for vector in vectors {
        let name = vector["name"].as_str().expect("name");
        let token = vector["token"].as_str().expect("token");
        let (payload_b64, signature_b64) = token.split_once('.').expect("token format");

        let mut mac = Hmac::<Sha256>::new_from_slice(secret).expect("secret");
        mac.update(payload_b64.as_bytes());
        mac.verify_slice(&URL_SAFE_NO_PAD.decode(signature_b64).expect("base64"))
            .unwrap_or_else(|_| panic!("{name}: signature must verify"));

        let signed = String::from_utf8(URL_SAFE_NO_PAD.decode(payload_b64).expect("base64")).expect("utf-8");
        assert_eq!(signed, vector["canonical_claims"].as_str().expect("canonical"), "{name}: signed bytes");

        let claims: ApprovalClaims = serde_json::from_str(&signed)
            .unwrap_or_else(|error| panic!("{name}: claims must deserialise: {error}"));
        // Re-minting the deserialised claims with this crate's canonical encoder
        // must reproduce the Python token exactly: same field set, same key order,
        // same escaping, same signature.
        assert_eq!(testing::mint(&claims), token, "{name}: Rust canonical encoding differs from Python");
        assert_eq!(
            payload_hash(&claims.payload).as_deref(),
            vector["payload_sha256"].as_str(),
            "{name}: payload digest"
        );
        assert_eq!(claims.payload_sha256, payload_hash(&claims.payload), "{name}: self-consistent digest");

        let action = find_action(&claims.action).unwrap_or_else(|| panic!("{name}: unknown action"));
        match (action.carries_choice, claims.choice.as_deref()) {
            (true, Some(choice)) => assert!(action.allowed_choices.contains(&choice), "{name}"),
            (false, None) => assert!(claims.expected_choice.is_none(), "{name}"),
            other => panic!("{name}: choice shape {other:?} disagrees with the registry"),
        }
        assert_eq!(
            claims.override_requested,
            claims.choice.is_some() && claims.choice != claims.expected_choice,
            "{name}: override_requested is derived, never asserted"
        );
        assert_eq!(claims.exp - document["now"].as_i64().expect("now"), 600, "{name}: configured ttl");
    }
}

/// The same vectors, refused when a single signed byte changes.
#[test]
fn a_vector_with_one_altered_claim_no_longer_verifies() {
    let document = read_contract("approval-vectors.json");
    let vector = &document["vectors"][0];
    let token = vector["token"].as_str().expect("token");
    let (payload_b64, signature_b64) = token.split_once('.').expect("format");
    let tampered = vector["canonical_claims"].as_str().expect("canonical").replacen("shortlist", "research", 1);
    let tampered_b64 = URL_SAFE_NO_PAD.encode(tampered.as_bytes());
    assert_ne!(tampered_b64, payload_b64);
    let mut mac = Hmac::<Sha256>::new_from_slice(testing::TEST_SECRET).expect("secret");
    mac.update(tampered_b64.as_bytes());
    assert!(mac.verify_slice(&URL_SAFE_NO_PAD.decode(signature_b64).expect("base64")).is_err());
}
