use runtime::IncrementalSseParser;

#[test]
fn test_incremental_sse_bytes_and_multiline_events() {
    let mut parser = IncrementalSseParser::new();

    // Event 1 split across 3 byte chunks
    let chunk1 = b"event: update\ndata: line 1\n";
    let chunk2 = b"data: line 2\n\n";

    let events1 = parser.push_bytes(chunk1);
    assert!(events1.is_empty());

    let events2 = parser.push_bytes(chunk2);
    assert_eq!(events2.len(), 1);
    assert_eq!(events2[0].event.as_deref(), Some("update"));
    assert_eq!(events2[0].data, "line 1\nline 2");
}

#[test]
fn test_multibyte_utf8_split_across_chunks() {
    let mut parser = IncrementalSseParser::new();

    let full_event = "event: message\ndata: 🚀 Antigravity Rocket Engine 🌟\n\n";
    let bytes = full_event.as_bytes();

    // Split right in middle of 4-byte rocket emoji 🚀 (bytes 21..25)
    let part1 = &bytes[..23];
    let part2 = &bytes[23..];

    let events1 = parser.push_bytes(part1);
    assert!(events1.is_empty());

    let events2 = parser.push_bytes(part2);
    assert_eq!(events2.len(), 1);
    assert_eq!(events2[0].event.as_deref(), Some("message"));
    assert_eq!(events2[0].data, "🚀 Antigravity Rocket Engine 🌟");
}

#[test]
fn test_crlf_and_comment_flushing() {
    let mut parser = IncrementalSseParser::new();

    let raw = ": ping comment\r\nevent: completion\r\ndata: finished\r\n\r\n";
    let events = parser.push_bytes(raw.as_bytes());

    assert_eq!(events.len(), 1);
    assert_eq!(events[0].event.as_deref(), Some("completion"));
    assert_eq!(events[0].data, "finished");
}
