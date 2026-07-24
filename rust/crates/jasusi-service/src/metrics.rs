use std::collections::HashMap;
use std::fmt::Write;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

/// A single metric point in a Prometheus-compatible exposition.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MetricPoint {
    pub name: String,
    pub value: u64,
    pub unit: String,
    pub timestamp_ms: u64,
    pub labels: Vec<(String, String)>,
    /// Histogram aggregates. `None` for counters and gauges.
    pub sum: Option<u64>,
    pub min: Option<u64>,
    pub max: Option<u64>,
}

/// Family of points sharing the same metric name.
#[derive(Debug, Clone)]
pub struct MetricFamily {
    pub name: String,
    pub help: String,
    pub metric_type: MetricType,
    pub points: Vec<MetricPoint>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MetricType {
    Counter,
    Gauge,
    Histogram,
}

impl MetricType {
    #[must_use]
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Counter => "counter",
            Self::Gauge => "gauge",
            Self::Histogram => "histogram",
        }
    }
}

/// Internal storage for one metric name, keyed by label set.
#[derive(Debug, Default)]
struct MetricCell {
    counter: AtomicU64,
    gauge: AtomicU64,
    /// Sum and count for histogram approximation.
    hist_sum: AtomicU64,
    hist_count: AtomicU64,
    hist_min: AtomicU64,
    hist_max: AtomicU64,
}

type LabelSet = Vec<(String, String)>;
type MetricFamilies = HashMap<String, HashMap<LabelSet, Arc<MetricCell>>>;

impl MetricCell {
    fn new() -> Self {
        Self {
            counter: AtomicU64::new(0),
            gauge: AtomicU64::new(0),
            hist_sum: AtomicU64::new(0),
            hist_count: AtomicU64::new(0),
            hist_min: AtomicU64::new(u64::MAX),
            hist_max: AtomicU64::new(0),
        }
    }

    fn increment(&self, n: u64) {
        self.counter.fetch_add(n, Ordering::Relaxed);
    }

    fn set_gauge(&self, v: u64) {
        self.gauge.store(v, Ordering::Relaxed);
    }

    fn record_histogram(&self, v: u64) {
        self.hist_sum.fetch_add(v, Ordering::Relaxed);
        self.hist_count.fetch_add(1, Ordering::Relaxed);
        self.hist_min.fetch_min(v, Ordering::Relaxed);
        let _ = self
            .hist_max
            .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |old| {
                if v > old {
                    Some(v)
                } else {
                    Some(old)
                }
            });
    }
}

/// Self-contained metrics registry covering all phase-8 KPIs.
///
/// Counters, gauges, and histograms are recorded lock-free. Prometheus
/// rendering is performed on demand.
#[derive(Debug)]
pub struct MetricsRecorder {
    metrics: Arc<Mutex<MetricFamilies>>,
}

impl MetricsRecorder {
    #[must_use]
    pub fn new() -> Self {
        Self {
            metrics: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    fn cell(&self, name: &str, labels: &[(&str, &str)]) -> Arc<MetricCell> {
        let mut key: Vec<(String, String)> = Vec::new();
        for (k, v) in labels {
            key.push((k.to_string(), v.to_string()));
        }
        key.sort_unstable();
        let mut metrics = self
            .metrics
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        metrics
            .entry(name.to_string())
            .or_default()
            .entry(key)
            .or_insert_with(|| Arc::new(MetricCell::new()))
            .clone()
    }

    /// Increments a counter metric by `n`.
    pub fn counter(&self, name: &str, labels: &[(&str, &str)], n: u64) {
        self.cell(name, labels).increment(n);
    }

    /// Sets a gauge metric.
    pub fn gauge(&self, name: &str, labels: &[(&str, &str)], v: u64) {
        self.cell(name, labels).set_gauge(v);
    }

    /// Records a histogram observation (latency, duration, size).
    pub fn histogram(&self, name: &str, labels: &[(&str, &str)], v: u64) {
        self.cell(name, labels).record_histogram(v);
    }

    /// Records request latency and increments request count.
    pub fn record_request(&self, path: &str, status: &str, latency_ms: u64) {
        self.counter(
            "jasusi_requests_total",
            &[("path", path), ("status", status)],
            1,
        );
        self.histogram(
            "jasusi_request_latency_ms",
            &[("path", path), ("status", status)],
            latency_ms,
        );
    }

    /// Convenience: record tool latency.
    pub fn record_tool(&self, tool_name: &str, latency_ms: u64) {
        self.counter("jasusi_tool_calls_total", &[("tool", tool_name)], 1);
        self.histogram("jasusi_tool_latency_ms", &[("tool", tool_name)], latency_ms);
    }

    /// Convenience: record first-token latency.
    pub fn record_first_token(&self, provider: &str, latency_ms: u64) {
        self.histogram(
            "jasusi_first_token_latency_ms",
            &[("provider", provider)],
            latency_ms,
        );
    }

    /// Convenience: record provider fallback or error.
    pub fn record_provider_event(&self, provider: &str, event: &str) {
        self.counter(
            "jasusi_provider_events_total",
            &[("provider", provider), ("event", event)],
            1,
        );
    }

    /// Convenience: queue depth.
    pub fn set_queue_depth(&self, depth: u64) {
        self.gauge("jasusi_queue_depth", &[], depth);
    }

    /// Convenience: rejection count.
    pub fn record_rejection(&self, reason: &str) {
        self.counter("jasusi_rejections_total", &[("reason", reason)], 1);
    }

    /// Convenience: cancellation latency and orphan process observation.
    pub fn record_cancellation(&self, latency_ms: u64, orphan_count: u64) {
        self.histogram("jasusi_cancellation_latency_ms", &[], latency_ms);
        self.gauge("jasusi_orphan_process_count", &[], orphan_count);
    }

    /// Convenience: budget exhaustion.
    pub fn record_budget_exhaustion(&self, budget_type: &str, scope: &str) {
        self.counter(
            "jasusi_budget_exhaustion_total",
            &[("type", budget_type), ("scope", scope)],
            1,
        );
    }

    /// Convenience: sandbox denial.
    pub fn record_sandbox_denial(&self, tool: &str, reason: &str) {
        self.counter(
            "jasusi_sandbox_denials_total",
            &[("tool", tool), ("reason", reason)],
            1,
        );
    }

    /// Convenience: session recovery/migration failure.
    pub fn record_recovery_event(&self, kind: &str, outcome: &str) {
        self.counter(
            "jasusi_recovery_events_total",
            &[("kind", kind), ("outcome", outcome)],
            1,
        );
    }

    /// Renders the current metric values as Prometheus exposition text.
    #[must_use]
    pub fn render_prometheus(&self) -> String {
        let mut out = String::new();
        let now = now_ms();
        for family in self.families() {
            let _ = writeln!(
                &mut out,
                "# HELP {name} {help}\n# TYPE {name} {mtype}",
                name = family.name,
                help = family.help,
                mtype = family.metric_type.as_str()
            );
            for point in &family.points {
                let labels = render_labels(&point.labels);
                if family.metric_type == MetricType::Histogram {
                    // Prometheus requires a bucket, sum, and count series for
                    // a histogram.  The service keeps an intentionally small
                    // aggregate, so the +Inf bucket is the exact observation
                    // count and remains semantically valid.
                    let bucket_labels = add_label(&point.labels, "le", "+Inf");
                    let _ = writeln!(
                        &mut out,
                        "{}_bucket{} {} {}",
                        family.name,
                        render_labels(&bucket_labels),
                        point.value,
                        now
                    );
                    let _ = writeln!(
                        &mut out,
                        "{}_sum{} {} {}",
                        family.name,
                        labels,
                        point.sum.unwrap_or_default(),
                        now
                    );
                    let _ = writeln!(
                        &mut out,
                        "{}_count{} {} {}",
                        family.name, labels, point.value, now
                    );
                } else {
                    let _ = writeln!(
                        &mut out,
                        "{}{} {} {}",
                        family.name, labels, point.value, now
                    );
                }
            }
        }
        out
    }

    /// Returns a snapshot of all recorded metric families.
    #[must_use]
    pub fn families(&self) -> Vec<MetricFamily> {
        let mut families = Vec::new();
        let metrics = self
            .metrics
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        for (name, family) in metrics.iter() {
            let name = name.clone();
            let mut points = Vec::new();
            for (labels, cell) in family {
                let labels = labels.clone();
                let metric_type = infer_type(&name);
                let value = match metric_type {
                    MetricType::Histogram => cell.hist_count.load(Ordering::Relaxed),
                    _ if name.contains("_gauge") || is_gauge_name(&name) => {
                        cell.gauge.load(Ordering::Relaxed)
                    }
                    _ => cell.counter.load(Ordering::Relaxed),
                };
                points.push(MetricPoint {
                    name: name.clone(),
                    value,
                    unit: "1".to_string(),
                    timestamp_ms: now_ms(),
                    labels,
                    sum: (metric_type == MetricType::Histogram)
                        .then(|| cell.hist_sum.load(Ordering::Relaxed)),
                    min: (metric_type == MetricType::Histogram)
                        .then(|| cell.hist_min.load(Ordering::Relaxed))
                        .filter(|v| *v != u64::MAX),
                    max: (metric_type == MetricType::Histogram)
                        .then(|| cell.hist_max.load(Ordering::Relaxed)),
                });
            }
            let family_metric_type = infer_type(&name);
            families.push(MetricFamily {
                name,
                help: String::new(),
                metric_type: family_metric_type,
                points,
            });
        }
        families
    }
}

impl Default for MetricsRecorder {
    fn default() -> Self {
        Self::new()
    }
}

fn infer_type(name: &str) -> MetricType {
    if name.contains("_latency_") || name.contains("_duration_") || name.contains("_bytes_") {
        MetricType::Histogram
    } else if name.contains("_gauge") || is_gauge_name(name) {
        MetricType::Gauge
    } else {
        MetricType::Counter
    }
}

fn is_gauge_name(name: &str) -> bool {
    matches!(
        name,
        "jasusi_queue_depth"
            | "jasusi_orphan_process_count"
            | "jasusi_active_jobs"
            | "jasusi_connected_clients"
    )
}

fn sanitize(value: &str) -> String {
    value
        .replace('\\', "\\\\")
        .replace('"', "\\\"")
        .replace('\n', "\\n")
}

fn render_labels(labels: &[(String, String)]) -> String {
    if labels.is_empty() {
        return String::new();
    }
    let values = labels
        .iter()
        .map(|(k, v)| format!("{}=\"{}\"", sanitize(k), sanitize(v)))
        .collect::<Vec<_>>()
        .join(",");
    format!("{{{values}}}")
}

fn add_label(labels: &[(String, String)], key: &str, value: &str) -> Vec<(String, String)> {
    let mut out = labels.to_vec();
    out.push((key.to_string(), value.to_string()));
    out
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn counter_and_gauge_are_tracked() {
        let m = MetricsRecorder::new();
        m.counter("jasusi_requests_total", &[("path", "/api/task")], 3);
        m.gauge("jasusi_queue_depth", &[], 7);
        let families = m.families();
        assert!(families
            .iter()
            .any(|f| f.name == "jasusi_requests_total" && f.points[0].value == 3));
        assert!(families
            .iter()
            .any(|f| f.name == "jasusi_queue_depth" && f.points[0].value == 7));
    }

    #[test]
    fn histogram_tracks_count() {
        let m = MetricsRecorder::new();
        m.histogram("jasusi_request_latency_ms", &[], 120);
        m.histogram("jasusi_request_latency_ms", &[], 80);
        let families = m.families();
        let h = families
            .iter()
            .find(|f| f.name == "jasusi_request_latency_ms")
            .unwrap();
        assert_eq!(h.metric_type, MetricType::Histogram);
        assert_eq!(h.points[0].value, 2);
    }

    #[test]
    fn prometheus_output_contains_escaped_labels() {
        let m = MetricsRecorder::new();
        m.counter("jasusi_requests_total", &[("path", "/api/task")], 1);
        let out = m.render_prometheus();
        assert!(out.contains("jasusi_requests_total"));
        assert!(out.contains("path=\"/api/task\""));
    }
}
