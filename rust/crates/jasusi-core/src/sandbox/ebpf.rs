#[derive(Debug, Clone, PartialEq, Eq)]
pub enum EbpfStatus {
    Attached { program_name: String },
    Unavailable,
}

pub fn attach_tc_filter() -> EbpfStatus {
    EbpfStatus::Unavailable
}
