use crate::memory::session_store::{ContentBlock, ContentBlockType, TranscriptEntry};

pub struct TranscriptBuffer {
    entries: Vec<TranscriptEntry>,
    max_in_memory: usize,
}

impl TranscriptBuffer {
    pub fn new(max_in_memory: usize) -> Self {
        Self {
            entries: Vec::new(),
            max_in_memory,
        }
    }

    pub fn push(&mut self, entry: TranscriptEntry) {
        self.entries.push(entry);
        if self.entries.len() > self.max_in_memory {
            self.entries.remove(0);
        }
    }

    pub fn compact(&mut self, keep_last: usize) {
        if self.entries.len() > keep_last {
            self.entries.drain(..self.entries.len() - keep_last);
        }
    }

    pub fn entries(&self) -> &[TranscriptEntry] {
        &self.entries
    }

    pub fn len(&self) -> usize {
        self.entries.len()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    pub fn clear(&mut self) {
        self.entries.clear();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_entry(seq: u32) -> TranscriptEntry {
        TranscriptEntry {
            role: "user".to_string(),
            content: vec![ContentBlock {
                block_type: ContentBlockType::Text,
                content: format!("msg {seq}"),
                is_error: false,
            }],
            timestamp: chrono::Utc::now().to_rfc3339(),
            turn_seq: seq,
        }
    }

    #[test]
    fn test_push_and_len() {
        let mut buf = TranscriptBuffer::new(100);
        buf.push(make_entry(0));
        buf.push(make_entry(1));
        assert_eq!(buf.len(), 2);
    }

    #[test]
    fn test_max_in_memory_evicts_oldest() {
        let mut buf = TranscriptBuffer::new(3);
        for i in 0..5 {
            buf.push(make_entry(i));
        }
        assert_eq!(buf.len(), 3);
        assert_eq!(buf.entries()[0].turn_seq, 2);
    }

    #[test]
    fn test_compact_keeps_last_n() {
        let mut buf = TranscriptBuffer::new(100);
        for i in 0..10 {
            buf.push(make_entry(i));
        }
        buf.compact(4);
        assert_eq!(buf.len(), 4);
        assert_eq!(buf.entries()[0].turn_seq, 6);
    }

    #[test]
    fn test_compact_noop_when_within_limit() {
        let mut buf = TranscriptBuffer::new(100);
        buf.push(make_entry(0));
        buf.compact(10);
        assert_eq!(buf.len(), 1);
    }

    #[test]
    fn test_clear() {
        let mut buf = TranscriptBuffer::new(100);
        buf.push(make_entry(0));
        buf.clear();
        assert!(buf.is_empty());
    }
}
