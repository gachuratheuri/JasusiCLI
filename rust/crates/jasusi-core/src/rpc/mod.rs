pub mod server;

#[allow(clippy::default_trait_access)]
pub mod proto {
    tonic::include_proto!("jasusi.v3");
}
