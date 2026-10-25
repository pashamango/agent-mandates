//! Wolfram Offline Model Router (§5.1, §5.2)
//!
//! Autonomous offline local inference for task routing on Wolfram.
//! - Hardware Budget: 16 GB RAM, >= 20 GB free disk space.
//! - Model ID: mindrock-router-linear-policy-v1
//! - Runtime: wolfram_offline_policy_engine
//! - Architecture: linear_softmax_policy
//! - Quantization: none_direct_f64 (calibrated continuous projection)
//! - License: Apache-2.0
//! - Model Size: Verified dynamically against data/models/mindrock_router_v1.weights (< 2 KB RAM footprint, well within 16 GB budget)
//! - Decision Latency Budget: <= 3.0 seconds (typical execution < 1 ms)
//! - Offline Guarantee: Operates 100% offline without external network or paid API.
//! - Fallback Guarantee: Fail-closed fallback to deterministic rules (§3-§4) on unavailable/timeout.

use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct ModelMetadata {
    pub model_id: String,
    pub runtime: String,
    pub architecture: String,
    pub quantization: String,
    pub license: String,
    pub size_bytes: u64,
    pub latency_budget_secs: f64,
}

impl Default for ModelMetadata {
    fn default() -> Self {
        Self {
            model_id: "mindrock-router-linear-policy-v1".to_string(),
            runtime: "wolfram_offline_policy_engine".to_string(),
            architecture: "linear_softmax_policy".to_string(),
            quantization: "none_direct_f64".to_string(),
            license: "Apache-2.0".to_string(),
            size_bytes: 1100,
            latency_budget_secs: 3.0,
        }
    }
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct ModelInferenceDecision {
    pub recommended_slot: String,
    pub confidence: f64,
    pub latency_ms: u64,
    pub model_id: String,
    pub features: Vec<f64>,
}

#[derive(Debug, Clone)]
pub struct LocalModelEngine {
    pub metadata: ModelMetadata,
    pub weights_path: PathBuf,
    // Linear projection weights: 2 outputs (0: agy, 1: codex-local) x 10 input features
    weights: [[f64; 10]; 2],
    biases: [f64; 2],
    pub is_loaded: bool,
    pub inference_delay: Duration,
}

impl LocalModelEngine {
    pub fn new<P: AsRef<Path>>(weights_path: P) -> Self {
        let p = weights_path.as_ref().to_path_buf();
        match Self::load_from_file(&p) {
            Ok(engine) => engine,
            Err(_) => Self {
                metadata: ModelMetadata::default(),
                weights_path: p,
                weights: [[0.0; 10]; 2],
                biases: [0.0; 2],
                is_loaded: false,
                inference_delay: Duration::ZERO,
            },
        }
    }

    /// Load and rigorously validate model weights from disk (§5.1, §5.2)
    pub fn load_from_file<P: AsRef<Path>>(weights_path: P) -> Result<Self, String> {
        let p = weights_path.as_ref();
        if !p.exists() {
            return Err(format!(
                "MODEL_ROUTER_UNAVAILABLE: weights file not found: {:?}",
                p
            ));
        }

        let bytes = std::fs::read(p).map_err(|e| {
            format!("MODEL_ROUTER_UNAVAILABLE: failed to read weights file: {}", e)
        })?;

        let val: serde_json::Value = serde_json::from_slice(&bytes).map_err(|e| {
            format!("MODEL_ROUTER_UNAVAILABLE: corrupt JSON in weights file: {}", e)
        })?;

        let model_id = val.get("model_id").and_then(|v| v.as_str()).unwrap_or("");
        if model_id != "mindrock-router-linear-policy-v1" {
            return Err(format!(
                "MODEL_ROUTER_UNAVAILABLE: unsupported model_id '{}', expected 'mindrock-router-linear-policy-v1'",
                model_id
            ));
        }

        let declared_size = val.get("size_bytes").and_then(|v| v.as_u64()).unwrap_or(0);
        if declared_size != bytes.len() as u64 {
            return Err(format!(
                "MODEL_ROUTER_UNAVAILABLE: weights file size mismatch (actual {} bytes, declared {} bytes)",
                bytes.len(),
                declared_size
            ));
        }

        let runtime = val.get("runtime").and_then(|v| v.as_str()).unwrap_or("wolfram_offline_policy_engine");
        let architecture = val.get("architecture").and_then(|v| v.as_str()).unwrap_or("linear_softmax_policy");
        let quantization = val.get("quantization").and_then(|v| v.as_str()).unwrap_or("none_direct_f64");
        let license = val.get("license").and_then(|v| v.as_str()).unwrap_or("Apache-2.0");

        // Validate tensor shapes
        let weights_shape = val.pointer("/tensor_shapes/weights")
            .and_then(|v| v.as_array())
            .map(|arr| arr.iter().filter_map(|x| x.as_u64()).collect::<Vec<_>>())
            .unwrap_or_default();
        if weights_shape != vec![2, 10] {
            return Err(format!(
                "MODEL_ROUTER_UNAVAILABLE: invalid weights tensor shape {:?}",
                weights_shape
            ));
        }

        let biases_shape = val.pointer("/tensor_shapes/biases")
            .and_then(|v| v.as_array())
            .map(|arr| arr.iter().filter_map(|x| x.as_u64()).collect::<Vec<_>>())
            .unwrap_or_default();
        if biases_shape != vec![2] {
            return Err(format!(
                "MODEL_ROUTER_UNAVAILABLE: invalid biases tensor shape {:?}",
                biases_shape
            ));
        }

        let weights_json = val.get("calibrated_weights").and_then(|v| v.as_array()).ok_or_else(|| {
            "MODEL_ROUTER_UNAVAILABLE: missing calibrated_weights tensor".to_string()
        })?;
        if weights_json.len() != 2 {
            return Err("MODEL_ROUTER_UNAVAILABLE: calibrated_weights must have 2 rows".to_string());
        }

        let mut weights = [[0.0f64; 10]; 2];
        for i in 0..2 {
            let row = weights_json[i].as_array().ok_or_else(|| {
                "MODEL_ROUTER_UNAVAILABLE: calibrated_weights row not array".to_string()
            })?;
            if row.len() != 10 {
                return Err(format!(
                    "MODEL_ROUTER_UNAVAILABLE: calibrated_weights row {} length is {}, expected 10",
                    i, row.len()
                ));
            }
            for j in 0..10 {
                weights[i][j] = row[j].as_f64().ok_or_else(|| {
                    format!("MODEL_ROUTER_UNAVAILABLE: non-numeric weight at [{}, {}]", i, j)
                })?;
            }
        }

        let biases_json = val.get("calibrated_biases").and_then(|v| v.as_array()).ok_or_else(|| {
            "MODEL_ROUTER_UNAVAILABLE: missing calibrated_biases tensor".to_string()
        })?;
        if biases_json.len() != 2 {
            return Err("MODEL_ROUTER_UNAVAILABLE: calibrated_biases must have 2 elements".to_string());
        }
        let mut biases = [0.0f64; 2];
        for i in 0..2 {
            biases[i] = biases_json[i].as_f64().ok_or_else(|| {
                format!("MODEL_ROUTER_UNAVAILABLE: non-numeric bias at [{}]", i)
            })?;
        }

        let latency_budget = val.pointer("/hardware_budget/latency_budget_secs")
            .and_then(|v| v.as_f64())
            .unwrap_or(3.0);

        Ok(Self {
            metadata: ModelMetadata {
                model_id: model_id.to_string(),
                runtime: runtime.to_string(),
                architecture: architecture.to_string(),
                quantization: quantization.to_string(),
                license: license.to_string(),
                size_bytes: declared_size,
                latency_budget_secs: latency_budget,
            },
            weights_path: p.to_path_buf(),
            weights,
            biases,
            is_loaded: true,
            inference_delay: Duration::ZERO,
        })
    }

    /// Extract normalized continuous feature vector from task and environment
    pub fn extract_features(
        &self,
        tier: &str,
        status: &str,
        wait_time_secs: u64,
        agy_quota: &str,
        codex_quota: &str,
        cloud_qa_risk: bool,
        chatgpt_pool: u64,
    ) -> Vec<f64> {
        let wait_norm = (wait_time_secs as f64) / 7200.0;
        let tier_vs = if tier == "ВС" { 1.0 } else { 0.0 };
        let tier_vp = if tier == "ВП" { 1.0 } else { 0.0 };
        let tier_mc = if tier == "МС" { 1.0 } else { 0.0 };
        let tier_mp = if tier == "МП" { 1.0 } else { 0.0 };

        let agy_ok = if agy_quota == "OK" { 1.0 } else { 0.0 };
        let codex_ok = if codex_quota == "OK" { 1.0 } else { 0.0 };
        let qa_risk = if cloud_qa_risk { 1.0 } else { 0.0 };
        let pool_norm = (chatgpt_pool as f64) / 100.0;
        let is_todo = if status.eq_ignore_ascii_case("Todo") { 1.0 } else { 0.0 };

        vec![
            wait_norm,
            tier_vs,
            tier_vp,
            tier_mc,
            tier_mp,
            agy_ok,
            codex_ok,
            qa_risk,
            pool_norm,
            is_todo,
        ]
    }

    /// Perform offline forward pass with strict execution timeout enforcement (§5.2)
    /// Validates model weights file at runtime; absence or corruption triggers MODEL_ROUTER_UNAVAILABLE.
    pub fn infer(
        &self,
        tier: &str,
        status: &str,
        wait_time_secs: u64,
        agy_quota: &str,
        codex_quota: &str,
        cloud_qa_risk: bool,
        chatgpt_pool: u64,
    ) -> Result<ModelInferenceDecision, String> {
        // Enforce physical file presence and validation at inference time
        if !self.weights_path.exists() {
            return Err(format!(
                "MODEL_ROUTER_UNAVAILABLE: model weights file not found: {:?}",
                self.weights_path
            ));
        }

        // Validate file integrity on disk
        let current_engine = match Self::load_from_file(&self.weights_path) {
            Ok(eng) => eng,
            Err(e) => return Err(e),
        };

        let start = Instant::now();
        let features = self.extract_features(
            tier,
            status,
            wait_time_secs,
            agy_quota,
            codex_quota,
            cloud_qa_risk,
            chatgpt_pool,
        );

        let budget_secs = self.metadata.latency_budget_secs;
        let delay = self.inference_delay;
        let weights = current_engine.weights;
        let biases = current_engine.biases;
        let feat = features.clone();

        let (tx, rx) = std::sync::mpsc::channel();
        let handle = std::thread::spawn(move || {
            if delay > Duration::ZERO {
                std::thread::sleep(delay);
            }
            // Compute logits: y_i = \sum_j W_{ij} x_j + b_i
            let mut logits = [biases[0], biases[1]];
            for i in 0..2 {
                for j in 0..10 {
                    logits[i] += weights[i][j] * feat[j];
                }
            }

            // Softmax
            let max_logit = logits[0].max(logits[1]);
            let exp0 = (logits[0] - max_logit).exp();
            let exp1 = (logits[1] - max_logit).exp();
            let sum_exp = exp0 + exp1;
            let prob_agy = exp0 / sum_exp;
            let prob_codex = exp1 / sum_exp;
            let _ = tx.send((prob_agy, prob_codex));
        });

        let timeout_dur = Duration::from_secs_f64(budget_secs);
        let (prob_agy, prob_codex) = match rx.recv_timeout(timeout_dur) {
            Ok(res) => {
                let _ = handle.join();
                res
            }
            Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {
                return Err(format!(
                    "MODEL_INFERENCE_TIMEOUT: Offline model runtime exceeded budget of {:.6}s (RecvTimeoutError::Timeout)",
                    budget_secs
                ));
            }
            Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => {
                return Err("MODEL_ROUTER_UNAVAILABLE: inference worker thread disconnected unexpectedly".to_string());
            }
        };

        let elapsed = start.elapsed();
        let (recommended_slot, confidence) = if prob_codex > prob_agy {
            ("codex-local".to_string(), prob_codex)
        } else {
            ("agy".to_string(), prob_agy)
        };

        Ok(ModelInferenceDecision {
            recommended_slot,
            confidence,
            latency_ms: elapsed.as_millis() as u64,
            model_id: current_engine.metadata.model_id.clone(),
            features,
        })
    }
}
