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

use std::collections::{BTreeMap, BTreeSet};

use serde_json::Value;

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

/// One schema, reduced to what constrains a value.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Shape {
    /// JSON types other than `null`; empty means unconstrained.
    pub types: BTreeSet<String>,
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
    pub additional_properties: Option<String>,
}

impl Shape {
    fn empty() -> Self {
        Self {
            types: BTreeSet::new(),
            nullable: false,
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
}

/// Reduce `schema`, resolving local `$ref`s against `root`.
pub fn shape(schema: &Value, root: &Value) -> Result<Shape, String> {
    shape_at(schema, root, 0)
}

fn shape_at(schema: &Value, root: &Value, depth: usize) -> Result<Shape, String> {
    if depth > 32 {
        return Err("schema nesting or $ref cycle deeper than 32".into());
    }
    let object = match schema {
        Value::Bool(true) => return Ok(Shape::empty()),
        Value::Object(object) => object,
        other => return Err(format!("not a schema: {other}")),
    };

    if let Some(reference) = object.get("$ref").and_then(Value::as_str) {
        let target = resolve(reference, root)?;
        let mut resolved = shape_at(target, root, depth + 1)?;
        // Siblings of $ref (draft 2019+) add to it; only nullability and a
        // default are ever placed there by the serialisers involved.
        let siblings = shape_at(&strip(object, &["$ref"]), root, depth + 1)?;
        resolved.nullable |= siblings.nullable;
        resolved.default = resolved.default.or(siblings.default);
        return Ok(resolved);
    }

    // anyOf/oneOf whose alternatives are one real schema plus `null` is how
    // pydantic writes Optional[T]: fold it into T + nullable.
    for combinator in ["anyOf", "oneOf"] {
        if let Some(alternatives) = object.get(combinator).and_then(Value::as_array) {
            let (null, other): (Vec<&Value>, Vec<&Value>) =
                alternatives.iter().partition(|alternative| is_null_schema(alternative));
            if other.len() != 1 {
                return Err(format!("{combinator} with {} non-null alternatives is not supported", other.len()));
            }
            let mut inner = shape_at(other[0], root, depth + 1)?;
            inner.nullable |= !null.is_empty();
            let outer = shape_at(&strip(object, &[combinator]), root, depth + 1)?;
            return Ok(merge(inner, outer));
        }
    }

    let mut out = Shape::empty();
    match object.get("type") {
        Some(Value::String(name)) => add_type(&mut out, name),
        Some(Value::Array(names)) => {
            for name in names {
                add_type(&mut out, name.as_str().ok_or("non-string in type array")?);
            }
        }
        Some(other) => return Err(format!("unsupported type value {other}")),
        None => {}
    }
    if let Some(values) = object.get("enum").and_then(Value::as_array) {
        let mut allowed = BTreeSet::new();
        for value in values {
            if value.is_null() {
                out.nullable = true;
            } else {
                allowed.insert(value.to_string());
            }
        }
        out.enumeration = Some(allowed);
    }
    if let Some(constant) = object.get("const") {
        out.enumeration = Some(BTreeSet::from([constant.to_string()]));
    }
    for bound in BOUNDS {
        if let Some(value) = object.get(bound) {
            out.bounds.insert(bound.to_string(), canonical_number(value));
        }
    }
    out.format = object
        .get("format")
        .and_then(Value::as_str)
        .filter(|format| !WIDTH_FORMATS.contains(format))
        .map(str::to_string);
    out.pattern = object.get("pattern").and_then(Value::as_str).map(str::to_string);
    out.default = object.get("default").filter(|value| !value.is_null()).map(Value::to_string);
    if let Some(items) = object.get("items") {
        out.items = Some(Box::new(shape_at(items, root, depth + 1)?));
    }
    if let Some(properties) = object.get("properties").and_then(Value::as_object) {
        for (name, property) in properties {
            out.properties.insert(name.clone(), shape_at(property, root, depth + 1)?);
        }
    }
    if let Some(required) = object.get("required").and_then(Value::as_array) {
        out.required = required.iter().filter_map(Value::as_str).map(str::to_string).collect();
    }
    out.additional_properties = match object.get("additionalProperties") {
        None | Some(Value::Bool(true)) => None,
        Some(other) => Some(other.to_string()),
    };
    Ok(out)
}

fn add_type(shape: &mut Shape, name: &str) {
    if name == "null" {
        shape.nullable = true;
    } else {
        shape.types.insert(name.to_string());
    }
}

fn is_null_schema(schema: &Value) -> bool {
    schema.get("type").and_then(Value::as_str) == Some("null")
        && schema.as_object().is_some_and(|object| object.len() == 1)
}

fn strip(object: &serde_json::Map<String, Value>, keys: &[&str]) -> Value {
    let mut copy = object.clone();
    for key in keys {
        copy.remove(*key);
    }
    Value::Object(copy)
}

fn merge(mut inner: Shape, outer: Shape) -> Shape {
    inner.nullable |= outer.nullable;
    inner.default = inner.default.or(outer.default);
    inner.types.extend(outer.types);
    inner.bounds.extend(outer.bounds);
    inner.format = inner.format.or(outer.format);
    inner.pattern = inner.pattern.or(outer.pattern);
    inner.enumeration = inner.enumeration.or(outer.enumeration);
    inner
}

/// `5` and `5.0` are the same bound.
fn canonical_number(value: &Value) -> String {
    match value.as_f64() {
        Some(number) => format!("{number}"),
        None => value.to_string(),
    }
}

fn resolve<'a>(reference: &str, root: &'a Value) -> Result<&'a Value, String> {
    let pointer = reference.strip_prefix('#').ok_or_else(|| format!("non-local $ref {reference}"))?;
    root.pointer(pointer).ok_or_else(|| format!("unresolved $ref {reference}"))
}

/// Every semantic difference between two tool input schemas, path-qualified.
/// Empty means a caller valid against one is valid against the other.
pub fn differences(expected: &Value, actual: &Value) -> Vec<String> {
    match (shape(expected, expected), shape(actual, actual)) {
        (Ok(left), Ok(right)) => {
            let mut out = Vec::new();
            compare("$", &left, &right, &mut out);
            out
        }
        (left, right) => vec![format!("unreadable schema: expected {left:?}, actual {right:?}")],
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

    #[test]
    fn an_unsupported_union_fails_loudly_instead_of_passing() {
        let found = differences(&json!({"anyOf": [{"type": "string"}, {"type": "integer"}]}), &json!({"type": "string"}));
        assert!(found[0].starts_with("unreadable schema"), "{found:?}");
    }
}
