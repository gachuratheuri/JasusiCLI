use crate::memory::session_store::{ContentBlock, ContentBlockType, TranscriptEntry};

pub const MEMORY_FLUSH_THRESHOLD_TOKENS: u64 = 4_000;
pub const MAIN_COMPACTION_THRESHOLD_TOKENS: u64 = 10_000;
pub const DEEP_COMPACTION_THRESHOLD_TOKENS: u64 = 50_000;
pub const PRESERVE_RECENT: usize = 4;
pub const MAX_SUMMARY_CHARS: usize = 160;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CompactionStage {
    None,
    MemoryFlush,
    Main,
    Deep,
}

pub fn required_stage(total_tokens: u64) -> CompactionStage {
    if total_tokens >= DEEP_COMPACTION_THRESHOLD_TOKENS {
        CompactionStage::Deep
    } else if total_tokens >= MAIN_COMPACTION_THRESHOLD_TOKENS {
        CompactionStage::Main
    } else if total_tokens >= MEMORY_FLUSH_THRESHOLD_TOKENS {
        CompactionStage::MemoryFlush
    } else {
        CompactionStage::None
    }
}

pub fn compact_main(entries: &[TranscriptEntry], context_summary: &str) -> Vec<TranscriptEntry> {
    if entries.len() <= PRESERVE_RECENT {
        return entries
            .iter()
            .map(|e| TranscriptEntry {
                role: e.role.clone(),
                content: e
                    .content
                    .iter()
                    .map(|b| ContentBlock {
                        block_type: b.block_type.clone(),
                        content: strip_analysis_tags(&b.content),
                        is_error: b.is_error,
                    })
                    .collect(),
                timestamp: e.timestamp.clone(),
                turn_seq: e.turn_seq,
            })
            .collect();
    }

    let stripped: Vec<TranscriptEntry> = entries
        .iter()
        .map(|e| TranscriptEntry {
            role: e.role.clone(),
            content: e
                .content
                .iter()
                .map(|b| ContentBlock {
                    block_type: b.block_type.clone(),
                    content: strip_analysis_tags(&b.content),
                    is_error: b.is_error,
                })
                .collect(),
            timestamp: e.timestamp.clone(),
            turn_seq: e.turn_seq,
        })
        .collect();

    let summary_content = if context_summary.len() > MAX_SUMMARY_CHARS {
        &context_summary[..MAX_SUMMARY_CHARS]
    } else {
        context_summary
    };

    let summary_entry = TranscriptEntry {
        role: "system".to_string(),
        content: vec![ContentBlock {
            block_type: ContentBlockType::Text,
            content: format!("[COMPACTED CONTEXT]: {summary_content}"),
            is_error: false,
        }],
        timestamp: chrono::Utc::now().to_rfc3339(),
        turn_seq: 0,
    };

    let recent = &stripped[stripped.len() - PRESERVE_RECENT..];
    let mut result = vec![summary_entry];
    result.extend_from_slice(recent);
    result
}

fn strip_analysis_tags(text: &str) -> String {
    let mut result = text.to_string();
    while let (Some(start), Some(end)) = (result.find("<analysis>"), result.find("</analysis>")) {
        if start < end {
            result.replace_range(start..end + "</analysis>".len(), "");
        } else {
            break;
        }
    }
    result
}

pub fn compact_deep_summary(entries: &[TranscriptEntry], session_id: &str) -> String {
    let tool_calls: Vec<&TranscriptEntry> = entries
        .iter()
        .filter(|e| {
            e.content
                .iter()
                .any(|b| b.block_type == ContentBlockType::ToolUse)
        })
        .collect();

    let error_entries: Vec<&TranscriptEntry> = entries
        .iter()
        .filter(|e| e.content.iter().any(|b| b.is_error))
        .collect();

    format!(
        "# Session Summary: {}\n\
         **Turns:** {}\n\
         **Tool Calls:** {}\n\
         **Errors Resolved:** {}\n\
         **Status:** Compacted at {} — continuing from checkpoint",
        session_id,
        entries.len(),
        tool_calls.len(),
        error_entries.len(),
        chrono::Utc::now().to_rfc3339(),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_entry(role: &str, content: &str, seq: u32) -> TranscriptEntry {
        TranscriptEntry {
            role: role.to_string(),
            content: vec![ContentBlock {
                block_type: ContentBlockType::Text,
                content: content.to_string(),
                is_error: false,
            }],
            timestamp: chrono::Utc::now().to_rfc3339(),
            turn_seq: seq,
        }
    }

    #[test]
    fn test_required_stage_none_below_4k() {
        assert_eq!(required_stage(3_999), CompactionStage::None);
    }

    #[test]
    fn test_required_stage_memory_flush_at_4k() {
        assert_eq!(required_stage(4_000), CompactionStage::MemoryFlush);
    }

    #[test]
    fn test_required_stage_main_at_10k() {
        assert_eq!(required_stage(10_000), CompactionStage::Main);
    }

    #[test]
    fn test_required_stage_deep_at_50k() {
        assert_eq!(required_stage(50_000), CompactionStage::Deep);
    }

    #[test]
    fn test_compact_main_preserves_recent_4() {
        let entries: Vec<TranscriptEntry> =
            (0..10).map(|i| make_entry("user", &format!("msg {i}"), i)).collect();
        let result = compact_main(&entries, "summary");
        assert_eq!(result.len(), 5);
        assert_eq!(result.last().unwrap().turn_seq, 9);
    }

    #[test]
    fn test_compact_main_small_transcript_unchanged_count() {
        let entries: Vec<TranscriptEntry> =
            (0..3).map(|i| make_entry("user", "msg", i)).collect();
        let result = compact_main(&entries, "summary");
        assert_eq!(result.len(), 3);
    }

    #[test]
    fn test_compact_main_summary_truncated_to_160_chars() {
        let long_summary = "x".repeat(300);
        let entries: Vec<TranscriptEntry> =
            (0..8).map(|i| make_entry("user", "msg", i)).collect();
        let result = compact_main(&entries, &long_summary);
        let summary_content = &result[0].content[0].content;
        assert!(summary_content.len() <= "[COMPACTED CONTEXT]: ".len() + 160);
    }

    #[test]
    fn test_strip_analysis_tags() {
        let entries = vec![make_entry(
            "assistant",
            "Before <analysis>internal reasoning</analysis> after",
            0,
        )];
        let result = compact_main(&entries, "");
        let content = &result[0].content[0].content;
        assert!(!content.contains("<analysis>"));
        assert!(!content.contains("internal reasoning"));
        assert!(content.contains("Before"));
        assert!(content.contains("after"));
    }

    #[test]
    fn test_deep_compaction_summary_contains_session_id() {
        let entries: Vec<TranscriptEntry> =
            (0..5).map(|i| make_entry("user", "msg", i)).collect();
        let summary = compact_deep_summary(&entries, "my-session-123");
        assert!(summary.contains("my-session-123"));
        assert!(summary.contains("**Turns:** 5"));
    }
}
