//! Corpus-based evidence for formatting-preserving Python writes: the
//! lossless-CST round trip holds across representative real-world shapes, and
//! verified patches keep every unmodified region byte-identical.

use soleaux_intelligence::python_write::{
    SourcePatch, plan_ensure_import, verify_patches, verify_roundtrip,
};

// Multi-line literals keep real newlines and indentation; `\`-continuation
// escapes would strip the leading whitespace Python blocks depend on.
const CLASSIC_MODULE: &str = "#!/usr/bin/env python3
# -*- coding: utf-8 -*-
\"\"\"Module docstring with  odd   spacing preserved.\"\"\"

import os
import sys  # trailing comment
from typing import (
    Any,
    Mapping,
)


@functools.cache
async def handler(payload: Mapping[str, Any]) -> str | None:
    # internal comment    with spacing
    value = await fetch(payload)
    return f\"result={value!r:>12}\"


class Service:
    retries: int = 3

    def run(self) -> None:
        if (count := len(sys.argv)) > 1:
            print(count, file=sys.stderr)
";

const FSTRING_MODULE: &str = "width = 8
inner = f\"{'quoted literal'}\"
nested = f\"{f'{width:>{width}}'}\"
raw = rf\"\\d+{width}\"
multi = f\"\"\"line {width}
continues\"\"\"
";

const MATCH_MODULE: &str = "def route(event):
    match event:
        case {\"kind\": \"push\", \"ref\": str() as ref}:
            return ref
        case [first, *rest] if rest:
            return first
        case _:
            return None
";

const CONTINUATION_MODULE: &str = "total = 1 + \\
    2 + \\
    3
a = 1; b = 2 ; c = 3
text = (
    \"implicit\"
    \"concatenation\"
)
";

const TAB_MODULE: &str = "def tabbed():\n\tvalue = 1\n\tif value:\n\t\treturn value\n\treturn 0\n";

const CRLF_MODULE: &str = "import os\r\n\r\ndef windows_line_endings():\r\n    return os.name\r\n";

const UNICODE_MODULE: &str = "π = 3.14159
mängd = {\"ключ\": \"значение\"}
def grüße() -> str:
    return \"héllo 🌍\"
";

const GENERATOR_MODULE: &str = "def produce(*args, scale=1, **kwargs):
    yield from (item * scale for item in args if item)

consume = lambda xs: [x**2 for x in xs]
pairs = {key: value for key, value in items}
unique = {x for x in seen}
";

const DECORATOR_STACK_MODULE: &str = "@first
@second(arg=1)
@third.method()
class Layered:
    @property
    def value(self):
        global counter
        counter += 1
        return counter
";

const COMMENT_ONLY_MODULE: &str = "# just a comment\n# and another\n";

const NO_TRAILING_NEWLINE_MODULE: &str = "value = 1";

const CORPUS: &[(&str, &str)] = &[
    ("classic", CLASSIC_MODULE),
    ("fstrings", FSTRING_MODULE),
    ("match", MATCH_MODULE),
    ("continuations", CONTINUATION_MODULE),
    ("tabs", TAB_MODULE),
    ("crlf", CRLF_MODULE),
    ("unicode", UNICODE_MODULE),
    ("generators", GENERATOR_MODULE),
    ("decorators", DECORATOR_STACK_MODULE),
    ("comment_only", COMMENT_ONLY_MODULE),
    ("no_trailing_newline", NO_TRAILING_NEWLINE_MODULE),
    ("empty", ""),
];

#[test]
fn corpus_round_trips_byte_identically() {
    for (name, source) in CORPUS {
        let trip = verify_roundtrip(source);
        assert!(trip.parsed, "{name} failed to parse: {:?}", trip.error);
        assert!(trip.lossless, "{name} did not round-trip byte-identically");
    }
}

#[test]
fn utf8_bom_is_reported_and_round_trips() {
    let source = "\u{feff}value = 1\n";
    let trip = verify_roundtrip(source);
    assert!(trip.parsed);
    assert!(trip.lossless);
    assert!(trip.had_utf8_bom);
}

/// Replace one occurrence of `needle` in every corpus entry that contains it
/// and prove certification plus byte fidelity of both sides of the patch.
#[test]
fn corpus_patches_keep_unmodified_regions_byte_identical() {
    let rewrites: &[(&str, &str)] = &[
        ("return", "return "),
        ("value = 1", "value = 2"),
        ("width = 8", "width = 80"),
    ];
    let mut patched = 0usize;
    for (name, source) in CORPUS {
        let Some((needle, replacement)) = rewrites
            .iter()
            .find(|(needle, _)| source.contains(needle))
            .copied()
        else {
            continue;
        };
        let start = source.find(needle).expect("needle");
        let patch = SourcePatch {
            start_byte: start,
            end_byte: start + needle.len(),
            replacement: replacement.to_owned(),
        };
        let verification =
            verify_patches(source, std::slice::from_ref(&patch)).expect("verification");
        assert!(
            verification.certified,
            "{name} patch was not certified: {:?}",
            verification.diagnostics
        );
        assert!(verification.unmodified_regions_intact, "{name}");
        assert_eq!(
            &verification.postimage_source[..start],
            &source[..start],
            "{name}: prefix bytes changed"
        );
        assert_eq!(
            &verification.postimage_source[start + replacement.len()..],
            &source[start + needle.len()..],
            "{name}: suffix bytes changed"
        );
        patched += 1;
    }
    assert!(patched >= 4, "corpus rewrite coverage collapsed: {patched}");
}

#[test]
fn multiple_disjoint_patches_are_verified_together() {
    let source = "alpha = 1\nbeta = 2\ngamma = 3\n";
    let patches = vec![
        SourcePatch {
            start_byte: source.find("1").expect("1"),
            end_byte: source.find("1").expect("1") + 1,
            replacement: "10".to_owned(),
        },
        SourcePatch {
            start_byte: source.find("3").expect("3"),
            end_byte: source.find("3").expect("3") + 1,
            replacement: "30".to_owned(),
        },
    ];
    let verification = verify_patches(source, &patches).expect("verification");
    assert!(verification.certified, "{:?}", verification.diagnostics);
    assert_eq!(
        verification.postimage_source,
        "alpha = 10\nbeta = 2\ngamma = 30\n"
    );
}

#[test]
fn patch_producing_indentation_error_fails_closed() {
    let source = "def f():\n    return 1\n";
    let start = source.find("    return 1").expect("body");
    let patch = SourcePatch {
        start_byte: start,
        end_byte: start + "    return 1".len(),
        replacement: "  return 1\n      misaligned = 2".to_owned(),
    };
    let verification = verify_patches(source, &[patch]).expect("verification");
    assert!(!verification.certified);
    assert!(!verification.postimage.parsed);
}

#[test]
fn broken_preimage_is_reported_not_certified() {
    let source = "def broken(:\n    pass\n";
    let patch = SourcePatch {
        start_byte: 0,
        end_byte: 0,
        replacement: "# header\n".to_owned(),
    };
    let verification = verify_patches(source, &[patch]).expect("verification");
    assert!(!verification.certified);
    assert!(!verification.preimage.parsed);
    assert!(
        verification
            .diagnostics
            .iter()
            .any(|entry| entry.contains("preimage"))
    );
}

#[test]
fn ensure_import_lands_after_the_import_block() {
    let patch = plan_ensure_import(CLASSIC_MODULE, "json", None)
        .expect("plan")
        .expect("patch");
    let verification =
        verify_patches(CLASSIC_MODULE, std::slice::from_ref(&patch)).expect("verification");
    assert!(verification.certified, "{:?}", verification.diagnostics);
    let post = &verification.postimage_source;
    let import_offset = post.find("\nimport json\n").expect("inserted import");
    assert!(import_offset > post.find("from typing import").expect("existing import"));
    assert!(import_offset < post.find("@functools.cache").expect("first definition"));
}

#[test]
fn ensure_import_without_imports_lands_after_docstring_and_comments() {
    let source = "#!/usr/bin/env python3\n\"\"\"Doc.\"\"\"\n\nvalue = 1\n";
    let patch = plan_ensure_import(source, "os", None)
        .expect("plan")
        .expect("patch");
    let verification = verify_patches(source, std::slice::from_ref(&patch)).expect("verification");
    assert!(verification.certified, "{:?}", verification.diagnostics);
    assert_eq!(
        verification.postimage_source,
        "#!/usr/bin/env python3\n\"\"\"Doc.\"\"\"\n\nimport os\n\nvalue = 1\n"
    );
}

#[test]
fn ensure_import_uses_crlf_in_crlf_files() {
    let patch = plan_ensure_import(CRLF_MODULE, "sys", None)
        .expect("plan")
        .expect("patch");
    assert!(patch.replacement.contains("import sys\r\n"));
    let verification =
        verify_patches(CRLF_MODULE, std::slice::from_ref(&patch)).expect("verification");
    assert!(verification.certified, "{:?}", verification.diagnostics);
}

#[test]
fn ensure_import_into_empty_and_bare_modules() {
    let patch = plan_ensure_import("", "os", None)
        .expect("plan")
        .expect("patch");
    let verification = verify_patches("", std::slice::from_ref(&patch)).expect("verification");
    assert!(verification.certified);
    assert_eq!(verification.postimage_source, "import os\n");

    let patch = plan_ensure_import(NO_TRAILING_NEWLINE_MODULE, "os", None)
        .expect("plan")
        .expect("patch");
    let verification = verify_patches(NO_TRAILING_NEWLINE_MODULE, std::slice::from_ref(&patch))
        .expect("verification");
    assert!(verification.certified, "{:?}", verification.diagnostics);
    assert_eq!(verification.postimage_source, "import os\n\nvalue = 1");
}
