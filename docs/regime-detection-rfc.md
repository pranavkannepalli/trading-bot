# RFC: Regime Detection for Trading Bot (LLM Counsel)

**Status:** Proposed
**Date:** 2026-06-14
**Author:** Nerd (research sweep)
**Branch:** `regime-detection-rfc`

---

## 1. Executive Summary

Market regime detection classifies the current market environment into distinct behavioral states (e.g., trending bull, mean-reverting range, high-vol crisis) so the trading engine can adapt its strategy selection, position sizing, and risk constraints accordingly.

This RFC proposes a layered regime detection system for the `trading-bot` codebase that:
- **Tier 1 (Today):** Formalize and extend the existing `SimpleTrendVolRegimeDetector` with explicit calibration
- **Tier 2 (This sprint):** Add a statistical regime detector using Hidden Markov Models (HMM) via `hmmlearn` — industry-standard for regime inference from price/returns data
- **Tier 3 (Future):** Ensemble weighting: combine multiple detectors into a single regime signal

The system must operate within the existing **non-agentic core boundary** pattern: regime detection is a deterministic/statistical pipeline that feeds into strategy selection and risk caps — it never has access to live credentials or broker connections.

---

## 2. Assumptions

1. **Mock-safe by design.** All detectors must accept mock/CSV price data. No live feeds required.
2. **Deterministic for a given seed.** HMM fitting involves randomness; optionally seed the fit for reproducible backtests.
3. **Regime is a read-only signal.** Regime classification informs downstream components (strategy, counsel) but does not directly place orders.
4. **Options-aware but not options-dependent.** Regime detectors should accept volatility-type features (e.g., VIX proxy, ATR, realized vol) when available, but degrade gracefully to price-only features.
5. **No live credentials dependency.** All data ingestion uses mock generators or local CSV files.
6. **Backtest-compatible.** Every detector must be callable inside `BacktestRunner.run()` without network I/O.

---

## 3. Regime Taxonomy

The system classifies markets into **5 canonical regimes**:

| Regime | Label | Price Behavior | Vol Behavior | Typical Strategy |
|--------|-------|----------------|-------------|-----------------|
| `bull_trend` | Bull Trend | Persistent upward drift | Low to normal | Momentum / trend-following |
| `bear_trend` | Bear Trend | Persistent downward drift | Elevated to high | Short-biased / hedging |
| `range` | Range / Mean-Reverting | Oscillates within a band | Low | Mean-reversion / pairs |
| `volatile` | High Vol / Crisis | Large swings, no clear direction | High (VIX > 30 proxy) | Reduce exposure, tight stops |
| `neutral` | Undefined / Transition | Insufficient data or in-between | Normal | Default / do-nothing |

Why 5 instead of 3 (bull/bear/neutral)?
- The `volatile` regime is critical for options risk management — high vol changes greeks dramatically
- The `range` regime vs `bull_trend` distinction determines momentum vs mean-reversion strategy selection
- HMMs naturally produce 2-5 latent states; empirical work finds 3-4 meaningful states in equity markets

### Transition Rules

Regime changes should require **confirmation**: a regime signal must persist for N consecutive observations (default N=3) before the system adopts it. This prevents whipsaw from single-day spikes (e.g., VIX intraday spike to 30+ returning to 15 next day).

---

## 4. Architecture

```
┌─────────────────────────────────────────────────────┐
│                  RegimeDetector (ABC)                 │
│  detect(window_closes, features?) → Regime            │
└──────────────────────┬──────────────────────────────┘
                       │
       ┌───────────────┼───────────────┐
       ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────────┐
│ SimpleTrend  │ │  HMMRegime   │ │ EnsembleRegime   │
│ VolDetector  │ │  Detector    │ │  Detector        │
│ (exists)     │ │  (new)       │ │  (future)        │
│              │ │              │ │                  │
│ Inputs:      │ │ Inputs:      │ │ Inputs:          │
│ • window of  │ │ • returns    │ │ • all detectors  │
│   closes     │ │ • realized   │ │ • weights/config │
│ • slope      │ │   vol        │ │ • confidence     │
│ • vol proxy  │ │ • (VIX proxy)│ │   scores         │
│              │ │              │ │                  │
│ Heuristic    │ │ Statistical  │ │ Weighted vote    │
│ thresholds   │ │ (EM-fit HMM) │ │ or softmax       │
└──────┬───────┘ └──────┬───────┘ └────────┬─────────┘
       │                │                 │
       └────────────────┼─────────────────┘
                        ▼
              ┌──────────────────┐
              │  Strategy Router  │
              │  + Counsel adapts │
              └──────────────────┘
```

### Existing Code Compatibility

The current `RegimeDetector` ABC and `SimpleTrendVolRegimeDetector` stay as-is. New detectors implement the same interface:

```python
class RegimeDetector(ABC):
    def detect(self, window_closes: Sequence[float]) -> Regime: ...
```

For HMM and future detectors that need additional features (returns, vol), we extend with an optional `features` parameter:

```python
class HMMRegimeDetector(RegimeDetector):
    def __init__(self, model_path: Optional[str] = None, n_states: int = 3):
        ...
    def detect(self, window_closes: Sequence[float], 
               features: Optional[RegimeFeatures] = None) -> Regime: ...
    def fit(self, closes: Sequence[float]) -> None: ...  # offline training
```

---

## 5. Data Contracts

### 5.1 Regime (unchanged from current)

```python
@dataclass(frozen=True)
class Regime:
    name: str  # "bull_trend" | "bear_trend" | "range" | "volatile" | "neutral"
```

### 5.2 RegimeFeatures (new)

```python
@dataclass(frozen=True)
class RegimeFeatures:
    """Optional features for richer regime detection.
    All fields optional — detectors degrade gracefully when missing."""
    returns: Optional[list[float]] = None       # log returns over window
    realized_vol: Optional[float] = None         # annualized realized vol
    vix_proxy: Optional[float] = None            # VIX or ATM implied vol if available
    atr: Optional[float] = None                  # Average True Range
    volume: Optional[list[float]] = None         # Volume bars (normalized)
```

### 5.3 RegimeSignal (new — for ensemble/future use)

```python
@dataclass(frozen=True)
class RegimeSignal:
    regime: Regime
    confidence: float          # 0.0–1.0 from the detector
    detector_name: str         # "simple_trend_vol" | "hmm_gaussian" | "ensemble"
    timestamp: int             # time_index from env
```

### 5.4 BacktestStep Extension

Add `regime` field to `BacktestStep`:

```python
@dataclass
class BacktestStep:
    # ... existing fields ...
    regime: Optional[dict[str, Any]] = None  
    # {"name": "bull_trend", "confidence": 0.82, "detector": "hmm_gaussian"}
```

---

## 6. Detection Methods

### 6.1 Tier 1: SimpleTrendVolDetector (exists, formalize)

**Current state:** Uses slope of window closes + std dev of log returns. Outputs `bull`, `bear`, `range`, `volatile`.

**Proposed refinements:**
- Rename regime labels to canonical taxonomy (`bull` → `bull_trend`, etc.)
- Make thresholds configurable (currently hardcoded: vol≥0.10 → volatile, |slope|≤0.01 → range)
- Add `confidence` output based on how far the signal is from threshold boundaries
- Add confirmation window (require N=3 consecutive same-classifications)

**When to use:** Always available as fallback. Good enough for mock testing and initial POC. Zero dependencies beyond stdlib.

### 6.2 Tier 2: HMMRegimeDetector (new)

**Method:** Gaussian Hidden Markov Model on log returns + realized volatility.

**Key design decisions:**

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Library | `hmmlearn` (BSD license) | scikit-learn compatible, mature, industry standard |
| HMM variant | GaussianHMM | Returns are approximately normal in log-space; simpler than GMM-HMM |
| Number of states | 3 (default), 2–5 configurable | 3 states typically capture bull/ranging/bear; 5 matches taxonomy with vol states |
| Features | log returns + rolling realized vol (2-feature vector) | Single-feature (returns-only) loses vol regime info |
| Training | Offline batch fit on historical data; pickle model for reuse | Prevents per-step retraining; enables mock-safe backtesting |

**Fitting process (offline, mock-safe):**

```python
# offline_fit.py — run once, save model
from hmmlearn import hmm
import numpy as np
import pickle

# Load historical closes (from mock gen or CSV)
closes = generate_mock_closes(5000, seed=42)
log_returns = np.diff(np.log(closes))
realized_vol = pd.Series(log_returns).rolling(60).std() * np.sqrt(252)

# Stack features: (T, 2)
features = np.column_stack([log_returns[59:], realized_vol[59:]])  

model = hmm.GaussianHMM(n_components=3, covariance_type="full", random_state=42)
model.fit(features)

with open("models/hmm_regime_v1.pkl", "wb") as f:
    pickle.dump(model, f)
```

**Detection at runtime:**

```python
class HMMRegimeDetector(RegimeDetector):
    def detect(self, window_closes, features=None):
        # If no pre-fit model, fall back to simple detector
        if self.model is None:
            return self._fallback.detect(window_closes)
        
        # Build feature vector from window
        log_rets = np.diff(np.log(window_closes))
        rv = np.std(log_rets) * np.sqrt(252) if len(log_rets) > 1 else 0.0
        X = np.array([[np.mean(log_rets), rv]])
        
        state = self.model.predict(X)[0]
        confidence = np.max(self.model.predict_proba(X)[0])
        return self._map_state_to_regime(state, confidence)
```

**State-to-regime mapping:** HMM states are unlabeled. After fitting, inspect the mean returns per state and label manually:
- Highest mean return → `bull_trend`
- Lowest mean return → `bear_trend`
- Middle mean return + lowest vol → `range`
- Highest vol state → `volatile`

This mapping is stored as part of the serialized model artifact.

### 6.3 Tier 3: EnsembleRegimeDetector (future)

Weighted voting across multiple detectors. Each detector produces a `RegimeSignal` with confidence; the ensemble combines via:
- **Soft voting:** Weighted average of one-hot encoded regime × confidence
- **Hard voting:** Majority with confidence tiebreaker

```python
class EnsembleRegimeDetector(RegimeDetector):
    def __init__(self, detectors: list[RegimeDetector], weights: list[float]):
        ...
```

This is out-of-scope for initial implementation but the contract supports it.

---

## 7. Integration Points

### 7.1 Strategy Router

The regime signal determines which strategy variant to use:

```python
class RegimeAdaptiveStrategy(Strategy):
    def __init__(self, strategies: dict[str, Strategy], detector: RegimeDetector):
        self._strategies = strategies  
        # e.g. {"bull_trend": MomentumStrategy(), "range": MeanReversionStrategy(), ...}
        self._detector = detector
        self._current_regime = Regime("neutral")
    
    def propose_orders(self, observation):
        closes = observation.staggered_closes
        self._current_regime = self._detector.detect(closes)
        active = self._strategies.get(self._current_regime.name, self._strategies["neutral"])
        return active.propose_orders(observation)
```

### 7.2 LLM Counsel Adaptation

Regime feeds into counsel risk caps. Example: in `volatile` regime, counsel tightens `max_abs_total_delta` by 50%:

```python
class RegimeAwareCounsel(LLMCounsel):
    def advise(self, proposed_orders, market_state):
        regime = self._detector.detect(market_state.get("closes", []))
        base_advice = self._base_counsel.advise(proposed_orders, market_state)
        
        if regime.name == "volatile":
            # Halve position caps in high vol
            return Advice(
                max_order_quantity=(base_advice.max_order_quantity or 100) * 0.5,
                max_abs_total_delta=(base_advice.max_abs_total_delta or 10.0) * 0.5,
                max_abs_total_vega=(base_advice.max_abs_total_vega or 5.0) * 0.5,
            )
        return base_advice
```

### 7.3 BacktestRunner Integration

`BacktestRunner.run()` already accepts `Strategy`, `counsel`, and `config`. Adding regime detection requires no API changes:

```python
# Example: regime-adaptive backtest
detector = HMMRegimeDetector.from_pickle("models/hmm_regime_v1.pkl")
strategy = RegimeAdaptiveStrategy(
    strategies={"bull_trend": MomentumStrategy(), "range": MeanReversionStrategy(), ...},
    detector=detector,
)
counsel = RegimeAwareCounsel(base_counsel=StrictDeltaCounsel(max_delta=2.0), detector=detector)
runner = BacktestRunner(counsel=counsel)
result, steps = runner.run(strategy=strategy, closes=closes, record_steps=True)
```

---

## 8. Backtesting & Validation

### 8.1 Regime Detection Accuracy Metrics

| Metric | How to Measure | Target |
|--------|---------------|--------|
| **Transition lag** | Steps between VIX spike (ground truth) and regime flip | ≤ 3 steps for major transitions |
| **False positive rate** | Regime flips that revert within 5 steps | < 5% of total steps |
| **Regime persistence** | Average consecutive steps in same regime | bull/range: 20+; volatile: 5–15 |
| **Backtest PnL improvement** | Compare regime-adaptive vs always-buy baseline | Positive delta in Sharpe and max drawdown |

### 8.2 Mock Validation Suite

A dedicated test generates price series with known regime characteristics:

```python
def generate_regime_labeled_closes(n: int, regime_sequence: list[tuple[str, int]]) -> tuple[list[float], list[str]]:
    """
    Generate closes with labeled regimes.
    regime_sequence: [("bull_trend", 100), ("range", 50), ("volatile", 30), ...]
    Returns: (closes, ground_truth_labels)
    """
```

Tests then assert:
- Detector correctly identifies the dominant regime in each segment
- Transitions are detected within acceptable lag
- Confidence scores are higher in clear regimes, lower near transitions

### 8.3 Walk-Forward Validation

For HMM: train on period T₁, test on T₂ (non-overlapping). Measure regime classification stability.

---

## 9. Implementation Roadmap

### Phase 1: Formalize Existing (1–2 PRs)
- [ ] Rename regime labels to canonical taxonomy
- [ ] Extract thresholds into configurable `RegimeDetectorConfig`
- [ ] Add `detect_with_confidence()` returning `RegimeSignal`
- [ ] Add confirmation window logic (N=3 persistence)
- [ ] Add `regime` field to `BacktestStep` artifacts
- [ ] Unit tests for each regime type with known price patterns

### Phase 2: HMM Detector (2–3 PRs)
- [ ] Add `hmmlearn` dependency
- [ ] Implement `HMMRegimeDetector` with offline `fit()` and online `detect()`
- [ ] Model serialization (pickle → `models/` directory, gitignored)
- [ ] Mock data generator for regime-labeled price series
- [ ] HMM-specific unit tests (fit convergence, state mapping, confidence bounds)
- [ ] Backtest integration test: regime-adaptive strategy vs baseline

### Phase 3: Strategy-Counsel-Regime Wiring (2 PRs)
- [ ] Implement `RegimeAdaptiveStrategy` router
- [ ] Implement `RegimeAwareCounsel` risk-cap adapter
- [ ] End-to-end demo: `--case regime_adaptive` option in demo runner
- [ ] Artifact output includes regime trace alongside orders/fills

### Phase 4: Ensemble (Future / Optional)
- [ ] `EnsembleRegimeDetector` with weighted voting
- [ ] Regime performance analytics: PnL breakdown per regime
- [ ] Auto-calibration of HMM state count via BIC/AIC

---

## 10. Key References

| Source | Relevance |
|--------|-----------|
| [VolatilityBox: Regime Detection Guide (2026)](https://volatilitybox.com/research/volatility-regime-detection/) | VIX thresholds, 200-MA crossover, term structure, multi-signal synthesis |
| [QuantStart: HMM Regime Detection in QSTrader](https://www.quantstart.com/articles/market-regime-detection-using-hidden-markov-models-in-qstrader/) | Canonical hmmlearn implementation for S&P500 returns |
| [QuantInsti: Regime-Adaptive Trading with HMM + Random Forest (2025)](https://blog.quantinsti.com/regime-adaptive-trading-python/) | Walk-forward backtesting, HMM→RF signal pipeline |
| [HMM + RL for Portfolio Mgmt (CloudConf 2025)](https://www.cloud-conf.net/datasec/2025/proceedings/pdfs/IDS2025-3SVVEmiJ6JbFRviTl4Otnv/966100a067/966100a067.pdf) | Evidence: HMM-aware RL improves Sharpe and controls drawdowns |
| [AIMS Press: Ensemble-HMM Voting (2025)](https://www.aimspress.com/article/doi/10.3934/DSFE.2025019) | Bagging/boosting + HMM for regime shift detection |
| [taylorjmellon/market-regime-detection (GitHub)](https://github.com/taylorjmellon/market-regime-detection) | Reference implementation: K-Means + HMM + backtesting pipeline |
| [LSEG: Statistical & ML Regime Detection](https://developers.lseg.com/en/article-catalog/article/market-regime-detection) | Survey of methods: statistical vs ML approaches |
| [Price Action Lab: Momentum vs Mean-Reversion Regime Switching (2024)](https://www.priceactionlab.com/Blog/2024/01/mean-reversion-and-momentum-regime-switching/) | Empirical: momentum wins in bull trends; mean-reversion in bear/ranging |

---

## 11. Risks & Open Questions

1. **HMM state interpretability:** Unsupervised states aren't guaranteed to map cleanly to human labels. Mitigation: inspect means post-fit; fall back to simple detector if states are degenerate.
2. **Look-ahead bias:** `fit()` must only use data available up to the training cutoff. Walk-forward backtests enforce this.
3. **hmmlearn dependency weight:** Adds numpy/scipy transitive deps. Already reasonable for a quant Python project.
4. **VIX proxy fidelity:** Without live VIX data, ATR and realized vol are imperfect substitutes. Acceptable for mock/backtest use.
5. **Confirmation lag vs responsiveness:** N=3 confirmation means 3-step delay on real regime changes. A parameter to tune; defaults can be overridden.

---

## Appendix A: Concrete Example — HMM Detection Output

```json
{
  "time_index": 142,
  "close": 107.32,
  "regime": {
    "name": "volatile",
    "confidence": 0.87,
    "detector": "hmm_gaussian",
    "features": {
      "mean_log_return": -0.0031,
      "realized_vol_annualized": 0.34
    }
  },
  "proposed_orders": [
    {"symbol": "SPY", "side": "buy", "quantity": 5, "limit_price": null}
  ],
  "orders_executed": [
    {"symbol": "SPY", "side": "buy", "quantity": 2.5, "limit_price": null}
  ],
  "advice": {
    "max_order_quantity": 5.0,
    "max_abs_total_delta": 1.0,
    "max_abs_total_vega": 2.5,
    "regime_scalar": 0.5
  }
}
```

## Appendix B: File Layout

```
trading_bot/
├── regime_detection.py       # Existing SimpleTrendVolDetector + new HMMRegimeDetector
├── types.py                  # + RegimeFeatures, RegimeSignal
├── strategies.py             # + RegimeAdaptiveStrategy
├── llm_counsel.py            # + RegimeAwareCounsel
├── backtest.py               # + regime field in BacktestStep
├── models/                   # gitignored — serialized HMM models
│   └── .gitkeep
├── tests/
│   ├── test_regime_detection.py        # New
│   ├── test_regime_adaptive_strategy.py # New
│   └── test_regime_backtest.py         # New
└── docs/
    └── regime-detection-rfc.md         # This file
```