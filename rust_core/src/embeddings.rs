//! Pure-Rust deterministic static token-embedding and 1-bit Hamming quantization.
//!
//! Designed to accept `model2vec-rs`-style static token weight tables with zero
//! Python or network dependencies, plus deterministic hashed fallback vectors
//! for out-of-vocabulary tokens and offline unit testing.

use std::collections::HashMap;

/// Static token-embedding table with optional deterministic hashed OOV fallback.
#[derive(Debug, Clone)]
pub struct StaticTokenEmbeddings {
    dim: usize,
    weights: HashMap<String, Vec<f32>>,
}

impl StaticTokenEmbeddings {
    /// Create a static embedding table with no preloaded weights (deterministic hashed tokens).
    pub fn deterministic_default(dim: usize) -> Self {
        assert!(dim > 0, "embedding dimension must be positive");
        Self {
            dim,
            weights: HashMap::new(),
        }
    }

    /// Construct from preloaded `model2vec-rs`-style static token weight vectors.
    pub fn from_token_weights(
        dim: usize,
        weights: HashMap<String, Vec<f32>>,
    ) -> Result<Self, String> {
        if dim == 0 {
            return Err("embedding dimension must be positive".to_string());
        }
        for (token, vec) in &weights {
            if vec.len() != dim {
                return Err(format!(
                    "token '{token}' weight length {} does not match embedding dim {dim}",
                    vec.len()
                ));
            }
            if vec.iter().any(|w| !w.is_finite()) {
                return Err(format!("token '{token}' weight contains non-finite value"));
            }
        }
        Ok(Self { dim, weights })
    }

    pub fn dim(&self) -> usize {
        self.dim
    }

    /// Embed a text string by mean-pooling L2-normalized token vectors.
    pub fn embed_text(&self, text: &str) -> Vec<f32> {
        let mut acc = vec![0.0_f32; self.dim];
        let mut count = 0usize;
        for token in tokenize(text) {
            if let Some(vec) = self.weights.get(&token) {
                add_normalized(&mut acc, vec);
            } else {
                let hashed = deterministic_token_vector(&token, self.dim);
                add_normalized(&mut acc, &hashed);
            }
            count += 1;
        }
        if count > 0 {
            l2_normalize_in_place(&mut acc);
        }
        acc
    }

    /// Embed and quantize a text string into a 1-bit Hamming signature.
    pub fn embed_quantized(&self, text: &str) -> BinaryQuantizedEmbedding {
        BinaryQuantizedEmbedding::quantize(&self.embed_text(text))
    }
}

/// 1-bit sign-quantized embedding packed into 64-bit words for hardware `popcnt` Hamming distance.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BinaryQuantizedEmbedding {
    num_bits: usize,
    words: Vec<u64>,
}

impl BinaryQuantizedEmbedding {
    /// Quantize each dimension `v` into `1` if finite and `v >= 0.0` else `0`, packed LSB-first per `u64` word.
    pub fn quantize(embedding: &[f32]) -> Self {
        let num_bits = embedding.len();
        let num_words = num_bits.div_ceil(64);
        let mut words = vec![0_u64; num_words];
        for (idx, &val) in embedding.iter().enumerate() {
            if val.is_finite() && val >= 0.0 {
                words[idx / 64] |= 1_u64 << (idx % 64);
            }
        }
        Self { num_bits, words }
    }

    pub fn num_bits(&self) -> usize {
        self.num_bits
    }

    pub fn words(&self) -> &[u64] {
        &self.words
    }

    /// Compute exact Hamming distance between two same-dimension quantized embeddings.
    pub fn hamming_distance(&self, other: &Self) -> Result<u32, String> {
        if self.num_bits != other.num_bits {
            return Err(format!(
                "Hamming dimension mismatch: {} vs {}",
                self.num_bits, other.num_bits
            ));
        }
        Ok(self
            .words
            .iter()
            .zip(other.words.iter())
            .fold(0_u32, |acc, (a, b)| {
                acc.saturating_add((a ^ b).count_ones())
            }))
    }

    /// Normalized Hamming similarity in `[0.0, 1.0]`.
    pub fn hamming_similarity(&self, other: &Self) -> Result<f32, String> {
        let dist = self.hamming_distance(other)?;
        if self.num_bits == 0 {
            return Ok(1.0);
        }
        Ok(1.0 - (dist as f32 / self.num_bits as f32))
    }
}

/// Cosine similarity between two dense embedding vectors.
pub fn cosine_similarity(a: &[f32], b: &[f32]) -> Result<f32, String> {
    if a.len() != b.len() {
        return Err(format!(
            "cosine dimension mismatch: {} vs {}",
            a.len(),
            b.len()
        ));
    }
    let mut dot = 0.0_f64;
    let mut norm_a = 0.0_f64;
    let mut norm_b = 0.0_f64;
    for (&x, &y) in a.iter().zip(b.iter()) {
        if !x.is_finite() || !y.is_finite() {
            return Ok(0.0);
        }
        let xf = f64::from(x);
        let yf = f64::from(y);
        dot += xf * yf;
        norm_a += xf * xf;
        norm_b += yf * yf;
    }
    let denom = norm_a.sqrt() * norm_b.sqrt();
    if !dot.is_finite() || !denom.is_finite() || denom == 0.0 {
        return Ok(0.0);
    }
    let sim = (dot / denom) as f32;
    if sim.is_finite() {
        Ok(sim)
    } else {
        Ok(0.0)
    }
}

fn tokenize(text: &str) -> Vec<String> {
    text.split(|c: char| !c.is_alphanumeric() && c != '_')
        .filter(|tok| !tok.is_empty())
        .map(|tok| tok.to_ascii_lowercase())
        .collect()
}

fn deterministic_token_vector(token: &str, dim: usize) -> Vec<f32> {
    let mut hash: u64 = 0xcbf29ce484222325;
    for &b in token.as_bytes() {
        hash ^= u64::from(b);
        hash = hash.wrapping_mul(0x100000001b3);
    }
    let mut out = Vec::with_capacity(dim);
    let mut state = hash;
    for _ in 0..dim {
        // SplitMix64 step for deterministic unit-range components.
        state = state.wrapping_add(0x9e3779b97f4a7c15);
        let mut z = state;
        z = (z ^ (z >> 30)).wrapping_mul(0xbf58476d1ce4e5b9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94d049bb133111eb);
        z ^= z >> 31;
        let signed = ((z >> 40) as f32 / ((1u32 << 24) as f32)) * 2.0 - 1.0;
        out.push(signed);
    }
    l2_normalize_in_place(&mut out);
    out
}

fn add_normalized(acc: &mut [f32], vec: &[f32]) {
    if vec.iter().any(|v| !v.is_finite()) {
        return;
    }
    let norm = vec
        .iter()
        .map(|&v| {
            let vf = f64::from(v);
            vf * vf
        })
        .sum::<f64>()
        .sqrt();
    if !norm.is_finite() || norm == 0.0 {
        return;
    }
    for (dst, &src) in acc.iter_mut().zip(vec.iter()) {
        let scaled = (f64::from(src) / norm) as f32;
        if scaled.is_finite() {
            *dst += scaled;
        }
    }
}

fn l2_normalize_in_place(vec: &mut [f32]) {
    if vec.iter().any(|v| !v.is_finite()) {
        for v in vec.iter_mut() {
            *v = 0.0;
        }
        return;
    }
    let norm = vec
        .iter()
        .map(|&v| {
            let vf = f64::from(v);
            vf * vf
        })
        .sum::<f64>()
        .sqrt();
    if norm.is_finite() && norm > 0.0 {
        for v in vec.iter_mut() {
            let normalized = (f64::from(*v) / norm) as f32;
            *v = if normalized.is_finite() {
                normalized
            } else {
                0.0
            };
        }
    } else {
        for v in vec.iter_mut() {
            *v = 0.0;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn binary_quantization_exact_hamming_distance_and_similarity() {
        let pos = vec![0.5, -0.2, 0.0, -1.0, 0.8, 0.1, -0.3, 0.9];
        let neg = pos.iter().map(|v| -v - 0.01).collect::<Vec<_>>();
        let q_pos = BinaryQuantizedEmbedding::quantize(&pos);
        let q_neg = BinaryQuantizedEmbedding::quantize(&neg);

        assert_eq!(q_pos.num_bits(), 8);
        assert_eq!(q_pos.words().len(), 1);
        assert_eq!(q_pos.hamming_distance(&q_pos).unwrap(), 0);
        assert_eq!(q_pos.hamming_similarity(&q_pos).unwrap(), 1.0);
        assert_eq!(q_pos.hamming_distance(&q_neg).unwrap(), 8);
        assert_eq!(q_pos.hamming_similarity(&q_neg).unwrap(), 0.0);
    }

    #[test]
    fn cosine_and_hamming_scoring_rank_related_texts_higher() {
        let embedder = StaticTokenEmbeddings::deterministic_default(256);
        let anchor = embedder.embed_text("fn parse_ast_node token stream");
        let similar = embedder.embed_text("fn parse_ast_node syntax stream");
        let unrelated = embedder.embed_text("cuda kernel memory bandwidth");

        let cos_sim = cosine_similarity(&anchor, &similar).unwrap();
        let cos_unrel = cosine_similarity(&anchor, &unrelated).unwrap();
        assert!(
            cos_sim > cos_unrel,
            "expected similar cosine ({cos_sim}) > unrelated ({cos_unrel})"
        );

        let q_anchor = BinaryQuantizedEmbedding::quantize(&anchor);
        let q_similar = BinaryQuantizedEmbedding::quantize(&similar);
        let q_unrelated = BinaryQuantizedEmbedding::quantize(&unrelated);

        let ham_sim = q_anchor.hamming_similarity(&q_similar).unwrap();
        let ham_unrel = q_anchor.hamming_similarity(&q_unrelated).unwrap();
        assert!(
            ham_sim > ham_unrel,
            "expected similar hamming ({ham_sim}) > unrelated ({ham_unrel})"
        );
    }

    #[test]
    fn from_token_weights_loads_static_table_and_rejects_dim_mismatch() {
        let mut weights = HashMap::new();
        weights.insert("alpha".to_string(), vec![1.0, 0.0, 0.0, 0.0]);
        weights.insert("beta".to_string(), vec![0.0, 1.0, 0.0, 0.0]);

        let embedder = StaticTokenEmbeddings::from_token_weights(4, weights).unwrap();
        let vec = embedder.embed_text("alpha beta");
        assert_eq!(vec.len(), 4);
        assert!((vec[0] - vec[1]).abs() < 1e-6);

        let mut bad_weights = HashMap::new();
        bad_weights.insert("bad".to_string(), vec![1.0, 2.0]);
        assert!(StaticTokenEmbeddings::from_token_weights(4, bad_weights).is_err());

        for non_finite in [f32::NAN, f32::INFINITY, f32::NEG_INFINITY] {
            let mut non_finite_weights = HashMap::new();
            non_finite_weights.insert("bad".to_string(), vec![1.0, non_finite, 0.0, 0.0]);
            assert!(StaticTokenEmbeddings::from_token_weights(4, non_finite_weights).is_err());
        }
    }

    #[test]
    fn non_finite_inputs_do_not_propagate_in_cosine_quantize_or_normalize() {
        let nan_vec = vec![1.0, f32::NAN, 0.5, -0.5];
        let inf_vec = vec![1.0, f32::INFINITY, 0.5, f32::NEG_INFINITY];
        let clean = vec![1.0, 0.0, 0.5, -0.5];

        assert_eq!(cosine_similarity(&nan_vec, &clean).unwrap(), 0.0);
        assert_eq!(cosine_similarity(&inf_vec, &clean).unwrap(), 0.0);

        let q_inf = BinaryQuantizedEmbedding::quantize(&inf_vec);
        assert_eq!(q_inf.words()[0], 0b0101_u64);
        let q_nan = BinaryQuantizedEmbedding::quantize(&nan_vec);
        assert_eq!(q_nan.words()[0], 0b0101_u64);

        let mut acc = vec![1.0_f32, 2.0, 3.0, 4.0];
        add_normalized(&mut acc, &nan_vec);
        add_normalized(&mut acc, &inf_vec);
        assert_eq!(acc, vec![1.0, 2.0, 3.0, 4.0]);

        let mut to_norm = vec![1.0_f32, f32::INFINITY, 0.0, 0.0];
        l2_normalize_in_place(&mut to_norm);
        assert_eq!(to_norm, vec![0.0, 0.0, 0.0, 0.0]);
    }
}
