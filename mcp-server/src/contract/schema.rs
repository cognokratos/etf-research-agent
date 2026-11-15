//! Semantic comparison of tool input schemas.
//!
//! The same tool reaches the model through different serialisers: this server
//! (schemars, via rmcp) writes an optional string as `"type": ["string",
//! "null"]`; NAT's MCP client rebuilt it through pydantic as `"anyOf":
//! [{"type": "string"}, {"type": "null"}], "default": null`. A Rig agent will
//! produce a third form. Raw JSON equality would fail on every one of those, and
//! comparing only property names would pass a change from `string` to `integer`.
//!
//! So each schema is reduced to the parts that change what a caller may send —
//! types, nullability, required fields, enums, numeric and length bounds, nested
//! items and properties, and a non-null default — and the reductions are
//! compared. What is deliberately *not* compared, and why:
//!
//! * `description`, `title`, `$schema`: prose and metadata. Tool-level
//!   descriptions differ on purpose (NAT's `tool_overrides`).
//! * integer/number width `format` (`int32`, `int64`, `uint*`, `float`,
//!   `double`): schemars emits it, pydantic drops it, and no caller can express
//!   the difference in JSON. Any other `format` is compared.
//! * `default: null` on a nullable optional field: that is what "optional" means
//!   in both representations. A non-null default is compared.
//!
//! The reduction fails closed. Every keyword is either reduced (the
//! [`REDUCED`] list, plus `$ref` and a nullable `anyOf`/`oneOf`) or known to
//! change nothing a caller may send ([`ANNOTATIONS`]); anything else —
//! `allOf`, `not`, `if`/`then`/`else`, `dependentSchemas`, `contains`, an
//! unknown or vendor keyword — makes the schema not comparable, at every depth,
//! so it can never pass by being ignored. Siblings of `$ref` and of a nullable
//! union may only be annotations or a `default`. Numeric bounds are compared
//! exactly: integers as integers, and an integral float only where `f64` holds
//! it exactly (below 2^53).

use std::collections::{BTreeMap, BTreeSet};

use serde_json::{Map, Value};

const WIDTH_FORMATS: [&str; 10] =
    ["int8", "int16", "int32", "int64", "uint8", "uint16", "uint32", "uint64", "float", "double"];

const BOUNDS: [&str; 8] = [
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
];

/// Keywords whose meaning [`Shape`] captures, besides [`BOUNDS`].
const REDUCED: [&str; 10] =
    ["type", "enum", "const", "format", "pattern", "default", "items", "properties", "required", "additionalProperties"];

/// Keywords that never change what a caller may send. `$defs`/`definitions`
/// are only reached through a `$ref`, whose target is reduced in full.
const ANNOTATIONS: [&str; 10] = [
    "title",
    "description",
    "$schema",
    "$comment",
    "$defs",
    "definitions",
    "examples",
    "deprecated",
    "readOnly",
    "writeOnly",
];

/// Above this an `f64` no longer holds every integer, so an integral bound
/// written as a float may already have been rounded by the parser.
const EXACT_INTEGER_LIMIT: f64 = 9_007_199_254_740_992.0;

/// One schema, reduced to what constrains a value.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Shape {
    /// JSON types other than `null`; empty means unconstrained.
    pub types: BTreeSet<String>,
    /// Whether `null` is accepted: by `type` (if given) and by `enum` (if given).
    pub nullable: bool,
    /// Allowed non-null values, canonically serialised.
    pub enumeration: Option<BTreeSet<String>>,
    pub bounds: BTreeMap<String, String>,
    pub format: Option<String>,
    pub pattern: Option<String>,
    /// A non-null default, canonically serialised.
    pub default: Option<String>,
    pub items: Option<Box<Shape>>,
    pub properties: BTreeMap<String, Shape>,
    pub required: BTreeSet<String>,
    /// `false`, or the reduced schema for extra properties; `None` if allowed.
    pub additional_properties: Option<String>,
}

/// Reduce `schema`, resolving local `$ref`s against `root`. An error names the
/// path and the keyword or value the reduction cannot represent faithfully.
pub fn shape(schema: &Value, root: &Value) -> Result<Shape, String> {
    shape_at(schema, root, "$", 0)
}

fn shape_at(schema: &Value, root: &Value, path: &str, depth: usize) -> Result<Shape, String> {
    if depth > 32 {
        return Err(format!("{path}: schema nesting or $ref cycle deeper than 32"));
    }
    let object = match schema {
        Value::Bool(true) => return Ok(unconstrained()),
        Value::Object(object) => object,
        other => return Err(format!("{path}: not a supported schema: {other}")),
    };
    for keyword in object.keys() {
        let known = REDUCED.contains(&keyword.as_str())
            || BOUNDS.contains(&keyword.as_str())
            || ANNOTATIONS.contains(&keyword.as_str())
            || ["$ref", "anyOf", "oneOf"].contains(&keyword.as_str());
        if !known {
            return Err(format!("{path}: unsupported keyword {keyword:?}"));
        }
    }

    if let Some(reference) = object.get("$ref") {
        only_annotations_beside("$ref", object, path)?;
        let reference = reference.as_str().ok_or_else(|| format!("{path}: non-string $ref"))?;
        let target = resolve(reference, root).map_err(|error| format!("{path}: {error}"))?;
        let mut resolved = shape_at(target, root, path, depth + 1)?;
        resolved.default = merged_default(resolved.default, default_of(object), path)?;
        return Ok(resolved);
    }

    // anyOf/oneOf whose alternatives are one real schema plus `null` is how
    // pydantic writes Optional[T]: fold it into T + nullable.
    if object.contains_key("anyOf") && object.contains_key("oneOf") {
        return Err(format!("{path}: anyOf beside oneOf is not supported"));
    }
    for combinator in ["anyOf", "oneOf"] {
        if let Some(alternatives) = object.get(combinator) {
            only_annotations_beside(combinator, object, path)?;
            let alternatives =
                alternatives.as_array().ok_or_else(|| format!("{path}: {combinator} is not an array"))?;
            let (null, other): (Vec<&Value>, Vec<&Value>) =
                alternatives.iter().partition(|alternative| is_null_schema(alternative));
            if other.len() != 1 {
                return Err(format!("{path}: {combinator} with {} non-null alternatives is not supported", other.len()));
            }
            let mut inner = shape_at(other[0], root, path, depth + 1)?;
            if combinator == "oneOf" && inner.nullable && !null.is_empty() {
                // null would match both alternatives, which oneOf refuses.
                return Err(format!("{path}: oneOf with a nullable alternative beside null is not supported"));
            }
            inner.nullable |= !null.is_empty();
            inner.default = merged_default(inner.default, default_of(object), path)?;
            return Ok(inner);
        }
    }

    let mut out = unconstrained();
    let type_allows_null = match object.get("type") {
        None => true,
        Some(Value::String(name)) => add_type(&mut out, name),
        Some(Value::Array(names)) => {
            let mut null = false;
            for name in names {
                null |= add_type(&mut out, name.as_str().ok_or_else(|| format!("{path}: non-string in type array"))?);
            }
            null
        }
        Some(other) => return Err(format!("{path}: unsupported type value {other}")),
    };
    let allowed = match (object.get("enum"), object.get("const")) {
        (Some(_), Some(_)) => return Err(format!("{path}: enum beside const is not supported")),
        (Some(Value::Array(values)), None) => Some(values.as_slice()),
        (Some(other), None) => return Err(format!("{path}: enum is not an array: {other}")),
        (None, Some(constant)) => Some(std::slice::from_ref(constant)),
        (None, None) => None,
    };
    let enum_allows_null = match allowed {
        None => true,
        Some(values) => {
            out.enumeration = Some(values.iter().filter(|value| !value.is_null()).map(Value::to_string).collect());
            values.iter().any(Value::is_null)
        }
    };
    out.nullable = type_allows_null && enum_allows_null;
    for bound in BOUNDS {
        if let Some(value) = object.get(bound) {
            let number = canonical_number(value).map_err(|error| format!("{path}: {bound}: {error}"))?;
            out.bounds.insert(bound.to_string(), number);
        }
    }
    out.format = string_keyword(object, "format", path)?.filter(|format| !WIDTH_FORMATS.contains(&format.as_str()));
    out.pattern = string_keyword(object, "pattern", path)?;
    out.default = default_of(object);
    if let Some(items) = object.get("items") {
        out.items = Some(Box::new(shape_at(items, root, &format!("{path}[]"), depth + 1)?));
    }
    match object.get("properties") {
        None => {}
        Some(Value::Object(properties)) => {
            for (name, property) in properties {
                out.properties.insert(name.clone(), shape_at(property, root, &format!("{path}.{name}"), depth + 1)?);
            }
        }
        Some(other) => return Err(format!("{path}: properties is not an object: {other}")),
    }
    match object.get("required") {
        None => {}
        Some(Value::Array(names)) => {
            for name in names {
                let name = name.as_str().ok_or_else(|| format!("{path}: non-string in required"))?;
                out.required.insert(name.to_string());
            }
        }
        Some(other) => return Err(format!("{path}: required is not an array: {other}")),
    }
    out.additional_properties = match object.get("additionalProperties") {
        None | Some(Value::Bool(true)) => None,
        Some(Value::Bool(false)) => Some("false".into()),
        Some(schema) => Some(format!("{:?}", shape_at(schema, root, &format!("{path}.*"), depth + 1)?)),
    };
    Ok(out)
}

/// What the empty schema `{}` (or `true`) accepts: anything, `null` included.
fn unconstrained() -> Shape {
    Shape {
        types: BTreeSet::new(),
        nullable: true,
        enumeration: None,
        bounds: BTreeMap::new(),
        format: None,
        pattern: None,
        default: None,
        items: None,
        properties: BTreeMap::new(),
        required: BTreeSet::new(),
        additional_properties: None,
    }
}

/// Records a non-null type; returns whether `name` admits `null`.
fn add_type(shape: &mut Shape, name: &str) -> bool {
    if name == "null" {
        return true;
    }
    shape.types.insert(name.to_string());
    false
}

fn is_null_schema(schema: &Value) -> bool {
    schema.get("type").and_then(Value::as_str) == Some("null")
        && schema.as_object().is_some_and(|object| object.len() == 1)
}

/// A constraint beside `$ref` or a union would apply on top of it; rather than
/// model that intersection, only annotations and a `default` may sit there.
fn only_annotations_beside(keyword: &str, object: &Map<String, Value>, path: &str) -> Result<(), String> {
    for sibling in object.keys() {
        if sibling != keyword && sibling != "default" && !ANNOTATIONS.contains(&sibling.as_str()) {
            return Err(format!("{path}: {keyword} sibling {sibling:?} is not supported"));
        }
    }
    Ok(())
}

fn default_of(object: &Map<String, Value>) -> Option<String> {
    object.get("default").filter(|value| !value.is_null()).map(Value::to_string)
}

fn merged_default(inner: Option<String>, outer: Option<String>, path: &str) -> Result<Option<String>, String> {
    match (inner, outer) {
        (Some(left), Some(right)) if left != right => Err(format!("{path}: conflicting defaults {left} and {right}")),
        (left, right) => Ok(left.or(right)),
    }
}

fn string_keyword(object: &Map<String, Value>, keyword: &str, path: &str) -> Result<Option<String>, String> {
    match object.get(keyword) {
        None => Ok(None),
        Some(Value::String(value)) => Ok(Some(value.clone())),
        Some(other) => Err(format!("{path}: {keyword} is not a string: {other}")),
    }
}

/// `5` and `5.0` are the same bound; `2^53` and `2^53 + 1` are not. Integers
/// keep their exact value; an integral float is accepted only where `f64` is
/// exact, because beyond that the parser may already have merged two bounds.
fn canonical_number(value: &Value) -> Result<String, String> {
    let Value::Number(number) = value else {
        return Err(format!("not a number: {value}"));
    };
    if let Some(integer) = number.as_i64() {
        return Ok(integer.to_string());
    }
    if let Some(integer) = number.as_u64() {
        return Ok(integer.to_string());
    }
    let float = number.as_f64().ok_or_else(|| format!("unrepresentable number {number}"))?;
    if float.fract() != 0.0 {
        return Ok(format!("{float:?}"));
    }
    if float.abs() < EXACT_INTEGER_LIMIT {
        return Ok(format!("{}", float as i64));
    }
    Err(format!("integral float {number} is beyond the exact range of f64 and cannot be compared exactly"))
}

fn resolve<'a>(reference: &str, root: &'a Value) -> Result<&'a Value, String> {
    let pointer = reference.strip_prefix('#').ok_or_else(|| format!("non-local $ref {reference}"))?;
    root.pointer(pointer).ok_or_else(|| format!("unresolved $ref {reference}"))
}

/// Every semantic difference between two tool input schemas, path-qualified.
/// Empty means a caller valid against one is valid against the other; a schema
/// the reduction cannot represent is reported, never passed.
pub fn differences(expected: &Value, actual: &Value) -> Vec<String> {
    match (shape(expected, expected), shape(actual, actual)) {
        (Ok(left), Ok(right)) => {
            let mut out = Vec::new();
            compare("$", &left, &right, &mut out);
            out
        }
        (left, right) => [("expected", left), ("actual", right)]
            .into_iter()
            .filter_map(|(side, result)| result.err().map(|error| format!("{side} schema not comparable: {error}")))
            .collect(),
    }
}

fn compare(path: &str, expected: &Shape, actual: &Shape, out: &mut Vec<String>) {
    let mut field = |name: &str, left: String, right: String| {
        if left != right {
            out.push(format!("{path}: {name} {left} != {right}"));
        }
    };
    field("type", format!("{:?}", expected.types), format!("{:?}", actual.types));
    field("nullable", expected.nullable.to_string(), actual.nullable.to_string());
    field("enum", format!("{:?}", expected.enumeration), format!("{:?}", actual.enumeration));
    field("bounds", format!("{:?}", expected.bounds), format!("{:?}", actual.bounds));
    field("format", format!("{:?}", expected.format), format!("{:?}", actual.format));
    field("pattern", format!("{:?}", expected.pattern), format!("{:?}", actual.pattern));
    field("default", format!("{:?}", expected.default), format!("{:?}", actual.default));
    field("required", format!("{:?}", expected.required), format!("{:?}", actual.required));
    field(
        "additionalProperties",
        format!("{:?}", expected.additional_properties),
        format!("{:?}", actual.additional_properties),
    );
    let names: BTreeSet<&String> = expected.properties.keys().chain(actual.properties.keys()).collect();
    for name in names {
        let child = format!("{path}.{name}");
        match (expected.properties.get(name), actual.properties.get(name)) {
            (Some(left), Some(right)) => compare(&child, left, right, out),
            (Some(_), None) => out.push(format!("{child}: property missing")),
            (None, Some(_)) => out.push(format!("{child}: unexpected property")),
            (None, None) => unreachable!(),
        }
    }
    match (&expected.items, &actual.items) {
        (Some(left), Some(right)) => compare(&format!("{path}[]"), left, right, out),
        (None, None) => {}
        (left, right) => out.push(format!("{path}[]: items {} != {}", left.is_some(), right.is_some())),
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    fn same(left: Value, right: Value) {
        assert_eq!(differences(&left, &right), Vec::<String>::new(), "{left} vs {right}");
    }

    fn detected(left: Value, right: Value, needle: &str) {
        let found = differences(&left, &right);
        assert!(found.iter().any(|difference| difference.contains(needle)), "expected {needle:?} in {found:?}");
    }

    #[test]
    fn a_nullable_type_array_equals_an_any_of_with_null_and_a_null_default() {
        same(
            json!({"type": ["integer", "null"], "format": "int64", "description": "a"}),
            json!({"anyOf": [{"type": "integer"}, {"type": "null"}], "default": null, "description": "b"}),
        );
    }

    #[test]
    fn a_local_ref_is_resolved_before_comparison() {
        same(
            json!({"type": "object", "properties": {"d": {"$ref": "#/$defs/D"}}, "$defs": {"D": {"type": "string", "enum": ["a", "b"]}}}),
            json!({"type": "object", "properties": {"d": {"enum": ["b", "a"], "type": "string"}}}),
        );
    }

    #[test]
    fn integer_width_formats_are_ignored_but_other_formats_are_not() {
        same(json!({"type": "integer", "format": "int32"}), json!({"type": "integer"}));
        detected(json!({"type": "string", "format": "date"}), json!({"type": "string"}), "format");
    }

    #[test]
    fn a_changed_type_is_detected() {
        detected(json!({"type": ["string", "null"]}), json!({"type": ["integer", "null"]}), "type");
    }

    #[test]
    fn losing_nullability_is_detected() {
        detected(json!({"type": ["boolean", "null"]}), json!({"type": "boolean"}), "nullable");
    }

    #[test]
    fn a_changed_enum_is_detected() {
        detected(json!({"enum": ["reject", "research", "shortlist"]}), json!({"enum": ["reject", "shortlist"]}), "enum");
    }

    #[test]
    fn a_changed_bound_is_detected() {
        detected(json!({"type": "string", "maxLength": 4000}), json!({"type": "string", "maxLength": 400}), "bounds");
        detected(json!({"type": "integer"}), json!({"type": "integer", "minimum": 0}), "bounds");
        same(json!({"type": "integer", "maximum": 50}), json!({"type": "integer", "maximum": 50.0}));
    }

    #[test]
    fn a_changed_required_set_is_detected() {
        detected(
            json!({"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}),
            json!({"type": "object", "properties": {"a": {"type": "string"}}}),
            "required",
        );
    }

    #[test]
    fn nested_items_and_properties_are_compared() {
        detected(
            json!({"type": "array", "items": {"type": "object", "properties": {"x": {"type": "number"}}}}),
            json!({"type": "array", "items": {"type": "object", "properties": {"x": {"type": "string"}}}}),
            "$[].x: type",
        );
    }

    #[test]
    fn a_non_null_default_is_compared() {
        same(json!({"type": "integer", "default": 20}), json!({"type": "integer", "default": 20}));
        detected(json!({"type": "integer", "default": 20}), json!({"type": "integer", "default": 50}), "default");
        detected(json!({"type": "integer"}), json!({"type": "integer", "default": 50}), "default");
    }

    /// A schema the reduction cannot represent must be refused for the stated
    /// reason, on its own and even when compared with an identical copy.
    fn refused(schema: Value, plain: Value, reason: &str) {
        for found in [differences(&plain, &schema), differences(&schema, &schema)] {
            assert!(
                found.iter().any(|difference| difference.contains("not comparable") && difference.contains(reason)),
                "expected refusal {reason:?} in {found:?}"
            );
        }
    }

    #[test]
    fn an_unsupported_union_fails_loudly_instead_of_passing() {
        refused(
            json!({"anyOf": [{"type": "string"}, {"type": "integer"}]}),
            json!({"type": "string"}),
            "$: anyOf with 2 non-null alternatives",
        );
    }

    #[test]
    fn an_all_of_constraint_is_refused() {
        refused(json!({"type": "string", "allOf": [{"minLength": 10}]}), json!({"type": "string"}), r#"$: unsupported keyword "allOf""#);
    }

    #[test]
    fn a_not_constraint_is_refused() {
        refused(json!({"type": "string", "not": {"enum": ["x"]}}), json!({"type": "string"}), r#"$: unsupported keyword "not""#);
    }

    #[test]
    fn conditional_validation_is_refused() {
        refused(
            json!({"type": "object", "if": {"required": ["a"]}, "then": {"required": ["b"]}, "else": {}}),
            json!({"type": "object"}),
            "unsupported keyword",
        );
        for keyword in ["if", "then", "else", "dependentSchemas", "dependentRequired", "contains", "propertyNames", "x-vendor"] {
            refused(json!({"type": "object", keyword: {}}), json!({"type": "object"}), &format!("unsupported keyword {keyword:?}"));
        }
    }

    #[test]
    fn a_constraint_beside_a_ref_is_refused_but_annotations_and_defaults_are_kept() {
        let defs = json!({"D": {"type": "string"}});
        refused(
            json!({"type": "object", "properties": {"d": {"$ref": "#/$defs/D", "maxLength": 3}}, "$defs": defs}),
            json!({"type": "object", "properties": {"d": {"type": "string"}}}),
            r#"$.d: $ref sibling "maxLength""#,
        );
        same(
            json!({"type": "object", "properties": {"d": {"$ref": "#/$defs/D", "description": "x"}}, "$defs": defs}),
            json!({"type": "object", "properties": {"d": {"type": "string"}}}),
        );
        detected(
            json!({"type": "object", "properties": {"d": {"$ref": "#/$defs/D", "default": "a"}}, "$defs": defs}),
            json!({"type": "object", "properties": {"d": {"type": "string"}}}),
            "$.d: default",
        );
    }

    #[test]
    fn a_constraint_beside_a_nullable_union_is_refused() {
        refused(
            json!({"anyOf": [{"type": "string"}, {"type": "null"}], "default": null, "minLength": 1}),
            json!({"type": ["string", "null"]}),
            r#"$: anyOf sibling "minLength""#,
        );
    }

    #[test]
    fn unsupported_constraints_are_refused_at_every_depth() {
        refused(
            json!({"type": "object", "properties": {"a": {"type": "string", "not": {"const": ""}}}}),
            json!({"type": "object", "properties": {"a": {"type": "string"}}}),
            r#"$.a: unsupported keyword "not""#,
        );
        refused(
            json!({"type": "array", "items": {"type": "object", "dependentSchemas": {"a": {"required": ["b"]}}}}),
            json!({"type": "array", "items": {"type": "object"}}),
            r#"$[]: unsupported keyword "dependentSchemas""#,
        );
        refused(
            json!({"anyOf": [{"type": "string", "allOf": [{"minLength": 2}]}, {"type": "null"}]}),
            json!({"type": ["string", "null"]}),
            r#"$: unsupported keyword "allOf""#,
        );
        refused(
            json!({"type": "object", "properties": {"d": {"$ref": "#/$defs/D"}}, "$defs": {"D": {"type": "array", "contains": {}}}}),
            json!({"type": "object", "properties": {"d": {"type": "array"}}}),
            r#"$.d: unsupported keyword "contains""#,
        );
        refused(
            json!({"type": "object", "additionalProperties": {"type": "string", "if": {}}}),
            json!({"type": "object", "additionalProperties": {"type": "string"}}),
            r#"$.*: unsupported keyword "if""#,
        );
    }

    #[test]
    fn malformed_supported_keywords_are_refused_rather_than_skipped() {
        refused(json!({"type": "object", "required": "a"}), json!({"type": "object"}), "required is not an array");
        refused(json!({"type": "string", "enum": "a"}), json!({"type": "string"}), "enum is not an array");
        refused(json!({"type": "string", "pattern": 1}), json!({"type": "string"}), "pattern is not a string");
        refused(json!({"type": "integer", "exclusiveMinimum": true}), json!({"type": "integer"}), "exclusiveMinimum: not a number");
        refused(json!({"type": "array", "items": [{"type": "string"}]}), json!({"type": "array"}), "$[]: not a supported schema");
    }

    #[test]
    fn nullability_is_what_both_type_and_enum_admit() {
        detected(
            json!({"type": ["string", "null"], "enum": ["a"]}),
            json!({"type": ["string", "null"], "enum": ["a", null]}),
            "nullable",
        );
        same(json!({"type": "string", "enum": ["a", null]}), json!({"type": "string", "enum": ["a"]}));
    }

    #[test]
    fn distinct_large_integer_bounds_are_not_merged() {
        // 2^53 and 2^53 + 1 are the same f64; compared as integers they differ.
        detected(
            json!({"type": "integer", "maximum": 9_007_199_254_740_992_u64}),
            json!({"type": "integer", "maximum": 9_007_199_254_740_993_u64}),
            "bounds",
        );
        detected(
            json!({"type": "integer", "minimum": -9_007_199_254_740_993_i64}),
            json!({"type": "integer", "minimum": -9_007_199_254_740_992_i64}),
            "bounds",
        );
        detected(
            json!({"type": "integer", "maximum": u64::MAX}),
            json!({"type": "integer", "maximum": u64::MAX - 1}),
            "bounds",
        );
        same(json!({"type": "integer", "maximum": 9_007_199_254_740_993_u64}), json!({"type": "integer", "maximum": 9_007_199_254_740_993_u64}));
    }

    #[test]
    fn integral_float_bounds_are_exact_below_2_pow_53_and_refused_beyond() {
        same(json!({"type": "number", "maximum": 9_007_199_254_740_991_u64}), json!({"type": "number", "maximum": 9_007_199_254_740_991.0}));
        same(json!({"type": "number", "minimum": 0.5}), json!({"type": "number", "minimum": 0.5}));
        detected(json!({"type": "number", "minimum": 0.5}), json!({"type": "number", "minimum": 0.25}), "bounds");
        let beyond: Value = serde_json::from_str(r#"{"type": "number", "maximum": 9007199254740993.0}"#).expect("json");
        refused(beyond, json!({"type": "number", "maximum": 9_007_199_254_740_993_u64}), "beyond the exact range");
    }
}
