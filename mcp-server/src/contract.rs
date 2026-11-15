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
//!   subset of this server's with semantically equal input schemas (see
//!   [`schema`]), and must never include a tool that accepts an `approval_token`.
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

mod schema;

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

/// The six model-visible MCP tools, as `(name the model saw, NAT parameters,
/// this server's inputSchema)`.
fn model_visible_pairs() -> Vec<(String, Value, Value)> {
    let model = read_contract("model-tools.json");
    let published = published_tools();
    model["tools"]
        .as_array()
        .expect("tools")
        .iter()
        .filter_map(|tool| {
            let name = tool["function"]["name"].as_str()?;
            let mcp_name = name.strip_prefix("etf_mcp__")?;
            let source = published
                .as_array()
                .expect("array")
                .iter()
                .find(|candidate| candidate["name"] == mcp_name)
                .unwrap_or_else(|| panic!("model saw {name}, which this server does not publish"));
            Some((name.to_string(), tool["function"]["parameters"].clone(), source["inputSchema"].clone()))
        })
        .collect()
}

/// What NAT sent the model must be this server's read tools, semantically
/// unchanged — types, nullability, required, enums, bounds, nested shapes and
/// non-null defaults, compared through [`schema::differences`] so pydantic's
/// `anyOf[T, null]` and schemars' `["T", "null"]` are equal — plus the three
/// agent-local approval functions, and never a token-taking tool.
#[test]
fn the_model_saw_only_the_read_tools_with_this_servers_schemas() {
    let model = read_contract("model-tools.json");
    let mut seen_local = BTreeSet::new();
    for tool in model["tools"].as_array().expect("tools") {
        let name = tool["function"]["name"].as_str().expect("name");
        if !name.starts_with("etf_mcp__") {
            seen_local.insert(name.to_string());
            assert!(
                !tool["function"]["parameters"]["properties"].as_object().expect("properties").contains_key("approval_token"),
                "{name}: an agent-local approval function must never take a token from the model"
            );
        }
    }

    let pairs = model_visible_pairs();
    let seen_mcp: BTreeSet<String> =
        pairs.iter().map(|(name, _, _)| name.trim_start_matches("etf_mcp__").to_string()).collect();
    let expected_mcp: BTreeSet<String> = MODEL_VISIBLE_MCP_TOOLS.iter().map(|n| n.to_string()).collect();
    assert_eq!(seen_mcp, expected_mcp, "the model-visible MCP tools are the six read tools");
    for (name, model_schema, server_schema) in &pairs {
        let differences = schema::differences(model_schema, server_schema);
        assert!(differences.is_empty(), "{name}: model-visible schema differs from this server's: {differences:#?}");
    }

    let expected_local: BTreeSet<String> =
        ["assign_etf", "commit_evaluation", "shortlist_etf"].iter().map(|n| n.to_string()).collect();
    assert_eq!(seen_local, expected_local, "the three approval-gated functions are agent-local");
}

/// The comparison above is only worth something if it fails. Each mutation is
/// applied to a copy of this server's real published schema and must be named.
#[test]
fn an_incompatible_change_to_a_published_schema_is_detected() {
    let pairs = model_visible_pairs();
    let schema_of = |tool: &str| -> (Value, Value) {
        let (_, model, server) =
            pairs.iter().find(|(name, _, _)| name == &format!("etf_mcp__{tool}")).expect("tool").clone();
        (model, server)
    };
    type Mutation = Box<dyn Fn(&mut Value)>;
    let mutations: Vec<(&str, &str, Mutation)> = vec![
        ("evaluate_etf", "llm_recommendation: type", Box::new(|s| s["properties"]["llm_recommendation"]["type"] = json!(["integer", "null"]))),
        ("search_etfs", "ucits: nullable", Box::new(|s| s["properties"]["ucits"]["type"] = json!("boolean"))),
        ("search_etfs", "min_investment_score: bounds", Box::new(|s| s["properties"]["min_investment_score"]["maximum"] = json!(100))),
        ("search_etfs", "decision: enum", Box::new(|s| s["properties"]["decision"]["enum"] = json!(["reject", "research", "shortlist"]))),
        ("get_etf_history", "limit: default", Box::new(|s| s["properties"]["limit"]["default"] = json!(50))),
        ("get_etf", "$: required", Box::new(|s| s["required"] = json!([]))),
        ("get_etf", "etf: type", Box::new(|s| s["properties"]["etf"]["type"] = json!("array"))),
        ("get_research_context", "unexpected property", Box::new(|s| s["properties"]["actor_id"] = json!({"type": "string"}))),
        ("get_research_summary", "additionalProperties", Box::new(|s| s["additionalProperties"] = json!(false))),
    ];
    for (tool, needle, mutate) in mutations {
        let (model, mut server) = schema_of(tool);
        assert!(schema::differences(&model, &server).is_empty(), "{tool}: baseline must pass first");
        mutate(&mut server);
        let found = schema::differences(&model, &server);
        assert!(found.iter().any(|difference| difference.contains(needle)), "{tool}: expected {needle:?}, got {found:?}");
    }
}

/// The agent-local approval functions have no MCP counterpart to compare with,
/// so their model-facing constraints are pinned here as the bar a Rust port
/// must meet: the decision vocabulary, the length bounds, what is required.
#[test]
fn the_approval_functions_model_facing_constraints_are_frozen() {
    let model = read_contract("model-tools.json");
    let parameters = |name: &str| -> schema::Shape {
        let tool = model["tools"].as_array().expect("tools").iter().find(|t| t["function"]["name"] == name).expect(name);
        let schema = &tool["function"]["parameters"];
        schema::shape(schema, schema).expect("readable")
    };
    let decisions: BTreeSet<String> = ["\"reject\"", "\"research\"", "\"shortlist\""].iter().map(|s| s.to_string()).collect();
    let bounds = |pairs: &[(&str, &str)]| -> std::collections::BTreeMap<String, String> {
        pairs.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect()
    };
    let set = |names: &[&str]| -> BTreeSet<String> { names.iter().map(|n| n.to_string()).collect() };

    let commit = parameters("commit_evaluation");
    assert_eq!(commit.required, set(&["etf_id", "rules_decision", "requested_decision", "summary"]));
    for field in ["rules_decision", "requested_decision"] {
        let shape = &commit.properties[field];
        assert_eq!((shape.enumeration.as_ref(), shape.nullable), (Some(&decisions), false), "{field}");
    }
    let recommendation = &commit.properties["llm_recommendation"];
    assert_eq!((recommendation.enumeration.as_ref(), recommendation.nullable), (Some(&decisions), true));
    assert_eq!(commit.properties["research_note"].bounds, bounds(&[("maxLength", "4000")]));
    assert!(commit.properties["research_note"].nullable);
    assert_eq!(commit.properties["summary"].bounds, bounds(&[("maxLength", "1500"), ("minLength", "1")]));
    assert_eq!(commit.properties["etf_id"].bounds, bounds(&[("minLength", "1")]));

    let shortlist = parameters("shortlist_etf");
    assert_eq!(shortlist.required, set(&["etf_id", "rules_decision", "research_note", "summary"]));
    assert_eq!(shortlist.properties["rules_decision"].enumeration.as_ref(), Some(&decisions));
    assert_eq!(shortlist.properties["research_note"].bounds, bounds(&[("maxLength", "4000"), ("minLength", "1")]));
    assert!(!shortlist.properties["research_note"].nullable, "a shortlist note is mandatory");

    let assign = parameters("assign_etf");
    assert_eq!(assign.required, set(&["etf_id", "assignee", "summary"]));
    assert_eq!(assign.properties["assignee"].bounds, bounds(&[("maxLength", "200"), ("minLength", "1")]));
    for shape in [&commit, &shortlist, &assign] {
        assert!(!shape.properties.contains_key("actor_id") && !shape.properties.contains_key("request_id"));
    }
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
