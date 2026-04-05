fn main() {
    tonic_build::configure()
        .build_server(true)
        .build_client(false)
        .compile_protos(
            &["../../../proto/jasusi.proto"],
            &["../../../proto"],
        )
        .expect("Failed to compile jasusi.proto");

    // eBPF compilation — skipped gracefully on non-Linux or if bpf-linker absent
    #[cfg(target_os = "linux")]
    {
        if std::path::Path::new("ebpf/tc_filter.bpf.c").exists() {
            if let Err(e) = aya_build::build_ebpf_programs(&["ebpf/tc_filter.bpf.c"]) {
                eprintln!("WARN: eBPF compilation skipped: {e}");
            }
        }
    }
}
